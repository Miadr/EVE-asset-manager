import os
import sys
import shutil
import sqlite3
import subprocess
import threading
import secrets
import requests
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse, parse_qs


import scripts.assets_manager_ultimate as am
import scripts.universe_sync_smart as uss

from fastapi import FastAPI, HTTPException, Depends, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from logger_config import setup_logger

# === 初始化日志 (修改) ===
# 原: logger = setup_logger("AssetWeb", "asset_web.log")
logger = setup_logger("AssetWeb", "web_access.log")

# ================= 配置 (本地便携版) =================
if getattr(sys, 'frozen', False):
    BASE_PATH = sys._MEIPASS
else:
    BASE_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

DATA_DIR = os.path.join(os.getcwd(), 'data')
os.makedirs(DATA_DIR, exist_ok=True)

# ================= 数据存放 =================
def get_db_paths(server: str, lang: str = "en"):
    db_name = f"user_data_{server}.sqlite"
    if server == 'tranquility' and lang == 'zh':
        sde_name = "eve_universe_serenity.sqlite"
    else:
        sde_name = f"eve_universe_{server}.sqlite"
    return os.path.join(DATA_DIR, db_name), os.path.join(DATA_DIR, sde_name)

# ===== 启动自检及释出逻辑 =====
for init_server in ['serenity', 'tranquility', 'infinity']:
    init_db, init_sde = get_db_paths(init_server)
    
    if getattr(sys, 'frozen', False):
        bundled_sde = os.path.join(BASE_PATH, 'data', f'eve_universe_{init_server}.sqlite')
        if not os.path.exists(init_sde) and os.path.exists(bundled_sde):
            try:
                logger.info(f"释放默认内置物品数据库到 {init_sde} ...")
                shutil.copy2(bundled_sde, init_sde)
            except Exception as e:
                logger.error(f"[{init_server}] 默认物品数据库释放复制失败: {e}")

    if not os.path.exists(init_sde):
        try:
            logger.info(f"[{init_server}] 未找到宇宙数据库，正在创建空白 SDE 于 {init_sde}（将由 ESI 同步填充）")
            sde_conn = sqlite3.connect(init_sde)
            sde_conn.execute("CREATE TABLE IF NOT EXISTS invCategories (categoryID INTEGER PRIMARY KEY, categoryName TEXT)")
            sde_conn.execute("CREATE TABLE IF NOT EXISTS invGroups (groupID INTEGER PRIMARY KEY, categoryID INTEGER, groupName TEXT)")
            sde_conn.execute("CREATE TABLE IF NOT EXISTS invTypes (typeID INTEGER PRIMARY KEY, groupID INTEGER, typeName TEXT, typeName_en TEXT, volume REAL, mass REAL, description TEXT, source TEXT, pinyinFull TEXT, pinyinInitials TEXT)")
            sde_conn.execute("CREATE TABLE IF NOT EXISTS mapSolarSystems (solarSystemID INTEGER PRIMARY KEY, regionID INTEGER, solarSystemName TEXT)")
            sde_conn.execute("CREATE TABLE IF NOT EXISTS staStations (stationID INTEGER PRIMARY KEY, solarSystemID INTEGER, stationName TEXT)")
            sde_conn.commit()
            sde_conn.close()
        except Exception as e:
            logger.error(f"[{init_server}] 空白 SDE 创建失败: {e}")

    if not os.path.exists(init_db):
        try:
            logger.info(f"首测运行，发现无存档。正建立纯净的 user_data_{init_server} 数据库于 {init_db}")
            init_conn = sqlite3.connect(init_db)
            init_conn.execute('''CREATE TABLE IF NOT EXISTS auth_tokens (character_id INTEGER PRIMARY KEY, character_name TEXT, corp_id INTEGER, is_director BOOLEAN DEFAULT 0, is_corp_fetcher BOOLEAN DEFAULT 0, access_token TEXT, refresh_token TEXT, token_expiry TIMESTAMP, scopes TEXT)''')
            init_conn.execute('''CREATE TABLE IF NOT EXISTS assets (item_id INTEGER PRIMARY KEY, type_id INTEGER, owner_id INTEGER, location_id INTEGER, location_flag TEXT, location_type TEXT, quantity INTEGER, is_singleton BOOLEAN, is_corp BOOLEAN, is_blueprint BOOLEAN, is_original BOOLEAN, name TEXT, location_name TEXT, root_item_id INTEGER, is_ship_fitted BOOLEAN DEFAULT 0)''')
            init_conn.execute('CREATE TABLE IF NOT EXISTS structure_cache (structure_id INTEGER PRIMARY KEY, name TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
            init_conn.execute('CREATE TABLE IF NOT EXISTS owners_cache (owner_id INTEGER PRIMARY KEY, name TEXT, is_corp BOOLEAN)')
            init_conn.execute('CREATE INDEX IF NOT EXISTS idx_owner ON assets(owner_id)')
            init_conn.execute('CREATE INDEX IF NOT EXISTS idx_loc ON assets(location_id)')
            init_conn.commit()
            init_conn.close()
        except Exception as e:
            logger.error(f"启动自动建表失败: {e}")

DIST_DIR = os.path.join(BASE_PATH, 'dist')

SERVER_CONFIG = {
    'serenity': {
        'client_id': "bc90aa496a404724a93f41b4f4e97761",
        'login_base': "https://login.evepc.163.com",
        'esi_base': "https://ali-esi.evepc.163.com",
        'callback': "https://ali-esi.evepc.163.com/ui/oauth2-redirect.html"
    },
    'tranquility': {
        'client_id': "c5c106a0a3f04a8e91329d24ce762825",
        'login_base': "https://login.eveonline.com",
        'esi_base': "https://esi.evetech.net",
        'callback': "http://localhost:8001/api/auth/callback/tranquility"
    },
    'infinity': {
        'client_id': "bc90aa496a404724a93f41b4f4e97761",
        'login_base': "https://login-infinity.evepc.163.com",
        'esi_base': "https://ali-esi.evepc.163.com",
        'callback': "https://ali-esi.evepc.163.com/ui/oauth2-redirect.html"
    }
}

REQUIRED_SCOPES = [
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
    "esi-characters.read_blueprints.v1",
    "esi-corporations.read_blueprints.v1",
    "esi-characters.read_corporation_roles.v1",
    "esi-location.read_location.v1",
    "esi-location.read_ship_type.v1",
    "esi-universe.read_structures.v1"
]

# infinity ESI 不支持蓝图相关 scope
REQUIRED_SCOPES_INFINITY = [
    "esi-assets.read_assets.v1",
    "esi-assets.read_corporation_assets.v1",
    "esi-characters.read_corporation_roles.v1",
    "esi-location.read_location.v1",
    "esi-location.read_ship_type.v1",
    "esi-universe.read_structures.v1"
]

app = FastAPI(title="EVE Asset Manager V17 (Ops)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"])


def get_db_connection(server: str, lang: str = "en"):
    db_path, sde_path = get_db_paths(server, lang)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    if os.path.exists(sde_path): 
        conn.execute(f"ATTACH DATABASE '{sde_path}' AS sde")
    return conn

def get_sde_tables(conn) -> set:
    """返回已附加的 SDE 数据库中实际存在的表名集合"""
    try:
        rows = conn.execute("SELECT name FROM sde.sqlite_master WHERE type='table'").fetchall()
        return {row[0] for row in rows}
    except Exception:
        return set()


import logging

sync_status = {
    "assets": {"running": False, "text": "就绪", "last_update": 0, "has_error": False},
    "universe": {"running": False, "text": "就绪", "last_update": 0, "has_error": False}
}
class ProgressHandler(logging.Handler):
    def __init__(self, key):
        super().__init__()
        self.key = key
    def emit(self, record):
        sync_status[self.key]["text"] = self.format(record)
        if record.levelno >= logging.ERROR:
            sync_status[self.key]["has_error"] = True

# 挂载自定义钩子截获底层旧脚本输出
for key, logger_name in [("assets", "AssetWorker"), ("universe", "UniverseSync")]:
    l = logging.getLogger(logger_name)
    h = ProgressHandler(key)
    h.setFormatter(logging.Formatter('%(message)s'))
    l.addHandler(h)

sync_lock = threading.Lock()
def run_script_process(script_type, server):
    if not sync_lock.acquire(blocking=False): 
        logger.warning(f"Script run skipped (Locked): {script_type}")
        return
    sync_status[script_type]["has_error"] = False
    sync_status[script_type]["running"] = True
    sync_status[script_type]["text"] = f"初始化系统线程中... ({server})"
    try: 
        logger.info(f"Starting script process: {script_type} on {server}")
        if script_type == 'assets':
            import argparse
            am_args = argparse.Namespace(server=server)
            am.UnifiedAssetManager(args=am_args).run()
        elif script_type == 'universe':
            import argparse
            us_args = argparse.Namespace(server=server, threads=50, min_threads=2, fail_rate=0.3)
            uss.main(us_args)
        logger.info(f"Script process finished: {script_type}")
    except Exception as e:
        logger.error(f"Script execution failed: {e}")
        sync_status[script_type]["text"] = f"严重错误退出: {e}"
        sync_status[script_type]["has_error"] = True
    finally: 
        sync_status[script_type]["running"] = False
        if not sync_status[script_type]["has_error"]:
            sync_status[script_type]["text"] = "同步已完成并就绪"
        sync_lock.release()

class SyncResponse(BaseModel): message: str
class UrlPayload(BaseModel):
    url: str
    server: str = "serenity"
    code_verifier: Optional[str] = None

@app.get("/api/filters")
def get_filters(server: str = "serenity", lang: str = "en"):
    conn = get_db_connection(server, lang)
    cursor = conn.cursor()
    try:
        sde_tables = get_sde_tables(conn)
        has_stations   = 'staStations'   in sde_tables
        has_systems    = 'mapSolarSystems' in sde_tables
        has_groups     = 'invGroups'      in sde_tables
        has_categories = 'invCategories'  in sde_tables

        cursor.execute("""
            SELECT DISTINCT a.owner_id, a.is_corp, 
                   COALESCE(oc.name, 'Corp ' || a.owner_id) as name
            FROM assets a
            LEFT JOIN owners_cache oc ON a.owner_id = oc.owner_id
        """)
        owners = [dict(row) for row in cursor.fetchall()]

        # 位置名：优先 SDE 表查询，否则直接用 assets.location_name（worker 已写入）
        if has_stations and has_systems:
            loc_sql = """
                SELECT COALESCE(
                    (SELECT stationName FROM sde.staStations WHERE stationID = location_id),
                    (SELECT solarSystemName FROM sde.mapSolarSystems WHERE solarSystemID = location_id),
                    location_name
                ) as location_name, COUNT(*) as count
                FROM assets WHERE is_ship_fitted = 0
                GROUP BY 1 ORDER BY count DESC
            """
        else:
            loc_sql = """
                SELECT COALESCE(location_name, 'Unknown Location ' || location_id) as location_name,
                       COUNT(*) as count
                FROM assets WHERE is_ship_fitted = 0
                GROUP BY location_name ORDER BY count DESC
            """
        cursor.execute(loc_sql)
        locations = [dict(row) for row in cursor.fetchall()]

        # 分类：需要 invTypes + invGroups + invCategories 同时存在
        categories = []
        if has_groups and has_categories:
            try:
                cursor.execute("""
                    SELECT DISTINCT c.categoryID, c.categoryName
                    FROM assets a
                    JOIN sde.invTypes t ON a.type_id = t.typeID
                    JOIN sde.invGroups g ON t.groupID = g.groupID
                    JOIN sde.invCategories c ON g.categoryID = c.categoryID
                    ORDER BY c.categoryName
                """)
                categories = [dict(row) for row in cursor.fetchall()]
            except Exception as e:
                logger.warning(f"Categories query failed: {e}")

        return {"owners": owners, "locations": locations, "categories": categories}
    except Exception as e:
        logger.error(f"Error in get_filters: {e}")
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.get("/api/search")
def search_assets(
    server: str = "serenity",
    lang: str = "en",
    q: Optional[str] = None, 
    root_id: Optional[int] = None, 
    owner_ids: Optional[str] = None, 
    location_name: Optional[str] = None,
    category_id: Optional[int] = None,
    include_fitted: bool = False, 
    page: int = 1, 
    limit: int = 100
):
    conn = get_db_connection(server, lang)
    cursor = conn.cursor()
    offset = (page - 1) * limit

    sde_tables = get_sde_tables(conn)
    has_inv_types  = 'invTypes'      in sde_tables
    has_groups     = 'invGroups'     in sde_tables
    has_categories = 'invCategories' in sde_tables
    has_stations   = 'staStations'   in sde_tables
    has_systems    = 'mapSolarSystems' in sde_tables

    # 构建 JOIN（按实际存在的表决定）
    base_joins = "FROM assets a"
    if has_inv_types:
        base_joins += "\n        LEFT JOIN sde.invTypes t ON a.type_id = t.typeID"
    if has_groups:
        base_joins += "\n        LEFT JOIN sde.invGroups g ON t.groupID = g.groupID"
    if has_categories:
        base_joins += "\n        LEFT JOIN sde.invCategories c ON g.categoryID = c.categoryID"
    base_joins += "\n        LEFT JOIN owners_cache oc ON a.owner_id = oc.owner_id"
    base_joins += "\n        LEFT JOIN assets parent ON a.location_id = parent.item_id"
    if has_inv_types:
        base_joins += "\n        LEFT JOIN sde.invTypes pt ON parent.type_id = pt.typeID"

    # 位置名表达式
    if has_stations and has_systems:
        loc_expr = "COALESCE((SELECT stationName FROM sde.staStations WHERE stationID = a.location_id), (SELECT solarSystemName FROM sde.mapSolarSystems WHERE solarSystemID = a.location_id), a.location_name)"
    else:
        loc_expr = "COALESCE(a.location_name, 'Unknown Location ' || a.location_id)"

    where_clause = "WHERE 1=1"
    params = []
    
    if root_id is not None:
        where_clause += " AND (a.location_id = ? OR a.root_item_id = ?) AND a.item_id != ?"
        params.extend([root_id, root_id, root_id])
    else:
        if owner_ids:
            ids = owner_ids.split(',')
            where_clause += f" AND a.owner_id IN ({','.join(['?']*len(ids))})"
            params.extend(ids)
        if location_name:
            where_clause += f" AND {loc_expr} = ?"
            params.append(location_name)
        if category_id and has_categories:
            where_clause += " AND c.categoryID = ?"
            params.append(category_id)
        if not include_fitted:
            where_clause += " AND a.is_ship_fitted = 0"

        if q and q.strip():
            q_str = f"%{q.strip().lower()}%"
            conds = ["t.typeName LIKE ?", "t.pinyinFull LIKE ?", "t.pinyinInitials LIKE ?",
                     "a.name LIKE ?", f"{loc_expr} LIKE ?"] if has_inv_types else [f"{loc_expr} LIKE ?", "a.name LIKE ?"]
            if has_groups:
                conds.append("g.groupName LIKE ?")
            if has_categories:
                conds.append("c.categoryName LIKE ?")
            where_clause += f" AND ({' OR '.join(conds)})"
            params.extend([q_str] * len(conds))

    try:
        count_sql = f"SELECT COUNT(*) {base_joins} {where_clause}"
        cursor.execute(count_sql, params)
        total_records = cursor.fetchone()[0]

        type_name_col = "t.typeName" if has_inv_types else "NULL"
        stats_sql = f"""
            SELECT {type_name_col}, SUM(a.quantity) as total_qty 
            {base_joins} 
            {where_clause} 
            GROUP BY {type_name_col}
            ORDER BY total_qty DESC
        """
        cursor.execute(stats_sql, params)
        stats_rows = cursor.fetchall()
        statistics = [{"name": r[0], "quantity": r[1]} for r in stats_rows]
        grand_total_qty = sum(item['quantity'] for item in statistics)

        data_sql = f"""
            SELECT 
                a.item_id, a.type_id, a.owner_id, a.is_corp, 
                a.quantity, {loc_expr} as location_name, a.location_id, a.name as custom_name,
                a.is_singleton, a.is_blueprint, a.is_ship_fitted, a.root_item_id,
                {'t.typeName, t.pinyinFull, t.pinyinInitials,' if has_inv_types else 'NULL as typeName, NULL as pinyinFull, NULL as pinyinInitials,'}
                {'g.groupName, g.groupID,' if has_groups else 'NULL as groupName, NULL as groupID,'}
                {'c.categoryID,' if has_categories else 'NULL as categoryID,'}
                COALESCE(oc.name, 'Corp ' || a.owner_id) as owner_name,
                COALESCE(parent.name, {'pt.typeName' if has_inv_types else 'NULL'}) as parent_container_name
            {base_joins}
            {where_clause}
            ORDER BY a.location_name, {'t.typeName' if has_inv_types else 'a.type_id'}
            LIMIT ? OFFSET ?
        """
        cursor.execute(data_sql, params + [limit, offset])
        rows = cursor.fetchall()
        
        return {"data": [dict(row) for row in rows], "total": total_records, "total_quantity": grand_total_qty, "statistics": statistics}
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(500, str(e))
    finally:
        conn.close()

@app.get("/api/auth/list")
def list_auth_chars(server: str = "serenity"):
    conn = get_db_connection(server)
    try:
        rows = conn.execute("""
            SELECT t.character_name, t.character_id, t.corp_id, t.is_director, 
                   oc.name as corp_name
            FROM auth_tokens t
            LEFT JOIN owners_cache oc ON t.corp_id = oc.owner_id
        """).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()

@app.delete("/api/auth/remove/{char_id}")
def remove_auth_char(char_id: int, server: str = "serenity"):
    conn = get_db_connection(server)
    try:
        conn.execute("DELETE FROM auth_tokens WHERE character_id=?", (char_id,))
        conn.execute("DELETE FROM assets WHERE owner_id=?", (char_id,))
        conn.commit()
        logger.info(f"Removed auth char: {char_id}")
        return {"message": "角色已移除"}
    finally:
        conn.close()

@app.post("/api/auth/add")
def add_auth_char(payload: UrlPayload):
    url = payload.url.strip()
    server = payload.server
    code = None
    try:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        if 'code' in params: code = params['code'][0]
        if not code: params = parse_qs(parsed.fragment); code = params.get('code', [None])[0]
        if not code and "code=" in url: code = url.split("code=")[1].split("&")[0]
        if not code: raise HTTPException(400, "无法提取 Code")
        
        cfg = SERVER_CONFIG[server]
        data = {"grant_type": "authorization_code", "code": code, "client_id": cfg['client_id']}
        if server in ('serenity', 'infinity'):
            data["redirect_uri"] = cfg['callback']
        else:
            if payload.code_verifier:
                data["code_verifier"] = payload.code_verifier
            else:
                data["redirect_uri"] = cfg['callback']
                
        r = requests.post(f"{cfg['login_base']}/v2/oauth/token", data=data, timeout=15)
        if not r.ok: 
            try: r_json = r.json()
            except: r_json = r.text
            logger.error(f"Token fetch failed: {r_json}")
            raise HTTPException(400, f"Token失败: {r_json}")
            
        token_data = r.json()
        access_token = token_data['access_token']
        refresh_token = token_data.get('refresh_token', access_token)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=token_data['expires_in'])

        verify = requests.get(f"{cfg['login_base']}/oauth/verify", headers={"Authorization": f"Bearer {access_token}"}).json()
        char_id = verify['CharacterID']
        name = verify['CharacterName']

        corp_id = 0
        if server == 'serenity':
            ds_param = "?datasource=serenity"
        elif server == 'infinity':
            ds_param = "?datasource=infinity"
        else:
            ds_param = "?datasource=tranquility"
        try:
            pub = requests.get(f"{cfg['esi_base']}/latest/characters/{char_id}/{ds_param}", timeout=5)
            if pub.status_code == 200: corp_id = pub.json().get('corporation_id', 0)
        except: pass

        is_director = 0
        try:
            roles = requests.get(f"{cfg['esi_base']}/latest/characters/{char_id}/roles/{ds_param}", headers={"Authorization": f"Bearer {access_token}"}, timeout=5)
            if roles.status_code == 200 and 'Director' in roles.json().get('roles', []): is_director = 1
        except: pass
        
        if corp_id > 0:
            try:
                c_req = requests.get(f"{cfg['esi_base']}/latest/corporations/{corp_id}/{ds_param}", timeout=5)
                if c_req.status_code == 200:
                    c_name = c_req.json().get('name', f'Corp {corp_id}')
                    conn_tmp = get_db_connection(server)
                    conn_tmp.execute("INSERT OR REPLACE INTO owners_cache (owner_id, name, is_corp) VALUES (?,?,1)", (corp_id, c_name))
                    conn_tmp.commit()
                    conn_tmp.close()
            except: pass

        conn = get_db_connection(server)
        exist = conn.execute("SELECT is_corp_fetcher FROM auth_tokens WHERE character_id=?", (char_id,)).fetchone()
        is_fetcher = exist[0] if exist else 0
        
        conn.execute('''INSERT OR REPLACE INTO auth_tokens 
            (character_id, character_name, corp_id, is_director, is_corp_fetcher, access_token, refresh_token, token_expiry, scopes)
            VALUES (?,?,?,?,?,?,?,?,?)''', (char_id, name, corp_id, is_director, is_fetcher, access_token, refresh_token, expiry, " ".join(REQUIRED_SCOPES_INFINITY if server == 'infinity' else REQUIRED_SCOPES)))
        conn.commit()
        conn.close()
        
        role_msg = " (⭐总监)" if is_director else ""
        logger.info(f"Auth added for {name} on {server}")
        return {"message": f"成功添加: {name}{role_msg}"}
    except Exception as e:
        logger.error(f"Auth add error: {e}")
        raise HTTPException(400, str(e))


@app.get("/api/sync/status")
def get_sync_status(server: str = "serenity"):
    import os, time
    db_mtime = "无缓存"
    db_path = os.path.join(DATA_DIR, f"user_data_{server}.sqlite")
    if os.path.exists(db_path):
        t = os.path.getmtime(db_path)
        # 时间格式化: 04月18日 12:30
        db_mtime = time.strftime("%m-%d %H:%M:%S", time.localtime(t))
    return {"assets": sync_status["assets"], "universe": sync_status["universe"], "db_mtime": db_mtime}

@app.post("/api/sync/assets", response_model=SyncResponse)
def sync_assets_ep(bt: BackgroundTasks, server: str = "serenity"):
    if sync_lock.locked(): raise HTTPException(423, "Running")
    bt.add_task(run_script_process, 'assets', server)
    logger.info("Triggered manual Asset Sync")
    return {"message": "Started"}

@app.post("/api/sync/universe", response_model=SyncResponse)
def sync_universe_ep(bt: BackgroundTasks, server: str = "serenity"):
    if sync_lock.locked(): raise HTTPException(423, "Running")
    bt.add_task(run_script_process, 'universe', server)
    logger.info("Triggered manual Universe Sync")
    return {"message": "Started"}

@app.get("/api/auth/callback/tranquility")
def tranquility_callback(code: str = None, state: str = None):
    return HTMLResponse(content="<html><body><h2 style='color:green;'>✅ 授权安全口令获取成功！</h2><h3>请全选并复制当前浏览器地址栏上方的完整网址，并将其粘贴回您的 EVE 资产管理器的第二步输入框中！</h3></body></html>")

# ================= 静态文件托管 =================
if os.path.exists(DIST_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")
    
    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(os.path.join(DIST_DIR, "favicon.ico"))

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"): raise HTTPException(404, "API endpoint not found")
        file_path = os.path.join(DIST_DIR, full_path)
        headers = {"Cache-Control": "no-cache, no-store, must-revalidate"} if (not full_path or full_path.endswith(".html")) else {}
        if os.path.exists(file_path) and os.path.isfile(file_path): return FileResponse(file_path, headers=headers)
        return FileResponse(os.path.join(DIST_DIR, "index.html"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
else:
    logger.warning(f"Frontend dist directory not found at {DIST_DIR}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
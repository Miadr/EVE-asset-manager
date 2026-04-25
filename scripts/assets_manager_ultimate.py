import sqlite3
import requests
import time
import os
import sys
import re
import threading
import traceback
from datetime import datetime, timedelta, timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 引入日志
from logger_config import setup_logger
logger = setup_logger("AssetWorker", "worker_assets.log")

# --- 全局配置 ---
OUTPUT_DIR = os.path.join(os.getcwd(), 'data')
AUTH_DB = os.path.join(OUTPUT_DIR, 'user_data.sqlite')
SDE_DB = os.path.join(OUTPUT_DIR, 'eve_universe.sqlite')

ESI_BASE = "https://ali-esi.evepc.163.com/latest"
AUTH_URL = "https://login.evepc.163.com/v2/oauth/token"
CLIENT_ID = "bc90aa496a404724a93f41b4f4e97761"
USER_AGENT = "EVE_Asset_Manager_Ultimate_v23_Ops"

def _clean_localized_name(name):
    """清理 EVE 本地化 XML 标签: <localized hint="Ibis">伊毕斯号*(伊毕斯号) → 伊毕斯号"""
    if not name:
        return name
    name = re.sub(r'<localized[^>]*>', '', name)   # 去掉 <localized hint="..."> 前缀
    name = re.sub(r'\*\([^)]*\)\s*$', '', name)    # 去掉末尾 *(...)
    return name.strip() or None

def get_db_conn():
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR, exist_ok=True)
    conn = sqlite3.connect(AUTH_DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    if os.path.exists(SDE_DB):
        try: conn.execute(f"ATTACH DATABASE '{SDE_DB}' AS sde")
        except sqlite3.OperationalError: pass 
    return conn

_sys_lock = threading.Lock()
def acquire_lock():
    # 改为使用简单的线程互斥锁，由于是单进程，这足以保护不并发
    if not _sys_lock.acquire(blocking=False):
        return None
    
    # 返回一个假的文件对象或标志来表示成功获取锁
    class DummyLock:
        def close(self):
            _sys_lock.release()
    return DummyLock()

class TokenManager:
    def __init__(self): pass

    def get_token(self, char_id):
        conn = get_db_conn()
        try:
            row = conn.execute("SELECT access_token, refresh_token, token_expiry, character_name FROM auth_tokens WHERE character_id=?", (char_id,)).fetchone()
            if not row: return None, None
            
            access_token, refresh_token, expiry_str, name = row
            try:
                clean_time = expiry_str.split("+")[0].split(".")[0]
                expiry = datetime.strptime(clean_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            except: 
                expiry = datetime.now(timezone.utc) - timedelta(seconds=1)

            if datetime.now(timezone.utc) > expiry - timedelta(minutes=15):
                logger.info(f"[Auth] Token expiring/old for {name}, refreshing...")
                return self._refresh_and_save(char_id, refresh_token, name)
            return access_token, name
        finally: conn.close()

    def force_refresh(self, char_id):
        conn = get_db_conn()
        try:
            row = conn.execute("SELECT refresh_token, character_name FROM auth_tokens WHERE character_id=?", (char_id,)).fetchone()
            if not row: return None, None
            logger.warning(f"[Auth] Forcing refresh for {row['character_name']}...")
            return self._refresh_and_save(char_id, row['refresh_token'], row['character_name'])
        finally: conn.close()

    def _refresh_and_save(self, char_id, current_refresh_token, name):
        try:
            r = requests.post(AUTH_URL, data={"grant_type": "refresh_token", "refresh_token": current_refresh_token, "client_id": CLIENT_ID}, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=10)
            if r.status_code != 200:
                logger.error(f"[Auth Error] Refresh failed for {name}: {r.status_code} {r.text}")
                return None, name
            data = r.json()
            new_access = data['access_token']
            new_refresh = data.get('refresh_token', current_refresh_token)
            new_expiry = (datetime.now(timezone.utc) + timedelta(seconds=data['expires_in'])).strftime("%Y-%m-%d %H:%M:%S")
            
            conn = get_db_conn()
            try:
                conn.execute("UPDATE auth_tokens SET access_token=?, refresh_token=?, token_expiry=? WHERE character_id=?", (new_access, new_refresh, new_expiry, char_id))
                conn.commit()
                return new_access, name
            finally: conn.close()
        except Exception as e: 
            logger.error(f"[Auth Error] Connection error: {e}")
            return None, name

class UnifiedAssetManager:
    def __init__(self, args=None):
        if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR, exist_ok=True)
        self.server = getattr(args, 'server', 'serenity') if args else 'serenity'
        global AUTH_DB, SDE_DB, ESI_BASE, AUTH_URL, CLIENT_ID
        if self.server == 'tranquility':
            AUTH_DB = os.path.join(OUTPUT_DIR, 'user_data_tranquility.sqlite')
            SDE_DB = os.path.join(OUTPUT_DIR, 'eve_universe_tranquility.sqlite')
            ESI_BASE = "https://esi.evetech.net/latest"
            AUTH_URL = "https://login.eveonline.com/v2/oauth/token"
            CLIENT_ID = "c5c106a0a3f04a8e91329d24ce762825"
            self.ds = "?datasource=tranquility"
        elif self.server == 'infinity':
            AUTH_DB = os.path.join(OUTPUT_DIR, 'user_data_infinity.sqlite')
            SDE_DB = os.path.join(OUTPUT_DIR, 'eve_universe_infinity.sqlite')
            ESI_BASE = "https://ali-esi.evepc.163.com/latest"
            AUTH_URL = "https://login-infinity.evepc.163.com/v2/oauth/token"
            CLIENT_ID = "bc90aa496a404724a93f41b4f4e97761"
            self.ds = "?datasource=infinity"
        else:
            AUTH_DB = os.path.join(OUTPUT_DIR, 'user_data_serenity.sqlite')
            SDE_DB = os.path.join(OUTPUT_DIR, 'eve_universe_serenity.sqlite')
            ESI_BASE = "https://ali-esi.evepc.163.com/latest"
            AUTH_URL = "https://login.evepc.163.com/v2/oauth/token"
            CLIENT_ID = "bc90aa496a404724a93f41b4f4e97761"
            self.ds = "?datasource=serenity"
            
        self.token_mgr = TokenManager()
        
        self.session = requests.Session()
        retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))
        self.session.headers.update({"User-Agent": USER_AGENT})
        
        self._init_db()
        self.processed_corps = set() 
        self.asset_map = {}       
        self.location_names = {}  
        self.ship_types = set()   
        self.nameable_types = None
        
        conn = get_db_conn()
        try:
            cursor = conn.execute("SELECT typeID FROM sde.invTypes WHERE groupID IN (SELECT groupID FROM sde.invGroups WHERE categoryID IN (6, 65) OR groupID IN (12, 340, 448))")
            self.nameable_types = {row[0] for row in cursor.fetchall()}
        except: pass
        finally: conn.close()

    def _init_db(self):
        conn = get_db_conn()
        try:
            conn.execute('''CREATE TABLE IF NOT EXISTS assets (item_id INTEGER PRIMARY KEY, type_id INTEGER, owner_id INTEGER, location_id INTEGER, location_flag TEXT, location_type TEXT, quantity INTEGER, is_singleton BOOLEAN, is_corp BOOLEAN, is_blueprint BOOLEAN, is_original BOOLEAN, name TEXT, location_name TEXT, root_item_id INTEGER, is_ship_fitted BOOLEAN DEFAULT 0)''')
            conn.execute('CREATE TABLE IF NOT EXISTS structure_cache (structure_id INTEGER PRIMARY KEY, name TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
            conn.execute('CREATE TABLE IF NOT EXISTS owners_cache (owner_id INTEGER PRIMARY KEY, name TEXT, is_corp BOOLEAN)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_owner ON assets(owner_id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_loc ON assets(location_id)')
            conn.commit()
        finally: conn.close()

    def _fetch_pages_serial(self, url_base, token, owner_id, owner_name):
        all_items = []
        current_token = token
        total_pages = 1
        first_page_success = False
        
        for attempt in range(3):
            try:
                r1 = self.session.get(f"{url_base}&page=1", headers={"Authorization": f"Bearer {current_token}"}, timeout=20)
                if r1.status_code in [401, 403]:
                    logger.warning(f"Token Error HTTP {r1.status_code} on Page 1 for {owner_name}. Details: {r1.text}. Refreshing...")
                    new_token, _ = self.token_mgr.force_refresh(owner_id)
                    if new_token: current_token = new_token; continue
                
                if r1.status_code == 200:
                    page1_data = r1.json()
                    all_items.extend(page1_data)
                    total_pages = int(r1.headers.get('X-Pages', 1))
                    first_page_success = True; break
                else:
                    logger.error(f"Unexpected HTTP {r1.status_code} on Page 1 for {owner_name}: {r1.text}")
                    break
            except Exception as e:
                logger.error(f"Network exception on Page 1 setup for {owner_name}: {e}")
                time.sleep(2)
        
        if not first_page_success:
            logger.error(f"Failed to fetch Page 1 for {owner_name}. URL: {url_base}&page=1 Skipping.")
            return None

        if total_pages > 1:
            for p in range(2, total_pages + 1):
                page_success = False
                for attempt in range(5): 
                    try:
                        r = self.session.get(f"{url_base}&page={p}", headers={"Authorization": f"Bearer {current_token}"}, timeout=20)
                        if r.status_code == 200: all_items.extend(r.json()); page_success = True; break
                        elif r.status_code in [401, 403, 500]:
                             new_token, _ = self.token_mgr.force_refresh(owner_id)
                             if new_token: current_token = new_token
                        time.sleep(1 + attempt)
                    except: time.sleep(1)
                if not page_success: logger.error(f"Page {p}/{total_pages} failed for {owner_name}.")
        return all_items

    def _resolve_corp_name(self, corp_id):
        try:
            r = self.session.get(f"{ESI_BASE}/corporations/{corp_id}/{self.ds}", timeout=5)
            if r.status_code == 200: return r.json().get('name', f'Corp {corp_id}')
        except: pass
        return f'Corp {corp_id}'

    def update_roles_pre_sync(self):
        logger.info("=== Phase 0: Checking Roles ===")
        conn = get_db_conn()
        try:
            chars = conn.execute("SELECT character_id, character_name FROM auth_tokens").fetchall()
            conn.close()
            for row in chars:
                cid, name = row['character_id'], row['character_name']
                token, _ = self.token_mgr.get_token(cid)
                if not token: continue
                is_director = 0; corp_id = 0
                try:
                    pub = self.session.get(f"{ESI_BASE}/characters/{cid}/{self.ds}", timeout=5)
                    if pub.status_code == 200: corp_id = pub.json().get('corporation_id', 0)
                    roles_r = self.session.get(f"{ESI_BASE}/characters/{cid}/roles/{self.ds}", headers={"Authorization": f"Bearer {token}"}, timeout=5)
                    if roles_r.status_code in [401, 403]:
                         token, _ = self.token_mgr.force_refresh(cid)
                         if token: roles_r = self.session.get(f"{ESI_BASE}/characters/{cid}/roles/{self.ds}", headers={"Authorization": f"Bearer {token}"}, timeout=5)
                    if roles_r.status_code == 200 and 'Director' in roles_r.json().get('roles', []): is_director = 1
                except: pass
                upd = get_db_conn()
                upd.execute("UPDATE auth_tokens SET corp_id=?, is_director=? WHERE character_id=?", (corp_id, is_director, cid))
                if corp_id > 0:
                    exist = upd.execute("SELECT name FROM owners_cache WHERE owner_id=?", (corp_id,)).fetchone()
                    if not exist:
                        cname = self._resolve_corp_name(corp_id)
                        upd.execute("INSERT OR REPLACE INTO owners_cache VALUES (?,?,1)", (corp_id, cname))
                upd.commit(); upd.close()
        except: pass

    def run_phase_1_fetch_and_save(self):
        logger.info("=== Phase 1: Fetch & Save ===")
        conn = get_db_conn()
        try: chars = conn.execute("SELECT character_id, character_name, corp_id, is_director FROM auth_tokens").fetchall()
        finally: conn.close()

        total = len(chars)
        for idx, row in enumerate(chars, 1):
            char_id, name, corp_id, is_director = row['character_id'], row['character_name'], row['corp_id'], bool(row['is_director'])
            token, _ = self.token_mgr.get_token(char_id)
            if not token: logger.warning(f"Skipping {name} (No Token)"); continue
            logger.info(f"Processing ({idx}/{total}): {name}")
            
            char_assets = self._fetch_pages_serial(f"{ESI_BASE}/characters/{char_id}/assets/{self.ds}", token, char_id, name)
            if char_assets is not None:
                # infinity 服务器不支持蓝图 scope，跳过避免 403 警告
                if self.server != 'infinity':
                    char_bp = self._fetch_pages_serial(f"{ESI_BASE}/characters/{char_id}/blueprints/{self.ds}", token, char_id, name)
                else:
                    char_bp = []
                self._process_and_write(char_id, char_assets, char_bp or [], token, is_corp=False, owner_name=name)

            if is_director and corp_id and corp_id not in self.processed_corps:
                corp_name = self._resolve_corp_name(corp_id)
                logger.info(f"Processing Corp: {corp_name}")
                corp_assets = self._fetch_pages_serial(f"{ESI_BASE}/corporations/{corp_id}/assets/{self.ds}", token, char_id, corp_name)
                if corp_assets is not None:
                    if self.server != 'infinity':
                        corp_bp = self._fetch_pages_serial(f"{ESI_BASE}/corporations/{corp_id}/blueprints/{self.ds}", token, char_id, corp_name)
                    else:
                        corp_bp = []
                    self._process_and_write(corp_id, corp_assets, corp_bp or [], token, is_corp=True, owner_name=corp_name)
                    self.processed_corps.add(corp_id)

    def _process_and_write(self, owner_id, asset_list, bp_list, token, is_corp, owner_name):
        merged = {}
        for item in asset_list:
            tid = item['item_id']
            merged[tid] = {
                'item_id': tid, 'type_id': item['type_id'], 'owner_id': owner_id,
                'location_id': item['location_id'], 'location_flag': item['location_flag'],
                'location_type': item['location_type'], 'quantity': item['quantity'],
                'is_singleton': item['is_singleton'], 'is_blueprint': 0, 'is_original': None, 'name': None
            }
        for bp in bp_list:
            tid = bp['item_id']
            if tid in merged: merged[tid]['is_blueprint'] = 1; merged[tid]['is_original'] = 1 if bp.get('quantity') == -1 else 0
            else:
                qty = bp.get('quantity', 1)
                merged[tid] = {'item_id': tid, 'type_id': bp['type_id'], 'owner_id': owner_id, 'location_id': bp['location_id'], 'location_flag': bp['location_flag'], 'location_type': 'other', 'quantity': 1 if qty < 0 else qty, 'is_singleton': True, 'is_blueprint': 1, 'is_original': 1 if qty == -1 else 0, 'name': None}

        candidates = [v for v in merged.values() if v['is_singleton']]
        if self.nameable_types: valid_ids = [v['item_id'] for v in candidates if v['type_id'] in self.nameable_types]
        else: valid_ids = [v['item_id'] for v in candidates]

        if valid_ids:
            endpoint = "corporations" if is_corp else "characters"
            url = f"{ESI_BASE}/{endpoint}/{owner_id}/assets/names/{self.ds}"
            try:
                chunk_size = 1000
                chunks = [valid_ids[i:i+chunk_size] for i in range(0, len(valid_ids), chunk_size)]
                for chunk in chunks:
                    r = self.session.post(url, json=chunk, headers={"Authorization": f"Bearer {token}"}, timeout=10)
                    if r.status_code == 200:
                        for entry in r.json():
                            cleaned = _clean_localized_name(entry.get('name'))
                            if cleaned and cleaned != "None" and entry['item_id'] in merged:
                                merged[entry['item_id']]['name'] = cleaned
            except: pass

        conn = get_db_conn()
        try:
            conn.execute("INSERT OR REPLACE INTO owners_cache VALUES (?,?,?)", (owner_id, owner_name, 1 if is_corp else 0))
            conn.execute("DELETE FROM assets WHERE owner_id=? AND is_corp=?", (owner_id, 1 if is_corp else 0))
            rows = []
            for item in merged.values():
                rows.append((item['item_id'], item['type_id'], item['owner_id'], item['location_id'], item['location_flag'], item['location_type'], item['quantity'], 1 if item['is_singleton'] else 0, 1 if is_corp else 0, item['is_blueprint'], item['is_original'], item['name'], None, None, 0))
            if rows: conn.executemany('INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
            conn.commit()
            logger.info(f"Saved {len(rows)} items for {owner_name}")
        except Exception as e:
            logger.error(f"DB Error processing {owner_name}: {e}")
        finally: conn.close()

    def run_phase_3_locations(self):
        logger.info("=== Phase 3: Locations ===")

        conn = get_db_conn()
        try:
            c = conn.execute("SELECT * FROM assets")
            self.asset_map = {row['item_id']: dict(row) for row in c.fetchall()}
            all_locs = {item['location_id'] for item in self.asset_map.values() if item['location_id'] not in self.asset_map}
            
            unknowns = []
            cached = {}
            try:
                cur = conn.execute("SELECT structure_id, name FROM structure_cache")
                cached = {row[0]: row[1] for row in cur.fetchall()}
            except: pass

            # 检查 SDE 中是否存在 staStations / mapSolarSystems 表
            sde_has_stations = False
            sde_has_systems = False
            try:
                conn.execute("SELECT 1 FROM sde.staStations LIMIT 1")
                sde_has_stations = True
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("SELECT 1 FROM sde.mapSolarSystems LIMIT 1")
                sde_has_systems = True
            except sqlite3.OperationalError:
                pass

            c = conn.cursor()
            for loc_id in all_locs:
                if sde_has_stations:
                    try:
                        c.execute("SELECT stationName FROM sde.staStations WHERE stationID=?", (loc_id,))
                        if res := c.fetchone(): self.location_names[loc_id] = res[0]; continue
                    except sqlite3.OperationalError:
                        pass
                if sde_has_systems:
                    try:
                        c.execute("SELECT solarSystemName FROM sde.mapSolarSystems WHERE solarSystemID=?", (loc_id,))
                        if res := c.fetchone(): self.location_names[loc_id] = res[0]; continue
                    except sqlite3.OperationalError:
                        pass
                if loc_id in cached: self.location_names[loc_id] = cached[loc_id]; continue
                unknowns.append(loc_id)

            logger.info(f"Resolving {len(unknowns)} unknown locations...")
            loc_owners = {}
            for item in self.asset_map.values():
                if item['location_id'] in unknowns: loc_owners.setdefault(item['location_id'], set()).add(item['owner_id'])

            # Fallback list of ALL known valid tokens
            all_tokens = []
            try:
                for k,v in self.token_mgr.__dict__.get('_cache', {}).items(): pass # not accessible directly
                all_raw = conn.execute("SELECT character_id FROM auth_tokens").fetchall()
                all_tokens = [r[0] for r in all_raw]
            except: pass

            # Infinity/Serenity 用自己的 ESI + language=zh
            # Tranquility 用自己的 ESI（可选 language=zh）
            if self.server == 'infinity':
                universe_esi = ESI_BASE
                universe_ds  = self.ds
                lang_param   = "&language=zh"
            elif self.server == 'serenity':
                universe_esi = ESI_BASE
                universe_ds  = self.ds
                lang_param   = "&language=zh"
            else:
                universe_esi = ESI_BASE
                universe_ds  = self.ds
                lang_param   = ""

            new_cache = []
            for i, sid in enumerate(unknowns, 1):
                struct_name = f"Unknown Location {sid}"
                should_cache = False 
                
                if 60000000 <= sid < 64000000:
                    try:
                        r = self.session.get(f"{universe_esi}/universe/stations/{sid}/{universe_ds}{lang_param}", timeout=5)
                        if r.status_code == 200:
                            struct_name = r.json().get('name', struct_name)
                            should_cache = True
                    except: pass
                elif 30000000 <= sid < 33000000:
                    try:
                        r = self.session.get(f"{universe_esi}/universe/systems/{sid}/{universe_ds}{lang_param}", timeout=5)
                        if r.status_code == 200:
                            struct_name = r.json().get('name', struct_name)
                            should_cache = True
                    except: pass
                else:
                    owners = sorted(list(loc_owners.get(sid, set())), key=lambda x: 1 if x > 90000000 else 0)
                    for fallback_char in all_tokens:
                        if fallback_char not in owners: owners.append(fallback_char)
                        
                    for oid in owners:
                        token, _ = self.token_mgr.get_token(oid)
                        if not token: continue
                        try:
                            r = self.session.get(f"{ESI_BASE}/universe/structures/{sid}/{self.ds}", headers={"Authorization": f"Bearer {token}"}, timeout=5)
                            if r.status_code == 200: struct_name = r.json().get('name'); should_cache = True; break
                            elif r.status_code in [401, 403]:
                                 new_token, _ = self.token_mgr.force_refresh(oid)
                                 if new_token:
                                     r = self.session.get(f"{ESI_BASE}/universe/structures/{sid}/{self.ds}", headers={"Authorization": f"Bearer {new_token}"}, timeout=5)
                                     if r.status_code == 200: struct_name = r.json().get('name'); should_cache = True; break
                        except: pass
                
                self.location_names[sid] = struct_name
                if should_cache: new_cache.append((sid, struct_name))

            if new_cache:
                conn.executemany("INSERT OR REPLACE INTO structure_cache (structure_id, name) VALUES (?, ?)", new_cache)
                conn.commit()
        finally: conn.close()

    def run_phase_4_topology(self):
        logger.info("=== Phase 4: Topology ===")
        conn = get_db_conn()
        try:
            c = conn.execute("SELECT typeID FROM sde.invTypes WHERE groupID IN (SELECT groupID FROM sde.invGroups WHERE categoryID=6)")
            self.ship_types = {row[0] for row in c.fetchall()}
        except: self.ship_types = set()

        updates = []
        path_cache = {}

        def analyze_path(curr_id):
            if curr_id in path_cache: return path_cache[curr_id]
            item = self.asset_map.get(curr_id)
            if not item: return None, "Void", False
            pid = item['location_id']
            if pid not in self.asset_map:
                lname = self.location_names.get(pid, str(pid))
                res = (curr_id, lname, False); path_cache[curr_id] = res; return res
            root_id, lname, parent_inside_ship = analyze_path(pid)
            parent_item = self.asset_map.get(pid)
            parent_is_ship = parent_item['type_id'] in self.ship_types if parent_item else False
            am_i_inside_ship = parent_inside_ship or parent_is_ship
            path_cache[curr_id] = (root_id, lname, am_i_inside_ship)
            return root_id, lname, am_i_inside_ship

        for idx, (tid, item) in enumerate(self.asset_map.items(), 1):
            root_id, loc_name, is_inside_ship = analyze_path(tid)
            is_fitted = 1 if is_inside_ship else 0
            updates.append((loc_name, root_id, is_fitted, tid))

        logger.info(f"Updating topology for {len(updates)} records...")
        conn.executemany('UPDATE assets SET location_name=?, root_item_id=?, is_ship_fitted=? WHERE item_id=?', updates)
        conn.commit(); conn.close()

    def run(self):
        lock_file = acquire_lock()
        if not lock_file: logger.warning("Asset Manager already running."); return
        try:
            self.update_roles_pre_sync()
            self.run_phase_1_fetch_and_save()
            self.run_phase_3_locations()
            self.run_phase_4_topology()
            logger.info("Asset Sync Completed Successfully.")
        except Exception as e:
            logger.error(f"Asset Manager Crashed: {e}")
            traceback.print_exc()
        finally:
            if lock_file: lock_file.close()

if __name__ == "__main__":
    app = UnifiedAssetManager()
    app.run()
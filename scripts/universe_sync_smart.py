import sqlite3
import requests
import traceback
import time
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from pypinyin import pinyin, lazy_pinyin, Style

# 引入日志
from logger_config import setup_logger
logger = setup_logger("UniverseSync", "worker_universe.log")

# --- 配置区域 ---
# 硬编码路径
OUTPUT_DIR = os.path.join(os.getcwd(), 'data') # 设置为当前工作目录下的 data 文件夹
DB_PATH = os.path.join(OUTPUT_DIR, 'eve_universe.sqlite')

ESI_BASE = "https://ali-esi.evepc.163.com/latest" 
DATASOURCE = "serenity"
USER_AGENT = "EVE_Universe_Sync_Smart_v2.1"
CHUNK_SIZE = 1000 

def get_retry_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries))
    session.headers.update({"User-Agent": USER_AGENT})
    return session

def get_db_connection():
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row 
    return conn

def get_pinyin_data(text):
    if not text: return "", ""
    full_str = "".join(lazy_pinyin(text))
    first_list = pinyin(text, style=Style.FIRST_LETTER)
    first_str = "".join([x[0] for x in first_list])
    return full_str, first_str

def fetch_all_esi_ids(session):
    logger.info("[1/4] Scanning ESI universe types...")
    url = f"{ESI_BASE}/universe/types/?datasource={DATASOURCE}"
    all_ids = set()
    
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        total_pages = int(resp.headers.get('X-Pages', 1))
        logger.info(f"Connected. Total pages: {total_pages}")
        all_ids.update(resp.json())

        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = []
                for page in range(2, total_pages + 1):
                    futures.append(executor.submit(session.get, f"{url}&page={page}", timeout=15))
                
                count = 0
                for future in as_completed(futures):
                    try:
                        r = future.result()
                        if r.status_code == 200: all_ids.update(r.json())
                    except Exception: pass
                    count += 1
                    if count % 5 == 0: logger.info(f"Page progress: {count}/{total_pages - 1}")

    except Exception as e:
        logger.error(f"ESI Connection failed: {e}")
        return []
    
    logger.info(f"ESI Scan complete. Total items: {len(all_ids)}")
    return list(all_ids)

def fetch_names_bulk(session, type_ids):
    logger.info(f"[2/4] Resolving names ({len(type_ids)} items)...")
    url = f"{ESI_BASE}/universe/names/?datasource={DATASOURCE}"
    chunks = [type_ids[i:i + CHUNK_SIZE] for i in range(0, len(type_ids), CHUNK_SIZE)]
    name_map = {} 
    
    def process_chunk(chunk):
        try:
            r = session.post(url, json=chunk, timeout=20)
            if r.status_code == 200: return r.json()
        except: pass
        return []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(process_chunk, c) for c in chunks]
        count = 0
        for future in as_completed(futures):
            items = future.result()
            if items:
                for item in items:
                    if item.get('category') == 'inventory_type':
                        name_map[item['id']] = item['name']
            count += 1
            if count % 10 == 0: logger.info(f"Name resolve progress: {count}/{len(chunks)}")
            
    logger.info(f"Name resolution complete. Valid items: {len(name_map)}")
    return name_map

def fetch_type_detail(session, type_id):
    url = f"{ESI_BASE}/universe/types/{type_id}/?datasource={DATASOURCE}&language=zh"
    try:
        r = session.get(url, timeout=10)
        if r.status_code == 200: return r.json()
    except: pass
    return None

def main():
    start_time = time.time()
    logger.info("=== Universe Sync Started ===")
    
    session = get_retry_session()
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("CREATE TABLE IF NOT EXISTS invTypes (typeID INTEGER PRIMARY KEY, groupID INTEGER, typeName TEXT, typeName_en TEXT, volume REAL, mass REAL, description TEXT, source TEXT, pinyinFull TEXT, pinyinInitials TEXT)")
        conn.commit()
        
        cursor.execute("SELECT typeID, typeName FROM invTypes")
        local_db_map = {row['typeID']: row['typeName'] for row in cursor.fetchall()}
        logger.info(f"[0/4] Local DB loaded: {len(local_db_map)} records.")
    except Exception as e:
        logger.error(f"DB init failed: {e}")
        return

    esi_ids = fetch_all_esi_ids(session)
    if not esi_ids: return
    
    esi_name_map = fetch_names_bulk(session, esi_ids)

    logger.info("[3/4] Comparing data...")
    to_update_name = [] 
    to_insert_ids = [] 
    
    for tid, name in esi_name_map.items():
        if tid in local_db_map:
            if local_db_map[tid] != name:
                py_full, py_first = get_pinyin_data(name)
                to_update_name.append((name, py_full, py_first, tid))
        else:
            to_insert_ids.append(tid)
            
    logger.info(f"To Rename: {len(to_update_name)} | To Insert: {len(to_insert_ids)}")

    if to_update_name:
        cursor.executemany('UPDATE invTypes SET typeName=?, pinyinFull=?, pinyinInitials=?, source="ESI_RENAME" WHERE typeID=?', to_update_name)
        conn.commit()
        logger.info("Renaming complete.")

    if to_insert_ids:
        logger.info(f"[4/4] Fetching details for {len(to_insert_ids)} new items...")
        insert_buffer = []
        processed_count = 0
        
        for tid in to_insert_ids:
            detail = fetch_type_detail(session, tid)
            if detail:
                name = detail.get('name', 'Unknown')
                py_full, py_first = get_pinyin_data(name)
                insert_buffer.append((detail['type_id'], detail.get('group_id'), name, None, detail.get('volume', 0.0), detail.get('mass', 0.0), detail.get('description', ''), 'ESI_NEW', py_full, py_first))
            
            processed_count += 1
            if processed_count % 50 == 0: logger.info(f"Detail progress: {processed_count}/{len(to_insert_ids)}")
            
            if len(insert_buffer) >= 50:
                cursor.executemany('INSERT OR REPLACE INTO invTypes (typeID, groupID, typeName, typeName_en, volume, mass, description, source, pinyinFull, pinyinInitials) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', insert_buffer)
                conn.commit()
                insert_buffer = []
            time.sleep(0.02) 

        if insert_buffer:
            cursor.executemany('INSERT OR REPLACE INTO invTypes (typeID, groupID, typeName, typeName_en, volume, mass, description, source, pinyinFull, pinyinInitials) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', insert_buffer)
            conn.commit()
            
        logger.info("Insert complete.")
    
    conn.close()
    logger.info(f"=== Sync Finished in {time.time() - start_time:.2f}s ===")

if __name__ == "__main__":
    main()
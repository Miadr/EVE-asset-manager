import sqlite3
import requests
import traceback
import time
import os
import threading
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from pypinyin import pinyin, lazy_pinyin, Style

# 引入日志
from logger_config import setup_logger
logger = setup_logger("UniverseSync", "worker_universe.log")

# --- 配置区域（默认值，由 argparse 覆盖） ---
OUTPUT_DIR = os.path.join(os.getcwd(), 'data')
DB_PATH = os.path.join(OUTPUT_DIR, 'eve_universe_serenity.sqlite')

ESI_BASE = "https://ali-esi.evepc.163.com/latest" 
DATASOURCE = "serenity"
USER_AGENT = "EVE_Universe_Sync_Smart_v2.5_Ops"
CHUNK_SIZE = 1000 

import argparse

def setup_environment(server):
    global DB_PATH, ESI_BASE, DATASOURCE
    if server == "tranquility":
        DB_PATH = os.path.join(OUTPUT_DIR, 'eve_universe_tranquility.sqlite')
        ESI_BASE = "https://esi.evetech.net/latest"
        DATASOURCE = "tranquility"
    elif server == "infinity":
        DB_PATH = os.path.join(OUTPUT_DIR, 'eve_universe_infinity.sqlite')
        ESI_BASE = "https://ali-esi.evepc.163.com/latest"
        DATASOURCE = "infinity"
    else:
        DB_PATH = os.path.join(OUTPUT_DIR, 'eve_universe_serenity.sqlite')
        ESI_BASE = "https://ali-esi.evepc.163.com/latest"
        DATASOURCE = "serenity"


class AdaptiveConcurrency:
    """
    自适应并发控制器。
    - 初始以 initial_workers 线程并发
    - 当窗口期内失败率超过 failure_threshold 时，自动将线程数减半
    - 线程数降至 min_workers 后不再继续降级
    - cooldown 秒内不重复降级，避免抖动
    """

    def __init__(self, initial_workers: int, min_workers: int = 2,
                 failure_threshold: float = 0.3, cooldown: float = 15.0,
                 window: int = 20):
        self._workers = initial_workers
        self.initial_workers = initial_workers
        self.min_workers = min_workers
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.window = window          # 统计窗口大小（最近 N 次请求）

        self._lock = threading.Lock()
        self._results: list[bool] = []  # True=成功, False=失败
        self._last_degrade = 0.0

    @property
    def current_workers(self) -> int:
        with self._lock:
            return self._workers

    def record(self, success: bool):
        with self._lock:
            self._results.append(success)
            # 保持滑动窗口大小
            if len(self._results) > self.window:
                self._results.pop(0)

            if not success:
                self._try_degrade()

    def _try_degrade(self):
        """必须在持锁状态下调用。"""
        if self._workers <= self.min_workers:
            return
        if len(self._results) < self.window // 2:
            return  # 样本不足，先积累

        failure_rate = self._results.count(False) / len(self._results)
        now = time.time()

        if failure_rate >= self.failure_threshold and (now - self._last_degrade) > self.cooldown:
            new_workers = max(self.min_workers, self._workers // 2)
            logger.warning(
                f"[自适应降级] 近 {len(self._results)} 次请求失败率 {failure_rate:.0%}"
                f"（阈值 {self.failure_threshold:.0%}），"
                f"线程数: {self._workers} → {new_workers}"
            )
            self._workers = new_workers
            self._last_degrade = now
            self._results.clear()   # 重置统计，给降级后的表现一个公平窗口

    def reset(self):
        """阶段切换时重置统计（保留当前线程数）。"""
        with self._lock:
            self._results.clear()

    def status(self) -> str:
        with self._lock:
            total = len(self._results)
            if total == 0:
                return f"workers={self._workers}"
            fail = self._results.count(False)
            return f"workers={self._workers}, 近{total}次失败率={fail/total:.0%}"


def get_retry_session():
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    session.mount('https://', HTTPAdapter(max_retries=retries, pool_connections=50, pool_maxsize=100))
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


def fetch_all_esi_ids(session, ac: AdaptiveConcurrency):
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
            def fetch_page(page):
                try:
                    r = session.get(f"{url}&page={page}", timeout=15)
                    if r.status_code == 200:
                        ac.record(True)
                        return r.json()
                    ac.record(False)
                except Exception:
                    ac.record(False)
                return []

            page_list = list(range(2, total_pages + 1))
            completed = 0
            with ThreadPoolExecutor(max_workers=ac.current_workers) as executor:
                futures = {executor.submit(fetch_page, p): p for p in page_list}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        all_ids.update(result)
                    completed += 1
                    if completed % 10 == 0:
                        logger.info(f"Page progress: {completed}/{total_pages - 1} | {ac.status()}")

    except Exception as e:
        logger.error(f"ESI Connection failed: {e}")
        return []
    
    logger.info(f"ESI Scan complete. Total items: {len(all_ids)}")
    return list(all_ids)


def fetch_names_bulk(session, type_ids, ac: AdaptiveConcurrency):
    logger.info(f"[2/4] Resolving names ({len(type_ids)} items)...")
    url = f"{ESI_BASE}/universe/names/?datasource={DATASOURCE}"
    chunks = [type_ids[i:i + CHUNK_SIZE] for i in range(0, len(type_ids), CHUNK_SIZE)]
    name_map = {}
    ac.reset()

    def process_chunk(chunk):
        try:
            r = session.post(url, json=chunk, timeout=20)
            if r.status_code == 200:
                ac.record(True)
                return r.json()
            ac.record(False)
        except Exception:
            ac.record(False)
        return []

    completed = 0
    with ThreadPoolExecutor(max_workers=ac.current_workers) as executor:
        futures = [executor.submit(process_chunk, c) for c in chunks]
        for future in as_completed(futures):
            items = future.result()
            if items:
                for item in items:
                    if item.get('category') == 'inventory_type':
                        name_map[item['id']] = item['name']
            completed += 1
            if completed % 10 == 0:
                logger.info(f"Name resolve progress: {completed}/{len(chunks)} | {ac.status()}")

    logger.info(f"Name resolution complete. Valid items: {len(name_map)}")
    return name_map


def fetch_all_category_ids(session, ac: AdaptiveConcurrency) -> list:
    url = f"{ESI_BASE}/universe/categories/?datasource={DATASOURCE}"
    try:
        r = session.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning(f"Failed to fetch category IDs: {e}")
    return []

def fetch_category_details(session, cat_ids: list, ac: AdaptiveConcurrency) -> list:
    logger.info(f"Fetching {len(cat_ids)} categories...")
    results = []
    lock = threading.Lock()

    def fetch_one(cid):
        try:
            r = session.get(f"{ESI_BASE}/universe/categories/{cid}/?datasource={DATASOURCE}&language=zh", timeout=10)
            if r.status_code == 200:
                ac.record(True)
                return r.json()
            ac.record(False)
        except Exception:
            ac.record(False)
        return None

    with ThreadPoolExecutor(max_workers=ac.current_workers) as executor:
        futures = {executor.submit(fetch_one, cid): cid for cid in cat_ids}
        for future in as_completed(futures):
            data = future.result()
            if data:
                with lock:
                    results.append(data)
    logger.info(f"Categories fetched: {len(results)}/{len(cat_ids)}")
    return results


def fetch_all_group_ids(session, ac: AdaptiveConcurrency) -> list:
    url = f"{ESI_BASE}/universe/groups/?datasource={DATASOURCE}"
    all_ids = []
    try:
        r = session.get(url, timeout=10)
        r.raise_for_status()
        total_pages = int(r.headers.get('X-Pages', 1))
        all_ids.extend(r.json())

        def fetch_page(page):
            try:
                resp = session.get(f"{url}&page={page}", timeout=15)
                if resp.status_code == 200:
                    ac.record(True)
                    return resp.json()
                ac.record(False)
            except Exception:
                ac.record(False)
            return []

        if total_pages > 1:
            with ThreadPoolExecutor(max_workers=ac.current_workers) as executor:
                futures = {executor.submit(fetch_page, p): p for p in range(2, total_pages + 1)}
                for future in as_completed(futures):
                    all_ids.extend(future.result())
    except Exception as e:
        logger.warning(f"Failed to fetch group IDs: {e}")
    logger.info(f"Group IDs fetched: {len(all_ids)}")
    return all_ids


def fetch_group_details(session, group_ids: list, ac: AdaptiveConcurrency) -> list:
    logger.info(f"Fetching {len(group_ids)} groups...")
    results = []
    lock = threading.Lock()
    completed_count = [0]

    def fetch_one(gid):
        try:
            r = session.get(f"{ESI_BASE}/universe/groups/{gid}/?datasource={DATASOURCE}&language=zh", timeout=10)
            if r.status_code == 200:
                ac.record(True)
                return r.json()
            ac.record(False)
        except Exception:
            ac.record(False)
        return None

    total = len(group_ids)
    with ThreadPoolExecutor(max_workers=ac.current_workers) as executor:
        futures = {executor.submit(fetch_one, gid): gid for gid in group_ids}
        for future in as_completed(futures):
            data = future.result()
            with lock:
                completed_count[0] += 1
                if data:
                    results.append(data)
            if completed_count[0] % 200 == 0:
                logger.info(f"Group detail progress: {completed_count[0]}/{total} | {ac.status()}")
    logger.info(f"Groups fetched: {len(results)}/{total}")
    return results


def fetch_type_details_parallel(session, type_ids: list, ac: AdaptiveConcurrency) -> list:
    """并行拉取物品详情，支持自适应降级。"""
    logger.info(f"[4/4] Fetching details for {len(type_ids)} new items...")
    ac.reset()

    results = {}
    lock = threading.Lock()
    completed_count = [0]

    def fetch_one(tid):
        url = f"{ESI_BASE}/universe/types/{tid}/?datasource={DATASOURCE}&language=zh"
        try:
            r = session.get(url, timeout=10)
            if r.status_code == 200:
                ac.record(True)
                return tid, r.json()
            ac.record(False)
        except Exception:
            ac.record(False)
        return tid, None

    total = len(type_ids)
    with ThreadPoolExecutor(max_workers=ac.current_workers) as executor:
        futures = {executor.submit(fetch_one, tid): tid for tid in type_ids}
        for future in as_completed(futures):
            tid, detail = future.result()
            if detail:
                with lock:
                    results[tid] = detail
            with lock:
                completed_count[0] += 1
                cnt = completed_count[0]
            if cnt % 100 == 0:
                logger.info(f"Detail progress: {cnt}/{total} | {ac.status()}")

    logger.info(f"Detail fetch complete. Success: {len(results)}/{total}")
    return list(results.values())


def main(args=None):
    if args is None:
        parser = argparse.ArgumentParser(description="EVE Universe Sync Script")
        parser.add_argument("--server", choices=["serenity", "tranquility", "infinity"], default="serenity")
        parser.add_argument(
            "--threads", type=int, default=50,
            help="初始最大线程数（失败率高时会自动降级，默认 50）"
        )
        parser.add_argument(
            "--min-threads", type=int, default=2,
            help="自动降级的最低线程数下限（默认 2）"
        )
        parser.add_argument(
            "--fail-rate", type=float, default=0.3,
            help="触发降级的失败率阈值，0~1 之间（默认 0.3）"
        )
        args = parser.parse_args()

    setup_environment(args.server)

    start_time = time.time()
    logger.info(f"=== Universe Sync Started: {args.server} | 初始线程={args.threads} | 最低线程={args.min_threads} | 降级阈值={args.fail_rate:.0%} ===")

    ac = AdaptiveConcurrency(
        initial_workers=args.threads,
        min_workers=args.min_threads,
        failure_threshold=args.fail_rate,
    )

    session = get_retry_session()
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("CREATE TABLE IF NOT EXISTS invTypes (typeID INTEGER PRIMARY KEY, groupID INTEGER, typeName TEXT, typeName_en TEXT, volume REAL, mass REAL, description TEXT, source TEXT, pinyinFull TEXT, pinyinInitials TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS invCategories (categoryID INTEGER PRIMARY KEY, categoryName TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS invGroups (groupID INTEGER PRIMARY KEY, categoryID INTEGER, groupName TEXT)")
        conn.commit()
        
        cursor.execute("SELECT typeID, typeName FROM invTypes")
        local_db_map = {row['typeID']: row['typeName'] for row in cursor.fetchall()}
        logger.info(f"[0/4] Local DB loaded: {len(local_db_map)} records.")
    except Exception as e:
        logger.error(f"DB init failed: {e}")
        return

    # === 同步 invCategories ===
    try:
        cat_ids = fetch_all_category_ids(session, ac)
        if cat_ids:
            ac.reset()
            cat_details = fetch_category_details(session, cat_ids, ac)
            cat_rows = [(d['category_id'], d.get('name', 'Unknown')) for d in cat_details if d]
            if cat_rows:
                cursor.executemany("INSERT OR REPLACE INTO invCategories VALUES (?,?)", cat_rows)
                conn.commit()
                logger.info(f"invCategories synced: {len(cat_rows)} rows.")
    except Exception as e:
        logger.warning(f"invCategories sync failed: {e}")

    # === 同步 invGroups ===
    try:
        group_ids = fetch_all_group_ids(session, ac)
        if group_ids:
            ac.reset()
            group_details = fetch_group_details(session, group_ids, ac)
            grp_rows = [(d['group_id'], d.get('category_id'), d.get('name', 'Unknown')) for d in group_details if d]
            if grp_rows:
                cursor.executemany("INSERT OR REPLACE INTO invGroups VALUES (?,?,?)", grp_rows)
                conn.commit()
                logger.info(f"invGroups synced: {len(grp_rows)} rows.")
    except Exception as e:
        logger.warning(f"invGroups sync failed: {e}")

    # === 同步 invTypes ===
    esi_ids = fetch_all_esi_ids(session, ac)
    if not esi_ids: return
    
    esi_name_map = fetch_names_bulk(session, esi_ids, ac)

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
        details = fetch_type_details_parallel(session, to_insert_ids, ac)

        insert_buffer = []
        for detail in details:
            name = detail.get('name', 'Unknown')
            py_full, py_first = get_pinyin_data(name)
            insert_buffer.append((
                detail['type_id'], detail.get('group_id'), name, None,
                detail.get('volume', 0.0), detail.get('mass', 0.0),
                detail.get('description', ''), 'ESI_NEW', py_full, py_first
            ))

        if insert_buffer:
            cursor.executemany(
                'INSERT OR REPLACE INTO invTypes (typeID, groupID, typeName, typeName_en, volume, mass, description, source, pinyinFull, pinyinInitials) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                insert_buffer
            )
            conn.commit()
            logger.info(f"Insert complete. {len(insert_buffer)} records written.")

    conn.close()
    logger.info(f"=== Sync Finished in {time.time() - start_time:.2f}s ===")


if __name__ == "__main__":
    main()

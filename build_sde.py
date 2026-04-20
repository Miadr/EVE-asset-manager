import os
import sqlite3
import yaml
import time
from pypinyin import pinyin, lazy_pinyin, Style

def get_pinyin_data(text):
    if not text: return "", ""
    try:
        full_str = "".join(lazy_pinyin(text))
        first_list = pinyin(text, style=Style.FIRST_LETTER)
        first_str = "".join([x[0] for x in first_list])
        return full_str, first_str
    except:
        return "", ""

def load_yaml(filepath):
    try:
        from yaml import CSafeLoader as Loader
    except ImportError:
        from yaml import SafeLoader as Loader
    print(f"Loading {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        return yaml.load(f, Loader=Loader)

def init_db(db_path):
    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE invCategories (categoryID INTEGER PRIMARY KEY, categoryName TEXT)")
    cursor.execute("CREATE TABLE invGroups (groupID INTEGER PRIMARY KEY, categoryID INTEGER, groupName TEXT)")
    cursor.execute("CREATE TABLE invTypes (typeID INTEGER PRIMARY KEY, groupID INTEGER, typeName TEXT, typeName_en TEXT, volume REAL, mass REAL, description TEXT, source TEXT, pinyinFull TEXT, pinyinInitials TEXT)")
    cursor.execute("CREATE TABLE mapSolarSystems (solarSystemID INTEGER PRIMARY KEY, regionID INTEGER, solarSystemName TEXT)")
    cursor.execute("CREATE TABLE staStations (stationID INTEGER PRIMARY KEY, solarSystemID INTEGER, stationName TEXT)")
    conn.commit()
    return conn

def get_name(data_node, lang='zh', fallback='en'):
    if not data_node: return "Unknown"
    if 'name' in data_node:
        names = data_node['name']
        if not names: return "Unknown"
        if lang in names: return names[lang]
        if fallback in names: return names[fallback]
    return "Unknown"

def main():
    start = time.time()
    sde_dir = 'eve-online-static-data-3304841-yaml'
    db_path = 'data/eve_universe_tranquility.sqlite'
    
    conn = init_db(db_path)
    cursor = conn.cursor()
    
    # 1. Categories
    try:
        cat_data = load_yaml(os.path.join(sde_dir, 'categories.yaml'))
        cat_inserts = []
        for cat_id, cat_info in cat_data.items():
            name = get_name(cat_info, lang='en')
            cat_inserts.append((cat_id, name))
        cursor.executemany("INSERT INTO invCategories VALUES (?,?)", cat_inserts)
        conn.commit()
        print(f"Inserted {len(cat_inserts)} categories.")
    except Exception as e: print(f"Categories error: {e}")
    
    # 2. Groups
    try:
        group_data = load_yaml(os.path.join(sde_dir, 'groups.yaml'))
        group_inserts = []
        for grp_id, grp_info in group_data.items():
            cat_id = grp_info.get('categoryID', 0)
            name = get_name(grp_info, lang='en')
            group_inserts.append((grp_id, cat_id, name))
        cursor.executemany("INSERT INTO invGroups VALUES (?,?,?)", group_inserts)
        conn.commit()
        print(f"Inserted {len(group_inserts)} groups.")
    except Exception as e: print(f"Groups error: {e}")
    
    # 3. Solar Systems
    try:
        sys_data = load_yaml(os.path.join(sde_dir, 'mapSolarSystems.yaml'))
        sys_inserts = []
        for sys_id, sys_info in sys_data.items():
            region_id = sys_info.get('regionID', 0)
            name = sys_info.get('name', str(sys_id))
            if isinstance(sys_info.get('name'), dict):
                name = get_name(sys_info)
            sys_inserts.append((sys_id, region_id, name))
        cursor.executemany("INSERT INTO mapSolarSystems VALUES (?,?,?)", sys_inserts)
        conn.commit()
        print(f"Inserted {len(sys_inserts)} solar systems.")
    except Exception as e: print(f"Solar Systems error: {e}")
    
    # 4. Types (Heavy!)
    try:
        types_data = load_yaml(os.path.join(sde_dir, 'types.yaml'))
        types_inserts = []
        count = 0
        for tid, tinfo in types_data.items():
            group_id = tinfo.get('groupID', 0)
            name_zh = get_name(tinfo, lang='zh')
            name_en = get_name(tinfo, lang='en')
            vol = tinfo.get('volume', 0.0)
            mass = tinfo.get('mass', 0.0)
            desc_val = tinfo.get('description', '')
            desc = desc_val.get('zh', desc_val.get('en', '')) if isinstance(desc_val, dict) else str(desc_val)
            py_full, py_init = get_pinyin_data(name_zh)
            types_inserts.append((tid, group_id, name_zh, name_en, vol, mass, desc, 'sde', py_full, py_init))
            
            count += 1
            if count % 5000 == 0:
                print(f"  ...processed {count} types")
                cursor.executemany("INSERT INTO invTypes VALUES (?,?,?,?,?,?,?,?,?,?)", types_inserts)
                types_inserts = []
                conn.commit()
                
        if types_inserts:
            cursor.executemany("INSERT INTO invTypes VALUES (?,?,?,?,?,?,?,?,?,?)", types_inserts)
            conn.commit()
        print(f"Inserted {count} types.")
    except Exception as e: print(f"Types error: {e}")
    
    conn.close()
    print(f"SDE Compilation completed in {time.time() - start:.1f} seconds! Database ready at {db_path}.")

if __name__ == '__main__':
    main()

import os
import sqlite3
import json
from typing import Dict, Any, List


DEFAULT_DB = os.path.join(os.path.dirname(__file__), 'data', 'basestation.db')


def _open_conn(db_path: str = DEFAULT_DB):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA foreign_keys=ON;')
    return conn


def init_db_from_config(config_path: str, db_path: str = DEFAULT_DB):
    """Create tables for every node listed in the JSON config file.
    Each node gets its own table named exactly as the node id (safe chars assumed).
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(config_path)

    with open(config_path, 'r') as f:
        cfg = json.load(f)

    conn = _open_conn(db_path)
    cur = conn.cursor()

    # Generic schema for time-series sensor records
    for node_id in cfg.keys():
        table = node_id
        cur.execute(f"""
        CREATE TABLE IF NOT EXISTS "{table}" (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER,
            seq INTEGER,
            mac TEXT,
            ts INTEGER,
            t REAL,
            h REAL,
            p REAL,
            q INTEGER,
            eco2 INTEGER,
            tvoc INTEGER,
            mx REAL,
            my REAL,
            mz REAL,
            a REAL,
            payload TEXT,
            received_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # Index for faster time-range queries
        cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_ts ON "{table}"(ts);')

    conn.commit()
    return conn


def insert_record(node_id: str, record: Dict[str, Any], db_path: str = DEFAULT_DB):
    conn = _open_conn(db_path)
    cur = conn.cursor()
    # extract fields with safe defaults
    device_id = record.get('id')
    seq = record.get('seq')
    mac = record.get('mac')
    ts = record.get('ts')
    t = record.get('t')
    h = record.get('h')
    p = record.get('p')
    q = record.get('q')
    eco2 = record.get('eco2')
    tvoc = record.get('tvoc')
    mx = record.get('mx')
    my = record.get('my')
    mz = record.get('mz')
    a = record.get('a')

    payload = json.dumps(record)
    cur.execute(f'INSERT INTO "{node_id}" (device_id, seq, mac, ts, t, h, p, q, eco2, tvoc, mx, my, mz, a, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (device_id, seq, mac, ts, t, h, p, q, eco2, tvoc, mx, my, mz, a, payload))
    conn.commit()
    conn.close()


def query_recent(node_id: str, limit: int = 100, db_path: str = DEFAULT_DB) -> List[Dict[str, Any]]:
    if not os.path.exists(db_path):
        return []
    conn = _open_conn(db_path)
    cur = conn.cursor()
    cur.execute(f'SELECT rowid, device_id, seq, mac, ts, t, h, p, q, eco2, tvoc, mx, my, mz, a, payload, received_at FROM "{node_id}" ORDER BY ts DESC NULLS LAST, rowid DESC LIMIT ?', (limit,))
    rows = cur.fetchall()
    cols = ['rowid', 'device_id', 'seq', 'mac', 'ts', 't', 'h', 'p', 'q', 'eco2', 'tvoc', 'mx', 'my', 'mz', 'a', 'payload', 'received_at']
    results = []
    for r in rows:
        obj = dict(zip(cols, r))
        # try to parse payload JSON for convenience
        try:
            obj['payload'] = json.loads(obj['payload'])
        except Exception:
            pass
        results.append(obj)
    conn.close()
    return results

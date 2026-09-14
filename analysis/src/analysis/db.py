"""SQLite 存储层：environment / measurements 表（spec §6 数据契约）。"""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS environment (
  ts        TEXT NOT NULL,          -- ISO8601
  zone      TEXT NOT NULL,          -- 库房/分区标识
  temp_c    REAL,
  rh_pct    REAL,
  co2_ppm   REAL,
  light_lux REAL,
  PRIMARY KEY (ts, zone)
);

CREATE TABLE IF NOT EXISTS measurements (
  ts          TEXT NOT NULL,
  box_id      TEXT NOT NULL,
  n           INTEGER,
  mean_len_mm REAL,
  mean_cap_mm REAL,
  p10_len     REAL,
  p90_len     REAL,
  quality     TEXT,
  PRIMARY KEY (ts, box_id)
);

CREATE TABLE IF NOT EXISTS rounds (
  ts         TEXT PRIMARY KEY,
  ok         INTEGER,
  n_results  INTEGER,
  n_failures INTEGER,
  aborted    INTEGER,
  -- 库房维度（2026-09-13 补）：daemon 每条 round 行都带这三个字段，但表里原先没有，
  -- 于是"这一轮是哪间库房、哪批蘑菇"在 /ingest 这一跳被静默丢掉。
  room_id    TEXT,
  entry_date TEXT,
  batch_no   TEXT
);

CREATE TABLE IF NOT EXISTS images (
  ts            TEXT NOT NULL,       -- 采图时刻（ISO8601，来自索引记录）
  station_id    TEXT,
  box_id        TEXT,
  angle_profile TEXT,
  camera_ip     TEXT,
  y             REAL,
  z             REAL,
  object_name   TEXT,                -- MinIO 对象名；失败行为 NULL
  cloud_url     TEXT,
  ok            INTEGER,
  error         TEXT,
  -- 库房维度（2026-09-13 补）：`patrol.daemon` 给每条索引行都盖了
  -- `room_id`/`entry_date`/`batch_no`（见 daemon._room_fields 的说明：多库房/多批次
  -- 部署时"这张照片属于哪一间、哪一批"必须跟着行本身走），但表里原先没有这三列，
  -- 写入时被静默丢弃 —— 照片在 MinIO 里、索引在库里，却再也说不清属于哪间库房。
  room_id       TEXT,
  entry_date    TEXT,
  batch_no      TEXT,
  -- 主键取 (ts, 站位, 角度档) 而不是 ADR-0005 举例的 (ts, object_name)：失败行没有
  -- object_name，用含 NULL 的列做主键会让幂等语义在 SQLite 上变得含糊。
  PRIMARY KEY (ts, station_id, angle_profile)
);

CREATE TABLE IF NOT EXISTS ranges (
  stage        TEXT NOT NULL,      -- 生长阶段（按菇体长度分段）
  param        TEXT NOT NULL,      -- temp_c / rh_pct / co2_ppm / light_lux
  lo           REAL,
  hi           REAL,
  rate_mm_per_h REAL,
  n            INTEGER,
  computed_at  TEXT NOT NULL,
  PRIMARY KEY (stage, param)
);
"""


def connect(db_path: str, *, check_same_thread: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


#: 建表语句之后**追加**过的列（表已存在时 `CREATE TABLE IF NOT EXISTS` 不会补列）。
#: 现场库是长期存在的，所以这里必须能就地升级——否则老库会一直是"缺列"的样子，
#: 而写入方（daemon）并不知道，只会静默丢字段。
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "images": {"room_id": "TEXT", "entry_date": "TEXT", "batch_no": "TEXT"},
    "rounds": {"room_id": "TEXT", "entry_date": "TEXT", "batch_no": "TEXT"},
}


def migrate(conn: sqlite3.Connection) -> list[str]:
    """把老库补到当前 schema，返回补了哪些列（``["images.room_id", …]`）。

    只做**加列**：SQLite 的 `ALTER TABLE ADD COLUMN` 对已有行填 NULL，
    历史行就保持"库房未知"——这比编一个默认值诚实。幂等，每次启动跑一遍。
    """
    added: list[str] = []
    for table, columns in ADDED_COLUMNS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                added.append(f"{table}.{name}")
    if added:
        conn.commit()
    return added


def upsert_environment(conn: sqlite3.Connection, rows: list[dict]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO environment (ts, zone, temp_c, rh_pct, co2_ppm, light_lux) "
        "VALUES (:ts, :zone, :temp_c, :rh_pct, :co2_ppm, :light_lux)",
        rows,
    )
    conn.commit()


MEASUREMENT_FIELDS = (
    "ts", "box_id", "n", "mean_len_mm", "mean_cap_mm", "p10_len", "p90_len", "quality",
)


def insert_measurements(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """行允许缺字段（按 NULL 入库）；字段名以 MEASUREMENT_FIELDS 为准。"""
    conn.executemany(
        "INSERT OR REPLACE INTO measurements (ts, box_id, n, mean_len_mm, mean_cap_mm, "
        "p10_len, p90_len, quality) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [[row.get(f) for f in MEASUREMENT_FIELDS] for row in rows],
    )
    conn.commit()


def fetch_measurements(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT ts, box_id, n, mean_len_mm, mean_cap_mm, p10_len, p90_len, quality "
        "FROM measurements ORDER BY ts"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


IMAGE_FIELDS = (
    "ts", "station_id", "box_id", "angle_profile", "camera_ip",
    "y", "z", "object_name", "cloud_url", "ok", "error",
    # 库房维度：与 daemon 写入的行一一对应（见 SCHEMA 里 images 的注释）
    "room_id", "entry_date", "batch_no",
)


def flatten_image(row: dict) -> dict:
    """patrol 的索引记录把坐标放在 ``yz`` 列表里，这里摊平成 ``y`` / ``z`` 两列。"""
    yz = row.get("yz") or ()
    flat = {f: row.get(f) for f in IMAGE_FIELDS}
    flat["y"] = yz[0] if len(yz) > 0 else None
    flat["z"] = yz[1] if len(yz) > 1 else None
    return flat


def insert_images(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """图像索引（ADR-0005）：一帧一行。行允许缺字段（按 NULL 入库）。

    用**命名**占位符而不是位置列表：`IMAGE_FIELDS` 是唯一的一处字段清单，
    加列时不会出现"SQL 里少写一个 ?、值整体错位"这种最难查的错。
    """
    cols = ", ".join(IMAGE_FIELDS)
    marks = ", ".join(f":{f}" for f in IMAGE_FIELDS)
    conn.executemany(
        f"INSERT OR REPLACE INTO images ({cols}) VALUES ({marks})",
        [flatten_image(row) for row in rows],
    )
    conn.commit()


def fetch_images(conn: sqlite3.Connection, *, station_id: str | None = None,
                 box_id: str | None = None, room_id: str | None = None,
                 limit: int | None = None) -> list[dict]:
    """按站位/框/库房（都可选）倒序取图像索引，供巡检台的历史模式使用（ADR-0003/0005）。"""
    sql = f"SELECT {', '.join(IMAGE_FIELDS)} FROM images"
    where: list[str] = []
    params: list = []
    if station_id:
        where.append("station_id = ?")
        params.append(station_id)
    if box_id:
        where.append("box_id = ?")
        params.append(box_id)
    if room_id:
        where.append("room_id = ?")
        params.append(room_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def replace_ranges(conn: sqlite3.Connection, rows: list[dict], computed_at: str) -> None:
    """批处理结果整体替换（评审 #6：区间表化，API 只读）。"""
    conn.execute("DELETE FROM ranges")
    conn.executemany(
        "INSERT INTO ranges (stage, param, lo, hi, rate_mm_per_h, n, computed_at) "
        "VALUES (:stage, :param, :lo, :hi, :rate_mm_per_h, :n, :computed_at)",
        [{**r, "computed_at": computed_at} for r in rows],
    )
    conn.commit()


def fetch_ranges(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT stage, param, lo, hi, rate_mm_per_h, n, computed_at FROM ranges "
        "ORDER BY stage, param"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]

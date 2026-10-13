"""SQLite 存储层：environment / measurements 表（spec §6 数据契约）。"""

from __future__ import annotations

import sqlite3

SCHEMA = """
-- 环境表存的是**小时聚合**，不是原始分钟序列（2026-10-12 定）。
-- 原始序列的权威在 TDengine，需要分钟级时按窗口现取（`GET /environment?granularity=raw`）；
-- 库里只留界面与分析真正要用的指标，69k 行原始点里绝大部分是没人看的。
CREATE TABLE IF NOT EXISTS environment (
  ts         TEXT NOT NULL,          -- 整点（本地时间，与 rounds/images 同口径）
  zone       TEXT NOT NULL,          -- 库房号（611/612/607/608），不是自由文本
  temp_c     REAL, temp_min REAL, temp_max REAL,
  rh_pct     REAL, rh_min   REAL, rh_max   REAL,
  co2_ppm    REAL, co2_min  REAL, co2_max  REAL,
  -- 补光灯**设备状态**：0=自动 1=手动 2=停止 3=关闭（ADR-0021）。分类量，取该小时众数，
  -- 不是"灯亮没亮"。
  lamp_state REAL,
  -- 循环补光：开 OnSSet 秒 / 停 OffSSet 秒（该小时最后一次设定），
  -- lamp_on_min = 该小时采样点数 × 占空比 ——"点亮分钟"是**设定量**，不是照度（ADR-0021）。
  lamp_on_s   REAL,
  lamp_off_s  REAL,
  lamp_on_m   REAL,
  lamp_off_m  REAL,
  lamp_on_min REAL,
  n          INTEGER,                -- 该小时实际采样点数：60 = 完整，不足即断档
  PRIMARY KEY (ts, zone)
);

-- 测量表（票 11）：生产者是 measure（`measure/record.py` 的 `RECORD_FIELDS`），
-- analysis 只消费，不复制字段表。主键 (ts, room_id, box_id) —— 缺库房维度会让两个库房
-- 同一个框号的行互相覆盖，于是 align 按小时聚合时把库房混算，区间表算出来是**错的**，
-- 不是空的。
-- `room_id` 必须 NOT NULL：主键里含 NULL 列时 SQLite 认为"每行都不同"，
-- 幂等（重发覆盖而不是新增）当场失效。
CREATE TABLE IF NOT EXISTS measurements (
  ts            TEXT NOT NULL,       -- 采图/测量时刻（本地 ISO8601，与 rounds/images 同口径）
  room_id       TEXT NOT NULL,       -- 库房号（611/612/607/608）；由生产者填，不许从 box_id 反推
  box_id        TEXT NOT NULL,       -- **菇框号**（B105 = 第 1 层第 5 框），不是站位号
  station_id    TEXT,                -- 站位号（S105）；将来与 images 的 (ts, station_id, angle_profile) join
  angle_profile TEXT,                -- 角度档；当前单档平视 level0。多档并存时主键要加它（需重建）
  n             INTEGER,             -- 本行有效检出数
  mean_len_mm   REAL,                -- 平视单档下**明确缺测**（无对应角度档）→ NULL，不写 0
  mean_cap_mm   REAL,                -- 同上
  p10_len       REAL,
  p90_len       REAL,
  quality       TEXT,                -- 逗号 flag 串；区间表与决策层只吃含 verified 的行
  PRIMARY KEY (ts, room_id, box_id)
);

CREATE TABLE IF NOT EXISTS rounds (
  ts         TEXT PRIMARY KEY,
    round_id   TEXT,
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
    round_id      TEXT,
  station_id    TEXT,
  box_id        TEXT,
  angle_profile TEXT,
  -- 成像参数快照 id（2026-10-13，巡检手票 11）：指向归档的 `configs/imaging-profile.yaml`
  -- 里某个快照，**只存 id，不塞参数全文**。老行 NULL = "那时还没记"，不编。
  imaging_profile TEXT,
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

-- 操作日志：环控平台的命令审计（谁、何时、把哪个点改成什么）。
-- **逐条留，不聚合**：一天几十条，事件本身就是最小有意义单位（见 analysis/audit.py 顶部）。
CREATE TABLE IF NOT EXISTS operations (
  ts          TEXT NOT NULL,       -- 操作时刻（本地 ISO8601）
  device_code TEXT NOT NULL,
  point_code  TEXT NOT NULL,
  zone        TEXT,                -- 库房号，由设备号前缀推出
  device_name TEXT,
  point_name  TEXT,
  value       TEXT,                -- 改成的值（字符串：可能是枚举/开关量）
  operator    TEXT,
  source      TEXT,                -- 现场 / 远程 …
  content     TEXT,                -- 平台给的一句话（"加湿开启设定[5]"）
  PRIMARY KEY (ts, device_code, point_code)
);

-- 批次台账（来自生产库 `mogu.mo_gu_batch`）。批次边界只能来自这里：
-- 环境数据是连续采集的，换批不会让传感器停，"按断档切段"在真机上被证否（2026-10-12）。
CREATE TABLE IF NOT EXISTS batches (
  room_id    TEXT NOT NULL,
  entry_date TEXT NOT NULL,        -- 入库日期 YYYY-MM-DD
  batch_no   TEXT,                 -- mogu-<id>
  batch_id   TEXT,
  PRIMARY KEY (room_id, entry_date)
);

CREATE TABLE IF NOT EXISTS ranges (
  stage        TEXT NOT NULL,      -- 生长阶段（按菇体长度分段）
  param        TEXT NOT NULL,      -- temp_c / rh_pct / co2_ppm / lamp_on_min
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
    "images": {
        "round_id": "TEXT",
        "room_id": "TEXT",
        "entry_date": "TEXT",
        "batch_no": "TEXT",
        # 成像参数快照 id（巡检手票 11）：老行留 NULL——"那时没记"比编一个 id 诚实。
        "imaging_profile": "TEXT",
    },
    "rounds": {
        "round_id": "TEXT",
        "room_id": "TEXT",
        "entry_date": "TEXT",
        "batch_no": "TEXT",
    },
    # 循环补光（2026-10-12）：加列即可，不用重建——老行这三列留空表示"那时还没采这个量"，
    # 比编一个 0 诚实（0 会被读成"补光为零"）。
    "environment": {
        "lamp_on_s": "REAL",
        "lamp_off_s": "REAL",
        "lamp_on_m": "REAL",
        "lamp_off_m": "REAL",
        "lamp_on_min": "REAL",
    },
    # 测量表的归属列（票 11）。`room_id` 不在这里 —— 它在主键里，只能靠重建补上
    # （见 `_rebuild_measurements`）。这两列是主键之外的，可以直接 ADD COLUMN。
    "measurements": {
        "station_id": "TEXT",
        "angle_profile": "TEXT",
    },
}


#: 改过名的列。老库是长期存在的，改名必须就地做（SQLite ≥ 3.25 支持 RENAME COLUMN）。
#: `environment.light_lux` 当时是空表，改名不动任何数据（ADR-0021）。
RENAMED_COLUMNS: dict[str, dict[str, str]] = {
    "environment": {"light_lux": "lamp_state"},
}


def _rename_columns(conn: sqlite3.Connection) -> list[str]:
    renamed: list[str] = []
    for table, mapping in RENAMED_COLUMNS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for old, new in mapping.items():
            if old in have and new not in have:
                conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
                renamed.append(f"{table}.{old}→{new}")
    if renamed:
        conn.commit()
    return renamed


def _rebuild_environment(conn: sqlite3.Connection) -> list[str]:
    """老库的 environment 是**逐分钟原始行**（2026-10-12 之前），现在只留小时聚合。

    两种形状加列兼容不了（原始行没有 min/max，聚合行也不该混进原始点），所以直接重建。
    敢 drop 的理由：这张表里全是**可再生的派生数据**，原始序列在 TDengine，重跑一次
    ingest 就回来了。重建后立刻按新 schema 建表。"""
    have = {row[1] for row in conn.execute("PRAGMA table_info(environment)")}
    if not have or {"temp_min", "n"} <= have:
        return []
    conn.execute("DROP TABLE environment")
    conn.executescript(SCHEMA)
    conn.commit()
    return ["environment(重建：原始分钟行 → 小时聚合)"]


def _rebuild_measurements(conn: sqlite3.Connection) -> list[str]:
    """老库的 `measurements` 没有 `room_id`，主键也还是 `(ts, box_id)`（票 11）。

    主键是表级约束，SQLite 改不了，只能重建。重建时**绝不编库房号** ——
    加列之前采的行没有库房信息，编一个就是往区间表里写假归属，比丢行更坏。

    - 表为空（生产 2026-10-12 实测 0 行，spec §7.2）：直接按新 schema 重建。
    - 表里有行：**整表原样搬进 `measurements_legacy`**，新表按新 schema 建。
      行不丢、字段不改名，谁要考古自己去查那张表。
    """
    have = {row[1] for row in conn.execute("PRAGMA table_info(measurements)")}
    if not have or "room_id" in have:
        return []
    n = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
    if n:
        conn.execute("DROP TABLE IF EXISTS measurements_legacy")
        conn.execute("ALTER TABLE measurements RENAME TO measurements_legacy")
        note = f"measurements(重建：{n} 行无库房维度 → measurements_legacy)"
    else:
        conn.execute("DROP TABLE measurements")
        note = "measurements(重建：老主键 (ts, box_id) → (ts, room_id, box_id))"
    conn.executescript(SCHEMA)
    conn.commit()
    return [note]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """把老库补到当前 schema，返回补了哪些列（``["images.room_id", …]`）。

    只做**加列**：SQLite 的 `ALTER TABLE ADD COLUMN` 对已有行填 NULL，
    历史行就保持"库房未知"——这比编一个默认值诚实。幂等，每次启动跑一遍。
    """
    added: list[str] = []
    added.extend(_rebuild_environment(conn))
    added.extend(_rebuild_measurements(conn))
    added.extend(_rename_columns(conn))
    for table, columns in ADDED_COLUMNS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue      # 表还不存在：建表阶段会按当前 schema 建，这里不用补列
        for name, ddl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                added.append(f"{table}.{name}")
    if added:
        conn.commit()
    return added


def upsert_environment(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """按 (ts, zone) 覆盖写。行允许缺字段（按 NULL 入库）——同一小时的聚合会随
    新采样到达被重算并覆盖，所以键在 (ts, zone) 上而不是追加。"""
    cols = ", ".join(ENVIRONMENT_FIELDS)
    marks = ", ".join(f":{f}" for f in ENVIRONMENT_FIELDS)
    conn.executemany(
        f"INSERT OR REPLACE INTO environment ({cols}) VALUES ({marks})",
        [{f: row.get(f) for f in ENVIRONMENT_FIELDS} for row in rows],
    )
    conn.commit()


ENVIRONMENT_FIELDS = (
    "ts", "zone",
    "temp_c", "temp_min", "temp_max",
    "rh_pct", "rh_min", "rh_max",
    "co2_ppm", "co2_min", "co2_max",
    "lamp_state", "lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m",
    "lamp_on_min", "n",
)


def fetch_environment(
    conn: sqlite3.Connection,
    *,
    zone: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """按库房与时间窗取实测序列（升序）。时间窗是含端点的 ISO8601 前缀比较。"""
    sql = f"SELECT {', '.join(ENVIRONMENT_FIELDS)} FROM environment"
    where: list[str] = []
    params: list = []
    for field, value, op in (("zone", zone, "="), ("ts", start, ">="), ("ts", end, "<=")):
        if value:
            where.append(f"{field} {op} ?")
            params.append(value)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts"
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


OPERATION_FIELDS = (
    "ts", "zone", "device_code", "device_name", "point_code", "point_name",
    "value", "operator", "source", "content",
)


def upsert_operations(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """同一 (时刻, 设备, 点位) 覆盖写：重拉同一窗口不会翻倍。"""
    cols = ", ".join(OPERATION_FIELDS)
    marks = ", ".join(f":{f}" for f in OPERATION_FIELDS)
    conn.executemany(
        f"INSERT OR REPLACE INTO operations ({cols}) VALUES ({marks})",
        [{f: row.get(f) for f in OPERATION_FIELDS} for row in rows],
    )
    conn.commit()


def fetch_operations(
    conn: sqlite3.Connection,
    *,
    zone: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """按库房与时间窗取操作日志，最新在前。"""
    sql = f"SELECT {', '.join(OPERATION_FIELDS)} FROM operations"
    where: list[str] = []
    params: list = []
    for field, value, op in (("zone", zone, "="), ("ts", start, ">="), ("ts", end, "<=")):
        if value:
            where.append(f"{field} {op} ?")
            params.append(value)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC, device_code"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


BATCH_FIELDS = ("room_id", "entry_date", "batch_no", "batch_id")


def upsert_batches(conn: sqlite3.Connection, rows: list[dict]) -> None:
    cols = ", ".join(BATCH_FIELDS)
    marks = ", ".join(f":{f}" for f in BATCH_FIELDS)
    conn.executemany(
        f"INSERT OR REPLACE INTO batches ({cols}) VALUES ({marks})",
        [{f: row.get(f) for f in BATCH_FIELDS} for row in rows],
    )
    conn.commit()


def fetch_batches(conn: sqlite3.Connection, *, room_id: str | None = None) -> list[dict]:
    sql = f"SELECT {', '.join(BATCH_FIELDS)} FROM batches"
    params: list = []
    if room_id:
        sql += " WHERE room_id = ?"
        params.append(room_id)
    sql += " ORDER BY room_id, entry_date"
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


#: 测量线的字段清单（票 11）。与 measure 侧 `RECORD_FIELDS` **一字不差**，
#: 禁第四套命名；`room_id` 在上游是必填，存储层用 NOT NULL 兜底（缺了就插不进去，
#: 而不是静默丢字段——"写入方多给、存储方少要"这个坑踩过一次）。
MEASUREMENT_FIELDS = (
    "ts", "room_id", "box_id", "station_id", "angle_profile",
    "n", "mean_len_mm", "mean_cap_mm", "p10_len", "p90_len", "quality",
)


def insert_measurements(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """按 (ts, room_id, box_id) 覆盖写。行允许缺字段（按 NULL 入库）。

    用**命名**占位符：`MEASUREMENT_FIELDS` 是唯一字段清单，加列时不会出现
    "SQL 少写一个 ?、值整体错位"这种最难查的错。
    """
    cols = ", ".join(MEASUREMENT_FIELDS)
    marks = ", ".join(f":{f}" for f in MEASUREMENT_FIELDS)
    conn.executemany(
        f"INSERT OR REPLACE INTO measurements ({cols}) VALUES ({marks})",
        [{f: row.get(f) for f in MEASUREMENT_FIELDS} for row in rows],
    )
    conn.commit()


def fetch_measurements(
    conn: sqlite3.Connection,
    *,
    room_id: str | None = None,
    box_id: str | None = None,
) -> list[dict]:
    """取测量行（升序）。可按库房/框号过滤 —— 区间表要按库房分组，不该在内存里筛。"""
    sql = f"SELECT {', '.join(MEASUREMENT_FIELDS)} FROM measurements"
    where: list[str] = []
    params: list = []
    if room_id:
        where.append("room_id = ?")
        params.append(room_id)
    if box_id:
        where.append("box_id = ?")
        params.append(box_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts"
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


ROUND_FIELDS = (
    "ts",
    "round_id",
    "ok",
    "n_results",
    "n_failures",
    "aborted",
    "room_id",
    "entry_date",
    "batch_no",
)

IMAGE_FIELDS = (
    "ts",
    "round_id",
    "station_id",
    "box_id",
    "angle_profile",
    "imaging_profile",
    "camera_ip",
    "y",
    "z",
    "object_name",
    "cloud_url",
    "ok",
    "error",
    # 库房维度：与 daemon 写入的行一一对应（见 SCHEMA 里 images 的注释）
    "room_id",
    "entry_date",
    "batch_no",
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


def fetch_images(
    conn: sqlite3.Connection,
    *,
    station_id: str | None = None,
    box_id: str | None = None,
    room_id: str | None = None,
    round_id: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """按轮次/站位/框/库房（都可选）倒序取图像索引，供历史模式使用。"""
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
    if round_id:
        where.append("round_id = ?")
        params.append(round_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_rounds(
    conn: sqlite3.Connection,
    *,
    room_id: str | None = None,
    entry_date: str | None = None,
    batch_no: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """按库房/入库日期/生产批次筛选巡检轮次，最新轮次优先。"""
    sql = f"SELECT {', '.join(ROUND_FIELDS)} FROM rounds"
    where: list[str] = []
    params: list = []
    for field, value in (
        ("room_id", room_id),
        ("entry_date", entry_date),
        ("batch_no", batch_no),
    ):
        if value:
            where.append(f"{field} = ?")
            params.append(value)
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

"""SQLite 存储层：environment / measurements 表（spec §6 数据契约）。"""

from __future__ import annotations

import json
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
  -- 菌簇范围（2026-10-13 加，测量师票 11）：**平视单档下唯一可用的长势量**。
  -- 盖径/杆长缺测后，生长速率只能从这几个量算（面积或投影径的小时增量），
  -- 见票 05 的口径变更。单位一律 mm / mm²，跨角度档不可比。
  mean_cluster_w_mm    REAL,         -- 菌簇水平跨度均值
  mean_cluster_h_mm    REAL,         -- 菌簇垂直跨度均值
  mean_area_mm2        REAL,         -- 菌簇面积均值
  mean_projected_d_mm  REAL,         -- 投影径均值（**不是菌盖直径**：平视下不许拿它冒充）
  p10_len       REAL,
  p90_len       REAL,
  quality       TEXT,                -- `+` 分隔的 flag 串；区间表与决策层只吃含 verified 的行
  -- 估算证据（票 20，2026-10-14）：平视单档下"估算"是常态，所以"哪些列是估的、
  -- 这个 mm 用的是哪把标尺"必须落进行里 —— 埋在离线 notes 里等于下游看不见。
  -- 枚举与形状见 `analysis/evidence.py`；过滤规则的唯一实现仍在测量侧。
  estimated_fields TEXT,             -- `+` 分隔的**列名清单**，读回是 list（无估算 ⇒ NULL）
  scale_source     TEXT,             -- bag_standard / calibration_target / bag_detected / none
  mm_per_px        REAL,             -- 本行用的 mm/px；可交叉复算 ≈ scale_ref_mm / scale_ref_px
  scale_ref_mm     REAL,             -- 标尺参考物的实物尺寸（mm）
  scale_ref_px     REAL,             -- 同一参考物在帧里的像素尺寸
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
  batch_no   TEXT,
  -- 轮内容完整性（巡检手票 13）：整轮疑似复制 ⇒ `suspect_duplicate`（生产者发布尔 True）
  -- + `dup_groups` = **对象数组**（JSON 文本）
  --   [{"sha256_12": "<12位>", "n": <帧数>, "stations": ["S211", …]}, …]
  --   无重复时 NULL 或 `[]`。**复核没查成** ⇒ 写 `content_check_error`（原因），
  --   **不许当成"已确认无重复"**。
  suspect_duplicate   INTEGER,
  dup_groups          TEXT,                -- JSON 文本：元素是对象，不是字符串（见上）
  content_check_error TEXT,
  -- 轮级计数（2026-10-13，巡检手票 13 §3.2）：重复帧数 / 实际记录帧数。
  n_duplicate         INTEGER,
  n_frames_recorded   INTEGER
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
  -- 轮内容完整性（2026-10-13，巡检手票 13）：一张陈旧帧被复制到多个站位时，
  -- 帧行标 `frame_dup`（生产者发布尔 True ⇒ 库里 0/1）+ 同指纹组 `dup_group`（sha12）；
  -- `content_sha256` 复核到就写。聚合统计（区间表/生长曲线）**不得吃**这些帧
  -- （见 analysis/integrity.py）。
  frame_dup      INTEGER,
  dup_group      TEXT,
  content_sha256 TEXT,
  -- 轮内连续性（2026-10-13，巡检手票 13 §3.2）：这张帧与上一张是否完全相同、
  -- 连续相同了多少张。**是标记，不是删除**：聚合侧照样要排除它们。
  same_as_previous  INTEGER,
  duplicate_streak  INTEGER,
  camera_ip     TEXT,
  y             REAL,
  z             REAL,
  object_name   TEXT,                -- MinIO 对象名；失败行为 NULL
  -- 这个名字是从哪来的（2026-10-13，巡检手加固）：`service` = 抓拍服务返回的 key；
  -- `request` = 服务没回名字，用我们请求时生成的名字兜底。兜底名可能与 MinIO 里的
  -- 实际 key 不一致 —— 那种断链只有把来源记下来才查得动。老行 NULL = "那时还没记"。
  object_name_source TEXT,
  cloud_url     TEXT,
  ok            INTEGER,
  error         TEXT,
  -- 该帧从下发到取回耗时（秒）。生产者一直在发这个键，但表里原先没有 ⇒ 被静默丢。
  -- 按"写入方多给、存储方少要"那条教训，这里补上列而不是继续吞掉（2026-10-13）。
  elapsed_s     REAL,
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

-- 批次台账：**pgsql 并集的物化缓存**（2026-10-14 起走 `postgres_db`/`mushroom_algorithm`；
-- 旧文案写的 `mogu.mo_gu_batch` 是业务库，用户已裁定禁用）。批次边界只能来自台账：
-- 环境数据是连续采集的，换批不会让传感器停，"按断档切段"在真机上被证否（2026-10-12）。
CREATE TABLE IF NOT EXISTS batches (
  room_id    TEXT NOT NULL,        -- **原始**库房号（可能是旧编号 7/8）；join 前用 analysis.rooms 归一
  entry_date TEXT NOT NULL,        -- 入库日期 YYYY-MM-DD
  batch_no   TEXT,                 -- 业务显示名 mogu-<id>；**缺映射就 NULL**（不编值）
  batch_id   TEXT,                 -- 身份 `<规范库房号>|<入库日期>`（ADR-0020）
  PRIMARY KEY (room_id, entry_date)
);

-- 台账**物化缓存的重算状态**（单行表，与 `ranges_state` 同款）。为什么不是给 batches 加列：
-- "最后刷新时间"是**一次作业事件**的属性，加到每行就是把同一事实重复 N 遍，而且老行是 NULL
-- 表达不了"从没刷过"。`GET /batches` 用它给 `ledger_refreshed_at` 与 `stale`。
CREATE TABLE IF NOT EXISTS batches_state (
  id           INTEGER PRIMARY KEY CHECK (id = 1),
  refreshed_at TEXT NOT NULL,       -- 最近一次成功替换的时间（本地 ISO8601）
  n_rows       INTEGER NOT NULL DEFAULT 0,
  n_pruned     INTEGER NOT NULL DEFAULT 0,  -- 本次替换**删掉**的行数（陈旧行自然 prune）
  pruned       TEXT,                -- JSON：被删掉的 `库房号|入库日期` 清单
  detail       TEXT,                -- JSON：来源差异 / 缺映射计数等
  updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ranges (
  stage        TEXT NOT NULL,      -- 生长阶段（按菇体长度分段）
  param        TEXT NOT NULL,      -- temp_c / rh_pct / co2_ppm / lamp_on_min
  lo           REAL,
  hi           REAL,
  -- 速率必须带量纲（Chief 2026-10-13 硬要求）：平视下杆长缺测，长势量可能是
  -- 面积（mm²/h）或投影径（mm/h）。`metric` 是源测量列名，**由它唯一决定量纲**。
  metric       TEXT,               -- mean_area_mm2 / mean_projected_d_mm / mean_len_mm …
  unit         TEXT,               -- mm2/h 或 mm/h
  rate_per_h   REAL,               -- 速率数值（单位见 unit）
  rate_mm_per_h REAL,              -- **只在 unit='mm/h' 时有效**，否则必须 NULL（旧字段，兼容保留）
  n            INTEGER,            -- 本箱的样本数（**本档**的，估算行绝不并进来）
  -- 同一个箱里**两类各几行**（票 26 的"计数分别可见"）：分箱与均值仍只来自本档的 n，
  -- 这两列是**数**出来的。放在同一行上，页面不必去 join 第二次。
  n_verified   INTEGER,
  n_estimate   INTEGER,
  -- 档（票 26 / ADR-0025）：'verified' / 'estimate'。估算档**整维标注「未验证」**，
  -- 且永不与已验证行混算 —— 本表只放**主档**的行（有已验证行时主档就是它），
  -- 估算行另以 n_estimate 计数出现在同一箱里（计数分开可见，统计不混合）。
  -- 老行留 NULL = "那时还没分档"；读侧**一律按未验证算**（见 fetch_ranges）。
  tier         TEXT,
  computed_at  TEXT NOT NULL,
  PRIMARY KEY (stage, param)
);

-- 区间表的**重算状态**（票 05）：空表本身说不出为什么空 —— "从没跑过"（批处理没接线）、
-- "没有测量"（检出模型未到位）、"平视下杆长恒缺测没有可用长势量"、"样本不足（每箱 <3）"
-- 是完全不同的四件事，页面上必须长得不一样。所以每次重算把**最近一次**的状态落一行，
-- `/ranges` 把它随区间一起返回。
CREATE TABLE IF NOT EXISTS ranges_state (
  id          INTEGER PRIMARY KEY CHECK (id = 1),  -- 单行表：只记最近一次重算
  computed_at TEXT,
  state       TEXT NOT NULL,     -- ok / no_measurements / all_excluded / no_environment
                                 -- / no_growth_metric / insufficient_samples / estimate_only
  state_text  TEXT NOT NULL,     -- 人话，页面直接显示，不用自己拼
  flags       TEXT,              -- JSON 列表：**整维标注**（票 26 的 unverified 等标注）——
                                 -- 标注进 flags 不进状态词（spec「状态与标注」）
  detail      TEXT,              -- JSON：计数 + 排除明细（**不许只有一句"样本不足"**）
  n_rows      INTEGER NOT NULL DEFAULT 0,
  metric      TEXT,              -- 本次用的长势量（决定量纲），没有则 NULL
  unit        TEXT,
  updated_at  TEXT NOT NULL
);
"""


def connect(db_path: str, *, check_same_thread: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    # 单进程里现在有多个写者（API 请求线程 + lifespan 里的入库线程）：
    # WAL 允许多读一写，但撞上写锁时要等一下，不要立刻抛 "database is locked"。
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    migrate(conn)
    return conn


def prepare_connection(conn: sqlite3.Connection) -> None:
    """共享连接的形态：**开 WAL**（多读一写）。

    API 拿一份连接给请求线程与 lifespan 里的作业线程用（票 12），所以这一步属于
    「怎么共享」而不是「建连接」。经门面 `analysis.store.prepare_connection` 转发到这里；
    PG 侧是空实现（MVCC 没有「日志模式」这回事）。
    """
    conn.execute("PRAGMA journal_mode=WAL")


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
        # object_name 的来源（service / request）：兜底名与 MinIO 实际 key 可能不一致。
        "object_name_source": "TEXT",
        # 帧级内容完整性（巡检手票 13）：陈旧重复帧不得进聚合统计。
        "frame_dup": "INTEGER",
        "dup_group": "TEXT",
        "content_sha256": "TEXT",
        "same_as_previous": "INTEGER",
        "duplicate_streak": "INTEGER",
        "elapsed_s": "REAL",
    },
    "rounds": {
        "round_id": "TEXT",
        "room_id": "TEXT",
        "entry_date": "TEXT",
        "batch_no": "TEXT",
        # 轮级内容完整性（巡检手票 13）。
        "suspect_duplicate": "INTEGER",
        "dup_groups": "TEXT",
        "content_check_error": "TEXT",
        "n_duplicate": "INTEGER",
        "n_frames_recorded": "INTEGER",
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
        # 菌簇范围（2026-10-13）：平视单档下盖径/杆长缺测，长势只能从这几个量算。
        "mean_cluster_w_mm": "REAL",
        "mean_cluster_h_mm": "REAL",
        "mean_area_mm2": "REAL",
        "mean_projected_d_mm": "REAL",
        # 估算证据（票 20，2026-10-14）。老行留 NULL = "那一行没有证据"，
        # **不是"没有估算"** —— 所以下游判"这行能不能用"永远看 quality，不看这些列是否为空。
        "estimated_fields": "TEXT",
        "scale_source": "TEXT",
        "mm_per_px": "REAL",
        "scale_ref_mm": "REAL",
        "scale_ref_px": "REAL",
    },
    # 区间表加量纲三列（票 05；Chief 2026-10-13 硬要求）。老行 NULL = "那时还没标量纲"。
    "ranges": {
        "metric": "TEXT",
        "unit": "TEXT",
        "rate_per_h": "REAL",
        # 档（票 26 / ADR-0025）：老行 NULL = "那时还没分档"，读侧按**未验证**算（fail-closed）。
        "tier": "TEXT",
        # 同一箱里两类行各几行（票 26）。老行 NULL = "那时还没分过档"，页面显示"未知"即可。
        "n_verified": "INTEGER",
        "n_estimate": "INTEGER",
    },
    # 状态表的整维标注列（票 26）：老库补列后是 NULL，读回来当空列表（= 没有标注）。
    "ranges_state": {
        "flags": "TEXT",
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


def fetch_latest_operations_before(
    conn: sqlite3.Connection, *, zone: str, before: str
) -> list[dict]:
    """每个 `(device_code, point_code)` 在 `before` **之前**的最后一条操作（票 09 的"沿前一日"）。

    为什么要有它：窗口起点前的那条变更决定"当天没改"到底是**沿用**某个值还是**未知**。
    只看窗口内的行会把"沿用"错报成"未知"——那是把缺数据当没设过，方向刚好反了。
    同一时刻有多条（重复导入）时取 `rowid` 最大的一条。
    """
    cols = ", ".join(f"o.{f}" for f in OPERATION_FIELDS)
    sql = f"""
    SELECT {cols} FROM operations o
    WHERE o.zone = ? AND o.ts < ?
      AND NOT EXISTS (
        SELECT 1 FROM operations n
        WHERE n.zone = o.zone AND n.device_code = o.device_code
          AND n.point_code = o.point_code AND n.ts < ?
          AND (n.ts > o.ts OR (n.ts = o.ts AND n.rowid > o.rowid))
      )
    ORDER BY o.device_code, o.point_code
    """
    cur = conn.execute(sql, (zone, before, before))
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


BATCH_FIELDS = ("room_id", "entry_date", "batch_no", "batch_id")


def upsert_batches(conn: sqlite3.Connection, rows: list[dict]) -> None:
    cols = ", ".join(BATCH_FIELDS)
    marks = ", ".join(f":{f}" for f in BATCH_FIELDS)
    conn.executemany(
        f"INSERT OR REPLACE INTO batches ({cols}) VALUES ({marks})",
        [{f: row.get(f) for f in BATCH_FIELDS} for row in rows],
    )
    conn.commit()


def replace_batches(
    conn: sqlite3.Connection,
    rows: list[dict],
    *,
    refreshed_at: str | None = None,
    detail: dict | None = None,
) -> dict:
    """**整体替换**台账物化缓存（票 13，Chief 2026-10-14 裁定）。

    为什么必须是替换而不是 upsert：`batches` 是 pgsql 并集的**缓存**，upsert 永不 prune ⇒
    源侧编码或我们的归一方式变一次，陈旧行就永久留下，还得靠人肉发现 + 手删
    （2026-10-14 实测：11 行旧编号 `7|`/`8|` 与新行 `607|` 同时存在，同一批次在页面上出两次）。

    三条纪律：
    1. **一个事务**里删+插：读的人只会看到旧集合或新集合，不会看到"空台账"
       （空台账会让上游把"缓存正在刷"读成"没有批次"）；
    2. 被删掉的键**逐条打印 + 计数**（收缩必须可见，不静默）；
    3. 同时写 `batches_state`，让 `GET /batches` 能给出 `ledger_refreshed_at` 与 `stale`。
    """
    import json
    from datetime import datetime

    stamp = refreshed_at or datetime.now().isoformat(timespec="seconds")
    old = {f"{r['room_id']}|{r['entry_date']}" for r in fetch_batches(conn)}
    new = {f"{row.get('room_id')}|{row.get('entry_date')}" for row in rows}
    pruned = sorted(old - new)

    cols = ", ".join(BATCH_FIELDS)
    marks = ", ".join(f":{f}" for f in BATCH_FIELDS)
    with conn:                      # 一个事务：删+插+状态一起提交
        conn.execute("DELETE FROM batches")
        conn.executemany(
            f"INSERT OR REPLACE INTO batches ({cols}) VALUES ({marks})",
            [{f: row.get(f) for f in BATCH_FIELDS} for row in rows],
        )
        conn.execute(
            "INSERT OR REPLACE INTO batches_state "
            "(id, refreshed_at, n_rows, n_pruned, pruned, detail, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?)",
            (stamp, len(rows), len(pruned), json.dumps(pruned, ensure_ascii=False),
             json.dumps(detail or {}, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
    if pruned:
        print(f"[batch_ingest] 替换掉 {len(pruned)} 行陈旧台账（逐条）：{', '.join(pruned)}")
    return {"n_rows": len(rows), "n_pruned": len(pruned), "pruned": pruned, "refreshed_at": stamp}


def fetch_batches_state(conn: sqlite3.Connection) -> dict:
    """台账物化缓存的刷新状态；**从没刷过也返回一条**（`refreshed_at = None`），形状恒定。"""
    import json

    cur = conn.execute(
        "SELECT refreshed_at, n_rows, n_pruned, pruned, detail, updated_at "
        "FROM batches_state WHERE id = 1"
    )
    row = cur.fetchone()
    if row is None:
        return {
            "refreshed_at": None, "n_rows": 0, "n_pruned": 0,
            "pruned": [], "detail": {}, "updated_at": None,
        }
    keys = ("refreshed_at", "n_rows", "n_pruned", "pruned", "detail", "updated_at")
    out = dict(zip(keys, row))
    for field in ("pruned", "detail"):
        try:
            out[field] = json.loads(out[field]) if out[field] else ([] if field == "pruned" else {})
        except (TypeError, ValueError):
            out[field] = {"raw": out[field], "parse_error": True} if field == "detail" else []
    return out


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


#: 测量线的字段清单（票 11）。与 measure 侧 `RECORD_FIELDS` **一字不差（含顺序）**，
#: 禁第四套命名；`room_id` 在上游是必填，存储层用 NOT NULL 兜底（缺了就插不进去，
#: 而不是静默丢字段——"写入方多给、存储方少要"这个坑踩过一次）。
#: 票 20（2026-10-14）追加五个估算证据列。**顺序照测量侧的线格式**（2026-10-14 对齐）：
#: 以前这边把菌簇四列排在 `quality` 之后，两侧顺序其实一直不同 —— 只有集合相同，
#: "一字不差"名不副实。现在按生产者的顺序排，测试用严格相等钉住。
MEASUREMENT_FIELDS = (
    "ts", "room_id", "box_id", "station_id", "angle_profile",
    "n", "mean_len_mm", "mean_cap_mm", "mean_cluster_w_mm", "mean_cluster_h_mm",
    "mean_area_mm2", "mean_projected_d_mm", "p10_len", "p90_len", "quality",
    "estimated_fields", "scale_source", "mm_per_px", "scale_ref_mm", "scale_ref_px",
)


def insert_measurements(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """按 (ts, room_id, box_id) 覆盖写。行允许缺字段（按 NULL 入库）。

    用**命名**占位符：`MEASUREMENT_FIELDS` 是唯一字段清单，加列时不会出现
    "SQL 少写一个 ?、值整体错位"这种最难查的错。

    两个证据列在入库前**归一**（`analysis/evidence.py`）：`estimated_fields` 收 list 或
    `+` 串都行、统一存 `+` 串（读回来是 list）；`scale_source` 走枚举归一
    （旧值 `bag_diameter` 归一 + 报警，未知值保留原文 + 报警）。
    """
    from analysis.evidence import format_estimated_fields, normalize_scale_source

    payload: list[dict] = []
    for row in rows:
        item = {f: row.get(f) for f in MEASUREMENT_FIELDS}
        item["estimated_fields"] = format_estimated_fields(row.get("estimated_fields"))
        item["scale_source"] = normalize_scale_source(row.get("scale_source"))
        payload.append(item)
    cols = ", ".join(MEASUREMENT_FIELDS)
    marks = ", ".join(f":{f}" for f in MEASUREMENT_FIELDS)
    conn.executemany(
        f"INSERT OR REPLACE INTO measurements ({cols}) VALUES ({marks})", payload,
    )
    conn.commit()


def fetch_measurements(
    conn: sqlite3.Connection,
    *,
    room_id: str | None = None,
    box_id: str | None = None,
) -> list[dict]:
    """取测量行（升序）。可按库房/框号过滤 —— 区间表要按库房分组，不该在内存里筛。

    `estimated_fields` **读回来是 list**（票 20 的形状要求）：列名清单让每处消费者
    自己 split 字符串，迟早有一处用逗号、一处用加号 —— 那就是静默错过滤。
    """
    from analysis.evidence import parse_estimated_fields

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
    out = [dict(zip(cols, row)) for row in cur.fetchall()]
    for row in out:
        row["estimated_fields"] = parse_estimated_fields(row.get("estimated_fields"))
    return out


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
    # 内容完整性（票 13）：聚合侧靠这三列决定"这一轮的帧要不要排除"
    "suspect_duplicate",
    "dup_groups",
    "content_check_error",
    "n_duplicate",
    "n_frames_recorded",
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
    "object_name_source",
    "frame_dup",
    "dup_group",
    "content_sha256",
    "same_as_previous",
    "duplicate_streak",
    "cloud_url",
    "elapsed_s",
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


def _as_text(value):
    """列表/字典 → JSON 文本，其余原样。

    `rounds.dup_groups` 是 TEXT 列，而生产者发来的是**对象数组**
    （`[{"sha256_12":…, "n":…, "stations":[…]}, …]`，见 `patrol/daemon.py:344`）——
    直接绑给 sqlite3 会抛 `ProgrammingError: type 'list' is not supported`。
    修的是这一条，但**真正的教训**是它的反面：如果存储层默默接受、库里头变成别的形状，
    那才是查不出来的那种坏。
    """
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _as_list(value):
    """JSON 文本 → Python 列表；不是 JSON 或解析失败就**原样返回**（不猜、不吞错）。"""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text.startswith(("[", "{")):
        return value
    try:
        return json.loads(text)
    except ValueError:
        return value


def insert_rounds(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """按 `ts` 覆盖写轮次行。列表型字段（`dup_groups`）序列化成 JSON 文本。

    读回来时由 `fetch_rounds` 解回列表 —— 写入方与读取方**成对**，否则下游拿到一个
    字符串，`dup_groups` 就没法用了。
    """
    cols = ", ".join(ROUND_FIELDS)
    marks = ", ".join(f":{f}" for f in ROUND_FIELDS)
    conn.executemany(
        f"INSERT OR REPLACE INTO rounds ({cols}) VALUES ({marks})",
        [{f: _as_text(r.get(f)) for f in ROUND_FIELDS} for r in rows],
    )
    conn.commit()


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
    out = []
    for row in cur.fetchall():
        item = dict(zip(cols, row))
        # 写入方把列表序列化成 JSON（`_as_text`），读取方成对解回来 ——
        # 否则下游拿到的 `dup_groups` 是个字符串，等于没这个字段。
        item["dup_groups"] = _as_list(item.get("dup_groups"))
        out.append(item)
    return out


def replace_ranges(conn: sqlite3.Connection, rows: list[dict], computed_at: str) -> None:
    """批处理结果整体替换（评审 #6：区间表化，API 只读）。

    **量纲铁律**：`rate_mm_per_h` 只在 `unit='mm/h'` 时写值，其余量纲一律 NULL ——
    一个叫 `_mm_` 的字段不许承载 mm²/h（票 05 / CONTEXT.md）。
    """
    conn.execute("DELETE FROM ranges")
    payload = []
    for row in rows:
        unit = row.get("unit")
        rate = row.get("rate_per_h")
        payload.append({
            **row,
            "computed_at": computed_at,
            # **不编默认档**：行上没标档就写 NULL，读侧按未验证算（写侧替它猜一个 =
            # 把"没人标过"洗成"已验证"）。本函数的调用方是 compute_ranges，那里档必有。
            "tier": row.get("tier"),
            "rate_mm_per_h": rate if unit == "mm/h" else None,
        })
    conn.executemany(
        "INSERT INTO ranges (stage, param, lo, hi, metric, unit, rate_per_h, "
        "rate_mm_per_h, n, tier, n_verified, n_estimate, computed_at) "
        "VALUES (:stage, :param, :lo, :hi, :metric, :unit, :rate_per_h, :rate_mm_per_h, "
        ":n, :tier, :n_verified, :n_estimate, :computed_at)",
        payload,
    )
    conn.commit()


def fetch_ranges(conn: sqlite3.Connection) -> list[dict]:
    """读区间表（只读物化结果）。

    **「未验证」是读侧派生的**（票 26 / ADR-0025）：判据只有 `tier`，且**没标过的行按
    未验证算** —— 宁可把老行短暂标成"未验证"（下一次重算就写上新档），也不许把没标过档的行
    当验证值显示。这一条是"看着像真的假数"最后一道闸。
    """
    from analysis.evidence import TIER_VERIFIED

    cur = conn.execute(
        "SELECT stage, param, lo, hi, metric, unit, rate_per_h, rate_mm_per_h, n, tier, "
        "n_verified, n_estimate, computed_at FROM ranges ORDER BY stage, param"
    )
    cols = [d[0] for d in cur.description]
    out = []
    for row in cur.fetchall():
        item = dict(zip(cols, row))
        item["unverified"] = item.get("tier") != TIER_VERIFIED
        out.append(item)
    return out


def fetch_advice_ranges(conn: sqlite3.Connection) -> list[dict]:
    """**建议层读区间表只许走这里**：只返回已验证档的行（票 26 / ADR-0025 §3）。

    "只有估算行"时返回 `[]` —— **有数不等于能用**：`GET /ranges` 照样把估算档给页面看，
    但建议层不许拿它当输入（调控建议不能建立在未验证的输入上）。断言在 `advice_input` 里，
    这里是数据库侧的入口。
    """
    from analysis.align import advice_input

    return advice_input(fetch_ranges(conn))


#: "区间表从来没人算过"—— **不要把"没算过"说成"样本不足"**：
#: 前者是接线问题（票 05 的 `refresh_ranges` 没排期），后者是数据问题。
RANGES_STATE_NEVER_COMPUTED = "never_computed"

_RANGES_STATE_COLS = (
    "computed_at", "state", "state_text", "detail", "flags", "n_rows", "metric", "unit",
    "updated_at",
)


def write_ranges_state(
    conn: sqlite3.Connection,
    *,
    state: str,
    state_text: str,
    computed_at: str | None = None,
    detail: dict | None = None,
    flags: list[str] | None = None,
    n_rows: int = 0,
    metric: str | None = None,
    unit: str | None = None,
    updated_at: str | None = None,
) -> None:
    """落最近一次重算的状态（单行表，整体替换）。

    `detail` 一律 JSON 序列化存库：调用方读回来就是 `dict`，不用自己解字符串。
    """
    from datetime import datetime

    conn.execute(
        "INSERT OR REPLACE INTO ranges_state "
        "(id, computed_at, state, state_text, detail, flags, n_rows, metric, unit, updated_at) "
        "VALUES (1, :computed_at, :state, :state_text, :detail, :flags, :n_rows, :metric, "
        ":unit, :updated_at)",
        {
            "computed_at": computed_at,
            "state": state,
            "state_text": state_text,
            "detail": json.dumps(detail or {}, ensure_ascii=False),
            "flags": json.dumps(list(flags or []), ensure_ascii=False),
            "n_rows": int(n_rows),
            "metric": metric,
            "unit": unit,
            "updated_at": updated_at or datetime.now().isoformat(timespec="seconds"),
        },
    )
    conn.commit()


def fetch_ranges_state(conn: sqlite3.Connection) -> dict:
    """读最近一次重算的状态。**库是空的时候也返回一条**（`never_computed`）——

    这样 `/ranges` 的调用方永远拿到同一个形状，不用自己判断"表不存在"。
    """
    cur = conn.execute(
        f"SELECT {', '.join(_RANGES_STATE_COLS)} FROM ranges_state WHERE id = 1"
    )
    row = cur.fetchone()
    if row is None:
        return {
            "computed_at": None,
            "state": RANGES_STATE_NEVER_COMPUTED,
            "state_text": "区间表还没有重算过（每日任务未接线或从未成功跑过）",
            "detail": {},
            "flags": [],
            "n_rows": 0,
            "metric": None,
            "unit": None,
            "updated_at": None,
        }
    out = dict(zip(_RANGES_STATE_COLS, row))
    try:
        out["detail"] = json.loads(out["detail"]) if out["detail"] else {}
    except (TypeError, ValueError):
        # 存坏了也别把端点打崩：原文留着，让排查看得到
        out["detail"] = {"raw_detail": out["detail"], "detail_parse_error": True}
    try:
        out["flags"] = json.loads(out["flags"]) if out["flags"] else []
    except (TypeError, ValueError):
        # 存坏了也别把端点打崩：**当没有标注**（不是当"已验证"）—— 读侧永远是保守那一侧
        out["flags"] = []
    return out

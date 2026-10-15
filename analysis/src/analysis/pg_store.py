"""PostgreSQL 数据访问层（票 28 第 1 步 expand）：与 `analysis/db.py` **同名同签名**。

**表进独立 schema（默认 `analysis`）**：目标库 `mushroom_algorithm` 的 public 里躺着大量
遗留业务表（`device_setpoint_changes` / `mushroom_batch_yield` / `decision_analysis_*` …），
analysis 的表放 public 就有撞名的可能 —— 撞上不是报错，是**写错表**，等发现时数据已经脏了。
schema 是这里的唯一护栏。

**这一层只管存储**：不读 TDengine（ADR-0026：只读源）、不碰别的 schema、不写 public。

与 SQLite 侧的三处差异（都是有意的，写在这里免得第 2 步再猜）：

1. `INSERT OR REPLACE` → `INSERT … ON CONFLICT (…) DO UPDATE`（幂等语义相同）；
2. **写入前按库里声明的列类型对齐值**（布尔 → 0/1、数字 → 文本）：SQLite 的列亲和性一直
   在替"写入方多给"擦屁股（`ok=True` 存成 1、`room_id=611` 存成 '611'），PG 会直接报错。
   类型表从 `information_schema` **现读**，不手抄一份（手抄的迟早与 DDL 漂移）；
3. `fetch_latest_operations_before` 不用 `rowid` 兜同一时刻的重复行 ——
   主键 `(ts, device_code, point_code)` 让"同一时刻同一个点两条"在 PG 里不可能存在。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

# 字段清单与 JSON 编解码都只有一处实现（`analysis/db.py`）：两张表的字段顺序、列表型字段
# 的存法必须两边一字不差，抄一份出来就是给"写进去是列表、读出来是字符串"留门。
from analysis.db import (
    BATCH_FIELDS,
    ENVIRONMENT_FIELDS,
    IMAGE_FIELDS,
    MEASUREMENT_FIELDS,
    OPERATION_FIELDS,
    RANGES_STATE_NEVER_COMPUTED,
    ROUND_FIELDS,
    _as_list,
    _as_text,
    flatten_image,
)
from analysis.store_config import StoreConfigError, load_dsn

DEFAULT_SCHEMA_ENV = "ANALYSIS_PG_SCHEMA"
DEFAULT_SCHEMA = "analysis"

#: schema 名会拼进 DDL（引号在这里没有意义：`CREATE SCHEMA` 的参数不能是占位符），
#: 所以只收朴素标识符。别的形态一律报错，不做"帮你转义一下"这种事。
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENT_MAX = 63

#: 与 `db.SCHEMA` 逐列对应（列名与顺序都一致，类型按 PG 的写法）。
#: 有测试逐列比对两边（`test_store_postgres.py`），改一边忘另一边会被抓住。
_TABLE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS {s}.environment (
      ts         text NOT NULL,
      zone       text NOT NULL,
      temp_c     double precision, temp_min double precision, temp_max double precision,
      rh_pct     double precision, rh_min   double precision, rh_max   double precision,
      co2_ppm    double precision, co2_min  double precision, co2_max  double precision,
      lamp_state double precision,
      lamp_on_s  double precision, lamp_off_s  double precision,
      lamp_on_m  double precision, lamp_off_m  double precision,
      lamp_on_min double precision,
      n          integer,
      PRIMARY KEY (ts, zone)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {s}.measurements (
      ts                 text NOT NULL,
      room_id            text NOT NULL,
      box_id             text NOT NULL,
      station_id         text,
      angle_profile      text,
      n                  integer,
      mean_len_mm        double precision,
      mean_cap_mm        double precision,
      mean_cluster_w_mm  double precision,
      mean_cluster_h_mm  double precision,
      mean_area_mm2      double precision,
      mean_projected_d_mm double precision,
      p10_len            double precision,
      p90_len            double precision,
      quality            text,
      estimated_fields   text,
      scale_source       text,
      mm_per_px          double precision,
      scale_ref_mm       double precision,
      scale_ref_px       double precision,
      PRIMARY KEY (ts, room_id, box_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {s}.rounds (
      ts                 text PRIMARY KEY,
      round_id           text,
      ok                 integer,
      n_results          integer,
      n_failures         integer,
      aborted            integer,
      room_id            text,
      entry_date         text,
      batch_no           text,
      suspect_duplicate  integer,
      dup_groups         text,
      content_check_error text,
      n_duplicate        integer,
      n_frames_recorded  integer
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {s}.images (
      ts                text NOT NULL,
      round_id          text,
      station_id        text,
      box_id            text,
      angle_profile     text,
      imaging_profile   text,
      frame_dup         integer,
      dup_group         text,
      content_sha256    text,
      same_as_previous  integer,
      duplicate_streak  integer,
      camera_ip         text,
      y                 double precision,
      z                 double precision,
      object_name       text,
      object_name_source text,
      cloud_url         text,
      ok                integer,
      error             text,
      elapsed_s         double precision,
      room_id           text,
      entry_date        text,
      batch_no          text,
      PRIMARY KEY (ts, station_id, angle_profile)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {s}.operations (
      ts          text NOT NULL,
      device_code text NOT NULL,
      point_code  text NOT NULL,
      zone        text,
      device_name text,
      point_name  text,
      value       text,
      operator    text,
      source      text,
      content     text,
      PRIMARY KEY (ts, device_code, point_code)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {s}.batches (
      room_id    text NOT NULL,
      entry_date text NOT NULL,
      batch_no   text,
      batch_id   text,
      PRIMARY KEY (room_id, entry_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {s}.batches_state (
      id           integer PRIMARY KEY CHECK (id = 1),
      refreshed_at text NOT NULL,
      n_rows       integer NOT NULL DEFAULT 0,
      n_pruned     integer NOT NULL DEFAULT 0,
      pruned       text,
      detail       text,
      updated_at   text NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {s}.ranges (
      stage         text NOT NULL,
      param         text NOT NULL,
      lo            double precision,
      hi            double precision,
      metric        text,
      unit          text,
      rate_per_h    double precision,
      rate_mm_per_h double precision,
      n             integer,
      n_verified    integer,
      n_estimate    integer,
      tier          text,
      computed_at   text NOT NULL,
      PRIMARY KEY (stage, param)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS {s}.ranges_state (
      id          integer PRIMARY KEY CHECK (id = 1),
      computed_at text,
      state       text NOT NULL,
      state_text  text NOT NULL,
      flags       text,
      detail      text,
      n_rows      integer NOT NULL DEFAULT 0,
      metric      text,
      unit        text,
      updated_at  text NOT NULL
    )
    """,
)


def schema_name(schema: str | None = None) -> str:
    """本次会话用的 schema：显式参数 → `ANALYSIS_PG_SCHEMA` → `analysis`。

    **默认不是 public**：public 里有遗留业务表（`device_setpoint_changes` 等），
    analysis 的表进去就可能撞名。
    """
    raw = (schema or os.environ.get(DEFAULT_SCHEMA_ENV) or DEFAULT_SCHEMA).strip()
    if not _IDENT_RE.match(raw) or len(raw) > _IDENT_MAX:
        raise StoreConfigError(
            f"schema 名不合法：{raw!r}（只收 [A-Za-z_][A-Za-z0-9_]*、≤{_IDENT_MAX} 字符）。"
            "名字要拼进 DDL，别的形态一律拒绝"
        )
    return raw


class PgConnection:
    """psycopg 连接 + 本次会话的 schema（门面按这个类型分派，见 `analysis/store.py`）。

    `column_types` 是 `ensure_schema` 时从 `information_schema` 读回来的列类型，
    写入前用它对齐值（见模块头第 2 条差异）。
    """

    def __init__(self, raw, schema: str):
        self.raw = raw
        self.schema = schema
        self.column_types: dict[str, dict[str, str]] = {}

    def commit(self) -> None:
        self.raw.commit()

    def close(self) -> None:
        self.raw.close()

    def transaction(self):
        """一个事务块（PG 侧多语句原子性的入口；SQLite 侧对应 `with conn:`）。"""
        return self.raw.transaction()

    def __enter__(self) -> "PgConnection":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False


def _driver():
    """惰性 import `psycopg`：只用 SQLite 时，import 链不该依赖重驱动（票 12 §1.1）。"""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - 取决于镜像
        raise StoreConfigError(
            "analysis 环境没有 psycopg：镜像要装 psycopg[binary]（票 28）"
        ) from exc
    return psycopg


def _load_column_types(conn: PgConnection) -> dict[str, dict[str, str]]:
    with conn.raw.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s",
            (conn.schema,),
        )
        out: dict[str, dict[str, str]] = {}
        for table, column, data_type in cur.fetchall():
            out.setdefault(table, {})[column] = data_type
    return out


def ensure_schema(conn: PgConnection) -> list[str]:
    """建 schema 与全部表（幂等），刷新列类型缓存；返回**本次新建**的 schema 名。

    对应 SQLite 侧的 `db.migrate`：老库补列那套历史（`ADDED_COLUMNS`）用不到 PG ——
    这边的表从第一天就是当前形状；将来加列走 `ALTER TABLE … ADD COLUMN`（第 2 步起才可能）。
    """
    created: list[str] = []
    with conn.raw.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (conn.schema,)
        )
        if cur.fetchone() is None:
            created.append(conn.schema)
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{conn.schema}"')
        for ddl in _TABLE_DDL:
            cur.execute(ddl.format(s=conn.schema))
    conn.column_types = _load_column_types(conn)
    return created


def connect(
    dsn: str | None = None, *, schema: str | None = None, connect_timeout: int = 10
) -> PgConnection:
    """建连接并保证 schema/表就位。连接串见 `analysis.store_config.load_dsn`。"""
    psycopg = _driver()
    target = schema_name(schema)
    resolved = load_dsn(dsn, what="analysis 存储（票 28）")
    try:
        raw = psycopg.connect(resolved, connect_timeout=connect_timeout, autocommit=True)
    except Exception as exc:  # noqa: BLE001 - 连不上就报错，绝不静默换后端
        raise StoreConfigError(f"连不上 pgsql（analysis 存储）：{exc}") from exc
    conn = PgConnection(raw, target)
    ensure_schema(conn)
    return conn


# ---------- 值的类型对齐 ----------


def _adapt(conn: PgConnection, table: str, field: str, value):
    """按库里声明的列类型对齐值。

    只处理 SQLite 亲和性真正会**静默**改变值的那几种（布尔进整数列、数字进文本列）；
    其余照原样交给驱动 —— 真正奇怪的值（字典进文本列之类）应该当场报错，
    冒充不了"SQLite 收得下、PG 收不下"的那一类。
    """
    if value is None:
        return None
    declared = conn.column_types.get(table, {}).get(field)
    if isinstance(value, bool):
        if declared == "integer":
            return int(value)
        if declared == "double precision":
            return float(value)
        if declared == "text":
            return "1" if value else "0"   # SQLite 的 TEXT 亲和性就是这么转的
        return value
    if declared == "text" and isinstance(value, (int, float)):
        return str(value)
    return value


def _payload(conn: PgConnection, table: str, fields: tuple[str, ...], row: dict) -> dict:
    return {f: _adapt(conn, table, f, row.get(f)) for f in fields}


def _table(conn: PgConnection, table: str) -> str:
    return f'"{conn.schema}".{table}'


def _upsert_sql(conn: PgConnection, table: str, fields: tuple[str, ...],
                pk: tuple[str, ...]) -> str:
    cols = ", ".join(fields)
    marks = ", ".join(f"%({f})s" for f in fields)
    updates = ", ".join(f"{f} = EXCLUDED.{f}" for f in fields if f not in pk)
    return (f"INSERT INTO {_table(conn, table)} ({cols}) VALUES ({marks}) "
            f"ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}")


def _executemany(conn: PgConnection, sql: str, payload: list[dict]) -> None:
    if not payload:
        return
    with conn.raw.cursor() as cur:
        cur.executemany(sql, payload)


def _fetch_dicts(conn: PgConnection, sql: str, params: list | tuple = ()) -> list[dict]:
    with conn.raw.cursor() as cur:
        cur.execute(sql, params)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


# ---------- environment ----------


def upsert_environment(conn: PgConnection, rows: list[dict]) -> None:
    """按 (ts, zone) 覆盖写。行允许缺字段（按 NULL 入库）。"""
    sql = _upsert_sql(conn, "environment", ENVIRONMENT_FIELDS, ("ts", "zone"))
    _executemany(conn, sql, [_payload(conn, "environment", ENVIRONMENT_FIELDS, r) for r in rows])


def fetch_environment(
    conn: PgConnection, *, zone: str | None = None, start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """按库房与时间窗取实测序列（升序）。时间窗是含端点的 ISO8601 前缀比较。"""
    sql = f"SELECT {', '.join(ENVIRONMENT_FIELDS)} FROM {_table(conn, 'environment')}"
    where: list[str] = []
    params: list = []
    for field, value, op in (("zone", zone, "="), ("ts", start, ">="), ("ts", end, "<=")):
        if value:
            where.append(f"{field} {op} %s")
            params.append(value)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts"
    return _fetch_dicts(conn, sql, params)


# ---------- operations ----------


def upsert_operations(conn: PgConnection, rows: list[dict]) -> None:
    """同一 (时刻, 设备, 点位) 覆盖写：重拉同一窗口不会翻倍。"""
    sql = _upsert_sql(conn, "operations", OPERATION_FIELDS, ("ts", "device_code", "point_code"))
    _executemany(conn, sql, [_payload(conn, "operations", OPERATION_FIELDS, r) for r in rows])


def fetch_operations(
    conn: PgConnection, *, zone: str | None = None, start: str | None = None,
    end: str | None = None, limit: int | None = None,
) -> list[dict]:
    """按库房与时间窗取操作日志，最新在前。"""
    sql = f"SELECT {', '.join(OPERATION_FIELDS)} FROM {_table(conn, 'operations')}"
    where: list[str] = []
    params: list = []
    for field, value, op in (("zone", zone, "="), ("ts", start, ">="), ("ts", end, "<=")):
        if value:
            where.append(f"{field} {op} %s")
            params.append(value)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC, device_code"
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    return _fetch_dicts(conn, sql, params)


def fetch_latest_operations_before(conn: PgConnection, *, zone: str, before: str) -> list[dict]:
    """每个 `(device_code, point_code)` 在 `before` **之前**的最后一条操作。

    判据与 SQLite 侧一致（"没有更晚的、仍在 before 之前的同点行"）；少了 `rowid` 那一截
    是因为 PG 里不存在同一 `(ts, device_code, point_code)` 的两行 —— 主键挡着。
    """
    cols = ", ".join(f"o.{f}" for f in OPERATION_FIELDS)
    table = _table(conn, "operations")
    sql = f"""
    SELECT {cols} FROM {table} o
    WHERE o.zone = %s AND o.ts < %s
      AND NOT EXISTS (
        SELECT 1 FROM {table} n
        WHERE n.zone = o.zone AND n.device_code = o.device_code
          AND n.point_code = o.point_code AND n.ts < %s AND n.ts > o.ts
      )
    ORDER BY o.device_code, o.point_code
    """
    return _fetch_dicts(conn, sql, [zone, before, before])


# ---------- batches（台账物化缓存） ----------


def upsert_batches(conn: PgConnection, rows: list[dict]) -> None:
    sql = _upsert_sql(conn, "batches", BATCH_FIELDS, ("room_id", "entry_date"))
    _executemany(conn, sql, [_payload(conn, "batches", BATCH_FIELDS, r) for r in rows])


def replace_batches(
    conn: PgConnection, rows: list[dict], *, refreshed_at: str | None = None,
    detail: dict | None = None,
) -> dict:
    """**整体替换**台账物化缓存：一个事务里删+插+写状态，被删掉的键逐条打印。

    语义与 SQLite 侧一字不差（见 `analysis/db.py` 的同名函数：upsert 永不 prune，
    陈旧行会永久留下，同一批次在页面上出两次）。
    """
    stamp = refreshed_at or datetime.now().isoformat(timespec="seconds")
    old = {f"{r['room_id']}|{r['entry_date']}" for r in fetch_batches(conn)}
    new = {f"{row.get('room_id')}|{row.get('entry_date')}" for row in rows}
    pruned = sorted(old - new)

    cols = ", ".join(BATCH_FIELDS)
    marks = ", ".join(f"%({f})s" for f in BATCH_FIELDS)
    updates = ", ".join(f"{f} = EXCLUDED.{f}" for f in BATCH_FIELDS
                        if f not in ("room_id", "entry_date"))
    payload = [_payload(conn, "batches", BATCH_FIELDS, r) for r in rows]
    with conn.transaction(), conn.raw.cursor() as cur:
        cur.execute(f"DELETE FROM {_table(conn, 'batches')}")
        if payload:
            cur.executemany(
                f"INSERT INTO {_table(conn, 'batches')} ({cols}) VALUES ({marks}) "
                f"ON CONFLICT (room_id, entry_date) DO UPDATE SET {updates}",
                payload,
            )
        cur.execute(
            f"INSERT INTO {_table(conn, 'batches_state')} "
            "(id, refreshed_at, n_rows, n_pruned, pruned, detail, updated_at) "
            "VALUES (1, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET refreshed_at = EXCLUDED.refreshed_at, "
            "n_rows = EXCLUDED.n_rows, n_pruned = EXCLUDED.n_pruned, "
            "pruned = EXCLUDED.pruned, detail = EXCLUDED.detail, updated_at = EXCLUDED.updated_at",
            (stamp, len(rows), len(pruned), json.dumps(pruned, ensure_ascii=False),
             json.dumps(detail or {}, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds")),
        )
    if pruned:
        print(f"[batch_ingest] 替换掉 {len(pruned)} 行陈旧台账（逐条）：{', '.join(pruned)}")
    return {"n_rows": len(rows), "n_pruned": len(pruned), "pruned": pruned, "refreshed_at": stamp}


def fetch_batches_state(conn: PgConnection) -> dict:
    """台账刷新状态；**从没刷过也返回一条**（`refreshed_at = None`），形状恒定。"""
    rows = _fetch_dicts(
        conn,
        "SELECT refreshed_at, n_rows, n_pruned, pruned, detail, updated_at "
        f"FROM {_table(conn, 'batches_state')} WHERE id = 1",
    )
    if not rows:
        return {
            "refreshed_at": None, "n_rows": 0, "n_pruned": 0,
            "pruned": [], "detail": {}, "updated_at": None,
        }
    return _state_row(rows[0])


def fetch_batches(conn: PgConnection, *, room_id: str | None = None) -> list[dict]:
    sql = f"SELECT {', '.join(BATCH_FIELDS)} FROM {_table(conn, 'batches')}"
    params: list = []
    if room_id:
        sql += " WHERE room_id = %s"
        params.append(room_id)
    sql += " ORDER BY room_id, entry_date"
    return _fetch_dicts(conn, sql, params)


def _state_row(row: dict) -> dict:
    """`pruned` / `detail` 是 JSON 文本 → 读回来是 list/dict（存坏了也不把端点打崩）。"""
    out = dict(row)
    for field in ("pruned", "detail"):
        try:
            out[field] = json.loads(out[field]) if out[field] else (
                [] if field == "pruned" else {})
        except (TypeError, ValueError):
            out[field] = {"raw": out[field], "parse_error": True} if field == "detail" else []
    return out


# ---------- measurements ----------


def insert_measurements(conn: PgConnection, rows: list[dict]) -> None:
    """按 (ts, room_id, box_id) 覆盖写；证据列先归一（`analysis/evidence.py`）。"""
    from analysis.evidence import format_estimated_fields, normalize_scale_source

    payload: list[dict] = []
    for row in rows:
        item = _payload(conn, "measurements", MEASUREMENT_FIELDS, row)
        item["estimated_fields"] = format_estimated_fields(row.get("estimated_fields"))
        item["scale_source"] = normalize_scale_source(row.get("scale_source"))
        payload.append(item)
    _executemany(
        conn,
        _upsert_sql(conn, "measurements", MEASUREMENT_FIELDS, ("ts", "room_id", "box_id")),
        payload,
    )


def fetch_measurements(
    conn: PgConnection, *, room_id: str | None = None, box_id: str | None = None
) -> list[dict]:
    """取测量行（升序）；`estimated_fields` 读回来是 **list**（票 20 的形状要求）。"""
    from analysis.evidence import parse_estimated_fields

    sql = f"SELECT {', '.join(MEASUREMENT_FIELDS)} FROM {_table(conn, 'measurements')}"
    where: list[str] = []
    params: list = []
    if room_id:
        where.append("room_id = %s")
        params.append(room_id)
    if box_id:
        where.append("box_id = %s")
        params.append(box_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts"
    out = _fetch_dicts(conn, sql, params)
    for row in out:
        row["estimated_fields"] = parse_estimated_fields(row.get("estimated_fields"))
    return out


# ---------- images / rounds ----------


def insert_images(conn: PgConnection, rows: list[dict]) -> None:
    """图像索引（ADR-0005）：一帧一行；`yz` 摊平成 y/z（`db.flatten_image`）。"""
    sql = _upsert_sql(conn, "images", IMAGE_FIELDS, ("ts", "station_id", "angle_profile"))
    _executemany(conn, sql, [_payload(conn, "images", IMAGE_FIELDS, flatten_image(r)) for r in rows])


def fetch_images(
    conn: PgConnection, *, station_id: str | None = None, box_id: str | None = None,
    room_id: str | None = None, round_id: str | None = None, limit: int | None = None,
) -> list[dict]:
    """按轮次/站位/框/库房（都可选）倒序取图像索引，供历史模式使用。"""
    sql = f"SELECT {', '.join(IMAGE_FIELDS)} FROM {_table(conn, 'images')}"
    where: list[str] = []
    params: list = []
    for field, value in (("station_id", station_id), ("box_id", box_id),
                         ("room_id", room_id), ("round_id", round_id)):
        if value:
            where.append(f"{field} = %s")
            params.append(value)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC"
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    return _fetch_dicts(conn, sql, params)


def insert_rounds(conn: PgConnection, rows: list[dict]) -> None:
    """按 `ts` 覆盖写轮次行；列表型字段（`dup_groups`）序列化成 JSON 文本（与读侧成对）。"""
    payload = [
        _payload(conn, "rounds", ROUND_FIELDS, {f: _as_text(r.get(f)) for f in ROUND_FIELDS})
        for r in rows
    ]
    _executemany(conn, _upsert_sql(conn, "rounds", ROUND_FIELDS, ("ts",)), payload)


def fetch_rounds(
    conn: PgConnection, *, room_id: str | None = None, entry_date: str | None = None,
    batch_no: str | None = None, limit: int | None = None,
) -> list[dict]:
    """按库房/入库日期/生产批次筛选巡检轮次，最新轮次优先；`dup_groups` 解回 list。"""
    sql = f"SELECT {', '.join(ROUND_FIELDS)} FROM {_table(conn, 'rounds')}"
    where: list[str] = []
    params: list = []
    for field, value in (("room_id", room_id), ("entry_date", entry_date),
                         ("batch_no", batch_no)):
        if value:
            where.append(f"{field} = %s")
            params.append(value)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC"
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))
    out = _fetch_dicts(conn, sql, params)
    for item in out:
        item["dup_groups"] = _as_list(item.get("dup_groups"))
    return out


# ---------- ranges ----------


def replace_ranges(conn: PgConnection, rows: list[dict], computed_at: str) -> None:
    """批处理结果整体替换（评审 #6：区间表化，API 只读）。

    **量纲铁律**：`rate_mm_per_h` 只在 `unit='mm/h'` 时写值，其余量纲一律 NULL。
    """
    payload = []
    for row in rows:
        unit = row.get("unit")
        rate = row.get("rate_per_h")
        payload.append(_payload(conn, "ranges", (
            "stage", "param", "lo", "hi", "metric", "unit", "rate_per_h",
            "rate_mm_per_h", "n", "tier", "n_verified", "n_estimate", "computed_at",
        ), {
            **row,
            "computed_at": computed_at,
            "tier": row.get("tier"),
            "rate_mm_per_h": rate if unit == "mm/h" else None,
        }))
    with conn.transaction(), conn.raw.cursor() as cur:
        cur.execute(f"DELETE FROM {_table(conn, 'ranges')}")
        if payload:
            cur.executemany(
                f"INSERT INTO {_table(conn, 'ranges')} "
                "(stage, param, lo, hi, metric, unit, rate_per_h, rate_mm_per_h, n, tier, "
                "n_verified, n_estimate, computed_at) VALUES "
                "(%(stage)s, %(param)s, %(lo)s, %(hi)s, %(metric)s, %(unit)s, %(rate_per_h)s, "
                "%(rate_mm_per_h)s, %(n)s, %(tier)s, %(n_verified)s, %(n_estimate)s, "
                "%(computed_at)s)",
                payload,
            )


def fetch_ranges(conn: PgConnection) -> list[dict]:
    """读区间表；**「未验证」是读侧派生的**（票 26 / ADR-0025）：只有 `tier` 是判据，
    没标过档的行按未验证算。"""
    from analysis.evidence import TIER_VERIFIED

    out = _fetch_dicts(
        conn,
        "SELECT stage, param, lo, hi, metric, unit, rate_per_h, rate_mm_per_h, n, tier, "
        f"n_verified, n_estimate, computed_at FROM {_table(conn, 'ranges')} "
        "ORDER BY stage, param",
    )
    for item in out:
        item["unverified"] = item.get("tier") != TIER_VERIFIED
    return out


def fetch_advice_ranges(conn: PgConnection) -> list[dict]:
    """**建议层读区间表只许走这里**：只返回已验证档的行（票 26 / ADR-0025 §3）。"""
    from analysis.align import advice_input

    return advice_input(fetch_ranges(conn))


_RANGES_STATE_COLS = (
    "computed_at", "state", "state_text", "detail", "flags", "n_rows", "metric", "unit",
    "updated_at",
)


def write_ranges_state(
    conn: PgConnection, *, state: str, state_text: str, computed_at: str | None = None,
    detail: dict | None = None, flags: list[str] | None = None, n_rows: int = 0,
    metric: str | None = None, unit: str | None = None, updated_at: str | None = None,
) -> None:
    """落最近一次重算的状态（单行表，整体替换）。"""
    with conn.raw.cursor() as cur:
        cur.execute(
            f"INSERT INTO {_table(conn, 'ranges_state')} "
            "(id, computed_at, state, state_text, detail, flags, n_rows, metric, unit, updated_at) "
            "VALUES (1, %(computed_at)s, %(state)s, %(state_text)s, %(detail)s, %(flags)s, "
            "%(n_rows)s, %(metric)s, %(unit)s, %(updated_at)s) "
            "ON CONFLICT (id) DO UPDATE SET computed_at = EXCLUDED.computed_at, "
            "state = EXCLUDED.state, state_text = EXCLUDED.state_text, "
            "detail = EXCLUDED.detail, flags = EXCLUDED.flags, n_rows = EXCLUDED.n_rows, "
            "metric = EXCLUDED.metric, unit = EXCLUDED.unit, updated_at = EXCLUDED.updated_at",
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


def fetch_ranges_state(conn: PgConnection) -> dict:
    """读最近一次重算的状态；**库是空的时候也返回一条**（`never_computed`）。"""
    rows = _fetch_dicts(
        conn,
        f"SELECT {', '.join(_RANGES_STATE_COLS)} FROM {_table(conn, 'ranges_state')} WHERE id = 1",
    )
    if not rows:
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
    out = dict(rows[0])
    try:
        out["detail"] = json.loads(out["detail"]) if out["detail"] else {}
    except (TypeError, ValueError):
        out["detail"] = {"raw_detail": out["detail"], "detail_parse_error": True}
    try:
        out["flags"] = json.loads(out["flags"]) if out["flags"] else []
    except (TypeError, ValueError):
        out["flags"] = []
    return out

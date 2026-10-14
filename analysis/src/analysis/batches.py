"""批次台账（pgsql）→ analysis.batches。

**批次边界只能来自台账**：先试过"按数据断档切段"，2026-10-12 在真机上被证否——
库房环境是连续采集的，换批时传感器并不停，25 天只会切出一段、跨了两批。

## 为什么不再走 `docker exec tools_mysql`

用户 2026-10-14 裁定：**`tools_mysql` 是业务数据库，禁止使用**。算法侧取数一律走
**pgsql**（compose 里的 `postgres_db`，库 `mushroom_algorithm`）。旧实现（`docker exec`
+ `mysql` 客户端 + `MYSQL_ROOT_PASSWORD`）已下线，连带"给容器挂 docker.sock"那条方案作废。

## 取数口径（⚠️ 待切换到权威源，Chief 2026-10-14）

**唯一权威 = 算法侧 `src/utils/batch_yield_service.get_batch_ranges` 的实时并集**
（IoT 面板 ∪ `image_text_quality`）；`mushroom_batch_yield` 只是**派生缓存**、会滞后。
本模块当前的 `SQL` 是**临时口径**（凭证明可用的两张表做并集），**不算权威**：
切换方式二选一 —— **A′ 镜像带 `src/**` ⇒ 直接 import 同一函数**；
否则走**算法侧只读 HTTP**（同一个函数的出口，不新增服务/端口）。
**两条路都不许自己再写一份 SQL 取集合**（本文件现状即为待替换项）。

pgsql 里带 `(room_id, in_date)` 的表有两张（`src/utils/create_table.py:138-208`）：

| 表 | 是什么 | 备注 |
| --- | --- | --- |
| `mushroom_batch_yield` | 每批次产量统计表 | `(room_id, in_date, stat_date)` 唯一；**它是被 `init_batch_yield_records` 灌出来的**（来源是 IoT 面板 + `image_text_quality`），所以覆盖取决于那个作业有没有跑 |
| `image_text_quality` | 文本描述/图像质量表 | 每次采集一条，`in_date` 是进库日期 |

所以这里取**两者的并集**（`UNION`）—— 单看一张都可能漏批。成员是否齐全、
四个库房覆盖到哪，见票 13 的"事实盘点"（结论由发版官只读查询回填）。

**为什么惰性 import `psycopg`**：analysis 的 API 启动链不许依赖重驱动；
批次作业在容器里跑时才需要它（票 12 §1.1 的过渡期暂停也依赖这一点）。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

DEFAULT_DSN_ENV = "PGSQL_DSN"
#: 更推荐的一条：DSN 放文件、进程运行时读（`env_file` 会把值注入 `docker inspect` 可见的环境变量）
DEFAULT_DSN_FILE_ENV = "PGSQL_DSN_FILE"
#: `batch_no` 映射文件（仓库里的数据文件；由 `python -m analysis.batch_map` 生成）。
#: **按包位置解析，不按 cwd**：相对路径换个工作目录就"文件不存在 ⇒ batch_no 全 NULL"，
#: 而那个失败长得跟"还没生成映射"一样（房间别名表那边实测踩过同一个坑）。
DEFAULT_MAP_PATH = str(Path(__file__).resolve().parents[2] / "data" / "batch_no_map.json")

#: 两条来源的并集；只取"批次身份"要用的两列（ADR-0020：库房号 + 入库日期）。
SQL = """
SELECT DISTINCT room_id, in_date
FROM (
    SELECT room_id, in_date FROM mushroom_batch_yield
    WHERE room_id IS NOT NULL AND in_date IS NOT NULL
    UNION
    SELECT room_id, in_date FROM image_text_quality
    WHERE room_id IS NOT NULL AND in_date IS NOT NULL
) AS batches
ORDER BY room_id, in_date
""".strip()

#: 只用来回答"**只出现在一张表里**的批次有哪些"（Chief 2026-10-14 的可观测性要求）。
#: 两张表单独看都可能漏批：`mushroom_batch_yield` 是派生表（覆盖取决于它自己的作业跑没跑），
#: `image_text_quality` 绑老链路。并集是当前唯一不丢批的口径，但**差异必须看得见**。
SOURCES_SQL = """
SELECT room_id, in_date,
       bool_or(src = 'yield')       AS in_yield,
       bool_or(src = 'text_quality') AS in_text
FROM (
    SELECT room_id, in_date, 'yield' AS src FROM mushroom_batch_yield
    WHERE room_id IS NOT NULL AND in_date IS NOT NULL
    UNION ALL
    SELECT room_id, in_date, 'text_quality' AS src FROM image_text_quality
    WHERE room_id IS NOT NULL AND in_date IS NOT NULL
) AS batches
GROUP BY room_id, in_date
ORDER BY room_id, in_date
""".strip()


class BatchFetchError(RuntimeError):
    """取不到台账时**不要**退回"按断档切段"——那会算出跨两批的假批次。"""


def _as_date(value) -> str:
    """`date`/`datetime`/ISO 字符串 → `YYYY-MM-DD`；解析不了即报错（**不猜**）。"""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise BatchFetchError(f"入库日期不合法：{value!r}") from exc


def load_batch_no_map(path: str | None = None) -> dict[str, str]:
    """读 `(room_id|in_date) → batch_no` 映射（从**我们自己库里既有行**反推出来的，不碰 MySQL）。

    文件缺失或损坏 ⇒ 返回 `{}` 并**打一行警告**：映射只影响显示名，
    不该因为一个坏文件把批次取数整个挡住；但**也不许静默** —— "缺映射"要能被看见
    （`fetch_batches_with_report` 会把它计数出来）。
    """
    target = Path(path or os.environ.get("BATCH_NO_MAP") or DEFAULT_MAP_PATH)
    if not target.exists():
        print(f"[batch_map] 没有映射文件 {target}：batch_no 一律留 NULL（不编值）")
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        raw = data.get("map", data) if isinstance(data, dict) else {}
        return {str(k): str(v) for k, v in raw.items() if v}
    except (OSError, ValueError, AttributeError) as exc:
        print(f"[batch_map] 映射文件读不了（{exc}）：batch_no 一律留 NULL")
        return {}


def rows_to_batches(rows: list, *, batch_no_map: dict[str, str] | None = None) -> list[dict]:
    """`(room_id, in_date)` 行 → 入库行。

    **`batch_no` 从映射取**；映射里没有就写 **NULL**（编一个就是造假）——
    显示名缺失时页面用派生 id `库房号|入库日期`，见 `batch_id`。

    **查找键要先归一到规范库房号**（2026-10-14）：映射文件是从**我们库里既有行**反推的，
    对 607/608 两间用的是旧编号（`7|…`/`8|…`），而 pgsql 取数回来的是 3 位编号
    （`607|…`）—— 直接拿原文查表会让**全部 11 个历史批次的 `batch_no` 静默变 NULL**，
    看起来跟"还没生成映射"一模一样。所以三种键都试：**规范号** → 原文 →
    **该规范号的旧编号**（映射的逆向，`alias_codes_for`）；都不中才算真缺。
    """
    from analysis.rooms import alias_codes_for, canonical_room

    mapping = batch_no_map or {}
    out: list[dict] = []
    for row in rows:
        if len(row) != 2:
            raise BatchFetchError(f"查询返回了 {len(row)} 列，期望 2 列（room_id, in_date）")
        room, in_date = row
        date = _as_date(in_date)
        if not room:
            raise BatchFetchError(f"库房号为空的批次行：{row!r}")
        raw = str(room)
        canonical = canonical_room(raw)
        candidates = [
            f"{canonical}|{date}" if canonical else None,
            f"{raw}|{date}",
            *(f"{code}|{date}" for code in alias_codes_for(canonical)),
        ]
        keys = list(dict.fromkeys(k for k in candidates if k))     # 去重保序
        out.append({
            "room_id": raw,
            "entry_date": date,
            "batch_no": next((mapping[k] for k in keys if k in mapping), None),
            "batch_id": keys[0],
        })
    out.sort(key=lambda r: (r["room_id"], r["entry_date"]))
    return out


def fetch_batches(*, dsn: str | None = None, runner=None, map_path: str | None = None) -> list[dict]:
    """从 pgsql 读台账（并集口径）。

    `runner` 可注入（测试用），形状：`(sql) -> list[tuple]`。
    生产走惰性 import 的 `psycopg`；连接串优先级：`--dsn` → `PGSQL_DSN_FILE` 文件内容
    → 环境变量 `PGSQL_DSN`。**没有就报错，绝不退回 `docker exec`、也绝不退回"按断档切段"。**
    """
    run = runner or _pgsql_runner(dsn)
    return rows_to_batches(run(SQL), batch_no_map=load_batch_no_map(map_path))


def fetch_batches_with_report(
    *, dsn: str | None = None, runner=None, map_path: str | None = None,
) -> tuple[list[dict], dict]:
    """同 `fetch_batches`，但多跑一条"来源差异"查询并返回**可观测报告**。

    报告字段：`n_total` / `n_both` / `only_in_batch_yield` / `only_in_text_quality` /
    `n_missing_batch_no` / `missing_batch_no`。**每个单边批次打一行日志** ——
    并集是当前唯一不丢批的口径，但差异必须看得见，等差异大了再收敛到真权威源。
    """
    run = runner or _pgsql_runner(dsn)
    mapping = load_batch_no_map(map_path)
    batches = rows_to_batches(run(SQL), batch_no_map=mapping)

    only_yield: list[str] = []
    only_text: list[str] = []
    sources_rows = run(SOURCES_SQL)
    for row in sources_rows:
        if len(row) != 4:
            raise BatchFetchError("来源差异查询返回列数不是 4（room_id, in_date, in_yield, in_text）")
        room, in_date, in_yield, in_text = row
        key = f"{room}|{_as_date(in_date)}"
        if in_yield and not in_text:
            only_yield.append(key)
        elif in_text and not in_yield:
            only_text.append(key)

    missing = [b["batch_id"] for b in batches if not b["batch_no"]]
    report = {
        "n_total": len(sources_rows),
        "n_both": len(sources_rows) - len(only_yield) - len(only_text),
        "only_in_batch_yield": only_yield,
        "only_in_text_quality": only_text,
        "n_missing_batch_no": len(missing),
        "missing_batch_no": missing,
    }
    for key in only_yield:
        print(f"[batch_ingest] 只出现在 mushroom_batch_yield：{key}")
    for key in only_text:
        print(f"[batch_ingest] 只出现在 image_text_quality：{key}")
    print(f"[batch_ingest] 来源差异：共 {report['n_total']} 个批次，"
          f"两表都有 {report['n_both']}，"
          f"仅 yield {len(only_yield)}，仅 text_quality {len(only_text)}；"
          f"缺 batch_no 映射 {len(missing)}（缺的写 NULL，不编值）")
    return batches, report


def _load_dsn(dsn: str | None = None) -> str:
    """连接串：显式参数 → 文件 → 环境变量。

    **为什么推荐文件**：`env_file` 是 compose 在创建容器时读文件并**注入成环境变量**的，
    值会出现在 `docker inspect` 的 `Config.Env`（发版官 2026-10-14 实测）。要满足
    "值不进 `docker inspect`"只有一条路：DSN 放文件、进程运行时自己读
    （宿主 0600 文件 bind 进容器 `:ro`；挂一个文件卷不算新增 service）。
    """
    if dsn and dsn.strip():
        return dsn.strip()
    path = (os.environ.get(DEFAULT_DSN_FILE_ENV) or "").strip()
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise BatchFetchError(f"读 {path} 失败（{DEFAULT_DSN_FILE_ENV}）：{exc}") from exc
        if text:
            return text
    env = (os.environ.get(DEFAULT_DSN_ENV) or "").strip()
    if env:
        return env
    raise BatchFetchError(
        f"没有 pgsql 连接串：设 {DEFAULT_DSN_FILE_ENV}（文件，推荐）或 {DEFAULT_DSN_ENV}（环境变量）。"
        "**不退回 docker exec / 不退回按断档切段**"
    )


def _pgsql_runner(dsn: str | None):
    resolved = _load_dsn(dsn)

    def run(sql: str) -> list:
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - 取决于容器镜像
            raise BatchFetchError(
                "analysis 环境没有 psycopg：镜像要装 psycopg[binary]（票 13）"
            ) from exc
        try:
            with psycopg.connect(resolved) as conn, conn.cursor() as cur:
                cur.execute(sql)
                return cur.fetchall()
        except Exception as exc:  # noqa: BLE001 - 连不上就报错，绝不返回空表
            raise BatchFetchError(f"读 pgsql 失败：{exc}") from exc

    return run


def dedupe_by_identity(
    rows: list[dict], *, key: str = "room_id_canonical"
) -> tuple[list[dict], dict]:
    """同一 `(规范库房号, 入库日期)` 出现多行时**只留一行**，并把重复**计数可见**。

    为什么读取侧也要挡（Chief 2026-10-14 选 B）：物化缓存换口径的那段时间里，
    同一批次会同时挂着新旧两种编号（`7|…` 与 `607|…`），两条都归一到同一个身份 ——
    `window_of` 会当成两个窗口，页面上就是**两张一模一样的批次卡**、环境汇总重复计入。
    这是"同一身份出现两次"，读的人不该看到两张卡。

    保留哪一条：优先**原始编号就是规范号**的那条（它不带历史编码），否则取第一条。
    """
    seen: dict[tuple, dict] = {}
    dupes: list[str] = []
    samples: list[dict] = []
    for row in rows:
        identity = (row.get(key), str(row.get("entry_date") or ""))
        if identity not in seen:
            seen[identity] = row
            continue
        keep = seen[identity]
        # 规范化程度更高的那条胜出（原始编号 == 规范号 ⇒ 不是历史编码）
        if row.get("room_id") == row.get(key) and keep.get("room_id") != keep.get(key):
            seen[identity] = row
        dupes.append(f"{identity[0]}|{identity[1]}")
        if len(samples) < 5:
            samples.append({
                "identity": f"{identity[0]}|{identity[1]}",
                "kept": seen[identity].get("room_id"),
                "dropped": row.get("room_id"),
            })
    out = sorted(seen.values(), key=lambda r: (str(r.get(key) or ""), str(r.get("entry_date") or "")))
    report = {
        "n_total": len(rows),
        "n_kept": len(out),
        "n_dupes": len(dupes),
        "dupe_keys": sorted(dupes),
        "dupe_samples": samples,
    }
    if dupes:
        print(
            f"[batches] 同一批次身份出现 {len(dupes)} 次重复（已去重，保留规范编号那条）："
            f"{', '.join(sorted(dupes))}"
        )
    return out, report


def window_of(
    batches: list[dict], *, room: str, limit: int = 3, key: str = "room_id"
) -> list[dict]:
    """某库房最近 `limit` 个批次的窗口；窗口右端是下一批的入库日（含端点用开区间）。

    `key` 缺省按原始 `room_id` 取；**跨域 join 请传 `key="room_id_canonical"`**
    （原始编号 `7`/`8` 与 `607`/`608` 不是一套编码，见 `analysis.rooms`）。
    **原始列一律不改**：解不出的行 `room_id_canonical` 是 NULL，不许拿默认值糊。
    """
    mine = [b for b in batches if b.get(key) == room]
    mine.sort(key=lambda b: b["entry_date"])
    picked = mine[-limit:]
    out: list[dict] = []
    for i, batch in enumerate(picked):
        nxt = picked[i + 1]["entry_date"] if i + 1 < len(picked) else None
        out.append({**batch, "until": nxt})
    out.reverse()          # 最新在前
    return out


def dumps(rows: list[dict]) -> str:
    return json.dumps(rows, ensure_ascii=False)

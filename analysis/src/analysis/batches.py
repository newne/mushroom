"""批次台账（pgsql）→ analysis.batches。

**批次边界只能来自台账**：先试过"按数据断档切段"，2026-10-12 在真机上被证否——
库房环境是连续采集的，换批时传感器并不停，25 天只会切出一段、跨了两批。

## 为什么不再走 `docker exec tools_mysql`

用户 2026-10-14 裁定：**`tools_mysql` 是业务数据库，禁止使用**。算法侧取数一律走
**pgsql**（compose 里的 `postgres_db`，库 `mushroom_algorithm`）。旧实现（`docker exec`
+ `mysql` 客户端 + `MYSQL_ROOT_PASSWORD`）已下线，连带"给容器挂 docker.sock"那条方案作废。

## 取数口径

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


def rows_to_batches(rows: list) -> list[dict]:
    """`(room_id, in_date)` 行 → 入库行。

    **`batch_no` 是 NULL**：`mogu-<id>` 那个显示名来自业务库的主键，pgsql 这两张表里没有
    对应 id，编一个就是造假。`batch_id` 用 `库房号|入库日期` 的既有写法（spec §2.6 表里
    第三种），要显示名另议（见票 13 的未决项）。
    """
    out: list[dict] = []
    for row in rows:
        if len(row) != 2:
            raise BatchFetchError(f"查询返回了 {len(row)} 列，期望 2 列（room_id, in_date）")
        room, in_date = row
        date = _as_date(in_date)
        if not room:
            raise BatchFetchError(f"库房号为空的批次行：{row!r}")
        out.append({
            "room_id": str(room),
            "entry_date": date,
            "batch_no": None,
            "batch_id": f"{room}|{date}",
        })
    out.sort(key=lambda r: (r["room_id"], r["entry_date"]))
    return out


def fetch_batches(*, dsn: str | None = None, runner=None) -> list[dict]:
    """从 pgsql 读台账。

    `runner` 可注入（测试用），形状：`(sql) -> list[tuple]`。
    生产走惰性 import 的 `psycopg`；连接串优先级：`--dsn` → `PGSQL_DSN_FILE` 文件内容
    → 环境变量 `PGSQL_DSN`。**没有就报错，绝不退回 `docker exec`、也绝不退回"按断档切段"。**
    """
    run = runner or _pgsql_runner(dsn)
    return rows_to_batches(run(SQL))


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


def window_of(batches: list[dict], *, room: str, limit: int = 3) -> list[dict]:
    """某库房最近 `limit` 个批次的窗口；窗口右端是下一批的入库日（含端点用开区间）。"""
    mine = [b for b in batches if b["room_id"] == room]
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

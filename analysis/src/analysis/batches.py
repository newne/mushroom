"""批次台账（`mogu.mo_gu_batch`）→ analysis.batches。

**为什么非要它**：先试过"按数据断档切段"，2026-10-12 在真机上被证否——
库房环境是连续采集的，换批时传感器并不停，25 天只会切出一段，跨了两批。
所以批次边界只能来自生产台账。

**为什么用 `docker exec` 而不是连 MySQL**：台账在宿主 docker 里的 `tools_mysql`，
口令只存在于容器 env（`MYSQL_ROOT_PASSWORD`）——与 `deploy-fetch-room` 同一套做法，
不把库口令搬到分析服务里。
"""

from __future__ import annotations

import json
import subprocess

DEFAULT_CONTAINER = "tools_mysql"
DEFAULT_DB = "mogu"
SQL = (
    "select id, code_num, in_time from mo_gu_batch "
    "where del_flag = 0 order by in_time asc"
)


class BatchFetchError(RuntimeError):
    """取不到台账时**不要**退回"按断档切段"——那会算出跨两批的假批次。"""


def parse_batches(tsv: str) -> list[dict]:
    """`id\tcode_num\tin_time` 的 TSV → 入库行。行数不符即报错，不静默错位。"""
    rows: list[dict] = []
    for lineno, line in enumerate(tsv.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise BatchFetchError(f"第 {lineno} 行字段数不是 3：{line!r}")
        batch_id, room, in_time = (p.strip() for p in parts)
        date = in_time.split(" ")[0]
        if not batch_id or not room or len(date) != 10:
            raise BatchFetchError(f"第 {lineno} 行不合法：{line!r}")
        rows.append({
            "room_id": room,
            "entry_date": date,
            "batch_no": f"mogu-{batch_id}",
            "batch_id": batch_id,
        })
    rows.sort(key=lambda r: (r["room_id"], r["entry_date"]))
    return rows


def fetch_batches(
    *, container: str = DEFAULT_CONTAINER, database: str = DEFAULT_DB, runner=None
) -> list[dict]:
    """从宿主容器里读台账。`runner` 可注入（测试用），形状：`(argv) -> str`。"""
    argv = [
        "docker", "exec", container, "sh", "-c",
        f'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "{SQL}" {database} 2>/dev/null',
    ]
    run = runner or _run
    out = run(argv)
    return parse_batches(out)


def _run(argv: list[str]) -> str:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BatchFetchError(f"执行失败：{exc}") from exc
    if done.returncode != 0:
        raise BatchFetchError(f"取台账失败（exit {done.returncode}）：{done.stderr.strip()[:200]}")
    return done.stdout


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

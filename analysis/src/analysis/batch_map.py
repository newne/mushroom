"""生成 `(room_id, in_date) → batch_no` 映射文件（Chief 2026-10-14 裁定的三步走第 1 步）。

**数据来源是我们自己的库**：`analysis.batches` 里既有行本来就带 `batch_no`
（历史上由 `docker exec tools_mysql` 那一跳写入的 `mogu-<id>`）。**不碰业务库、不碰 MySQL** ——
这不是"取数"，是从我们库里既有的行**反推**一份显式映射，落成仓库里的数据文件，
带**来源**与**生成时间**。

用法::

    python -m analysis.batch_map --out analysis/data/batch_no_map.json
    python -m analysis.batch_map --dsn <conninfo> --out analysis/data/batch_no_map.json

**读本库只有 PG**（票 28 contract）：`--dsn`/`--schema` 缺省走
`PGSQL_DSN_FILE`（文件优先）/`PGSQL_DSN` 与 `ANALYSIS_PG_SCHEMA`；没有本地文件库这个选项。

**缺映射**时 `batches.batch_no` 写 NULL（不编值），页面显示派生 id `库房号|入库日期`；
`fetch_batches_with_report()` 会给出"缺映射"的可见计数。长期解是让生产侧在 pgsql 补一列
真批次 id —— 那需要用户/业务侧协调，不归我们单方面决定（票 13 §3）。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from analysis import store
from analysis.batches import DEFAULT_MAP_PATH


def build_map(*, dsn: str | None = None, schema: str | None = None) -> dict[str, str]:
    """从本库 `batches` 表里「有 batch_no 的行」反推映射。缺失的行不猜。

    连接串与 schema 走与其它入口同一套缺省（`PGSQL_DSN_FILE` / `ANALYSIS_PG_SCHEMA`）。
    """
    conn = store.connect(dsn=dsn, schema=schema)
    try:
        mapping: dict[str, str] = {}
        for row in store.fetch_batches(conn):
            room = row.get("room_id")
            date = row.get("entry_date")
            batch_no = row.get("batch_no")
            if room and date and batch_no:
                mapping[f"{room}|{date}"] = str(batch_no)
        return mapping
    finally:
        conn.close()


def write_map(mapping: dict[str, str], out_path: str) -> Path:
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source": "analysis.batches 表里既有行（我们自己库；不碰 MySQL / 业务库）",
        "n": len(mapping),
        "map": dict(sorted(mapping.items())),
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="生成 batch_no 映射文件（从本库既有行反推）")
    p.add_argument("--dsn", help=store.CLI_DSN_HELP)
    p.add_argument("--schema", help=store.CLI_SCHEMA_HELP)
    p.add_argument("--out", default=DEFAULT_MAP_PATH, help="映射文件落点")
    args = p.parse_args(argv)

    mapping = build_map(dsn=args.dsn, schema=args.schema)
    target = write_map(mapping, args.out)
    print(f"[batch_map] 从本库反推 {len(mapping)} 条映射 → {target}")
    if not mapping:
        print("[batch_map] 本库里没有带 batch_no 的行：全部批次都会是 NULL（缺映射计数会显示）")


if __name__ == "__main__":
    main()

"""**一次性**搬迁工具（票 28 第 3 步）：把旧的 `mushrooms.db` 一次性搬进 PostgreSQL。

翻完这一票、旧库归档之后 —— **本文件连同这个目录一起删掉**。它不是服务代码的一部分，
没有别的模块 import 它，也不该长期留在仓库里。

## 为什么这里可以 `import sqlite3`

用户 2026-10-15 裁定「迁移并删除 sqlite 依赖，后续严禁使用本地文件数据库」⇒ 服务代码与
测试里一个 `sqlite3` 都不许再有。**唯一的例外就是这个一次性工具**：它要从旧库**读一次**
数据。为了不让这个例外蔓延，规矩是：

1. 只读旧库（`mode=ro` 打开，物理上写不进去），**不建表、不补列、不动它**；
2. 写侧一律走服务代码那份存储层 `analysis.store`（形状与生产写入一致）；
3. 服务代码与测试**不许** import 本文件；搬完就删。

## 幂等与对账

所有写入都是 upsert / 整体替换 ⇒ **可以重复跑**（搬迁期间旧库还在被写，跑第二遍搬增量）：

    # 看形状，不写库（不需要连接串）
    python -m analysis.tools.oneoff_sqlite_to_pg --source /opt/mushroom-analysis/mushrooms.db --dry-run
    # 真搬（连接串走 PGSQL_DSN_FILE，推荐；schema 缺省 analysis）
    python -m analysis.tools.oneoff_sqlite_to_pg --source /opt/mushroom-analysis/mushrooms.db

跑完打一张**逐表对账表**：旧库开始/结束时各数一次行数（差值就是搬迁期间的"实时增量"），
PG 侧写入前后各数一次，再比时间跨度。任何一表对不上 ⇒ 退出码 1。

基线（票 28 §4，2026-10-15 生产实测，合计 6,810 行）：images 3,925 / environment 2,712 /
rounds 82 / operations 62 / batches 27 / batches_state 1 / ranges_state 1 / measurements 0 /
ranges 0。搬迁当刻以实测为准 —— 那是量级与形状的基线，不是"必须等于"。

## 硬门

- **不写 TDengine**（ADR-0026：只读源）。本工具根本不 import 那条通路。
- **不碰 public**、不碰遗留表、不碰农场库：只往 `--schema`（缺省 `analysis`）里写，
  `public` 会被当场拒绝。
- 连接串只从环境变量/文件读，不打印、不进仓库、不写进命令行历史。

## 两处只有搬迁才会踩到的形状差

1. **images 的坐标**：旧库存 `y` / `z` 两列，而线格式是 `yz` 列表（`schema.flatten_image`
   认 `yz`）—— 不先拼回去，坐标会被摊平成 NULL。本文件的 `_write_images` 显式补上。
2. **measurements 的证据列**：写侧会跑服务代码本来就有的归一
   （`evidence.normalize_scale_source`），旧值 `bag_diameter` 会落成 `bag_standard`。
   这是应用一直以来的行为（不是搬迁引入的）；生产基线这张表 0 行。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: 一次性工具自己找 `analysis/src`（生产容器里 PYTHONPATH 已含它，这里只为开发机方便）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import store  # noqa: E402

#: 旧库的九张表（票 28 §4 的基线表就是这九张）。
TABLES = ("batches", "batches_state", "environment", "images", "measurements",
          "operations", "ranges", "ranges_state", "rounds")

#: 原样照抄的表 → 主键列（`ON CONFLICT` 用）：这三张是"某一刻的快照"或纯结果表，
#: 不借 upsert 语义重写它们。
_RAW_TABLES = {
    "ranges": ("stage", "param"),
    "batches_state": ("id",),
    "ranges_state": ("id",),
}

#: 走存储层写的表（与生产写入同形状）；`images` 单独走 `_write_images`。
_STORE_WRITERS = {
    "batches": store.upsert_batches,
    "environment": store.upsert_environment,
    "measurements": store.insert_measurements,
    "operations": store.upsert_operations,
    "rounds": store.insert_rounds,
}

#: 每张表用来对时间跨度的列（旧库与 PG 都读它）。
_TS_COLUMN = {"environment": "ts", "images": "ts", "rounds": "ts", "operations": "ts"}


@dataclass
class Reconciliation:
    """一张表的对账行：旧库前后各数一次，PG 前后各数一次。"""

    table: str
    source_before: int = 0
    source_after: int = 0
    target_before: int = 0
    target_after: int = 0
    written: int = 0
    source_span: tuple[str | None, str | None] = (None, None)
    target_span: tuple[str | None, str | None] = (None, None)
    notes: list[str] = field(default_factory=list)

    @property
    def live_growth(self) -> int:
        """搬迁期间旧库还在被写的那部分（实时增量）。"""
        return max(0, self.source_after - self.source_before)

    def verdict(self) -> tuple[bool, str]:
        """**只看事实**：PG 里至少要有搬迁开始那一刻的行；时间跨度不许倒退。"""
        if self.target_after < self.source_before:
            return False, f"PG 少 {self.source_before - self.target_after} 行"
        if self.target_after < self.target_before:
            return False, "PG 行数变少了（写入把旧行删了？）"
        if (self.source_span[1] and self.target_span[1]
                and self.target_span[1] < self.source_span[1]):
            return False, f"PG 最新时间 {self.target_span[1]} 早于旧库 {self.source_span[1]}"
        extra = (f"（搬迁期间旧库又长了 {self.live_growth} 行，再跑一次即可搬走）"
                 if self.live_growth else "")
        return True, "ok" + extra


def _sqlite_readonly(path: str) -> sqlite3.Connection:
    """**只读**打开旧库：物理上写不进去，搬迁不可能改坏它。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, table: str) -> int:
    if not _has_table(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _span(conn: sqlite3.Connection, table: str, column: str) -> tuple[str | None, str | None]:
    if not _has_table(conn, table):
        return (None, None)
    row = conn.execute(f"SELECT MIN({column}), MAX({column}) FROM {table}").fetchone()
    return (row[0], row[1])


def _read_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    """整表读成 dict 列表（旧库最多几千行，一次读完最简单也最不容易错）。"""
    if not _has_table(conn, table):
        return []
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


def _write_images(conn, rows: list[dict]) -> int:
    """图像索引：旧库把坐标存成 `y` / `z` 两列，线格式是 `yz` 列表。

    `store.insert_images` 会走 `schema.flatten_image`（认 `yz`）—— 不先拼回去的话，
    `y`/`z` 会被**摊平成 NULL**（老行明明有坐标）。这是搬迁才会踩到的形状差。
    """
    for row in rows:
        if not row.get("yz"):
            row["yz"] = [row.get("y"), row.get("z")]
    store.insert_images(conn, rows)
    return len(rows)


def _write_table(conn, table: str, rows: list[dict]) -> int:
    """一张表怎么落：三张快照表原样照抄，其余走存储层。"""
    if table in _RAW_TABLES:
        return _copy_raw(conn, table, rows, _RAW_TABLES[table])
    if table == "images":
        return _write_images(conn, rows)
    _STORE_WRITERS[table](conn, rows)
    return len(rows)


def _target_columns(conn, table: str) -> list[str]:
    """目标表在 PG 里的列（现读，不手抄）。"""
    with conn.raw.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
            (conn.schema, table),
        )
        return [row[0] for row in cur.fetchall()]


def _copy_raw(conn, table: str, rows: list[dict], key: tuple[str, ...]) -> int:
    """原样照抄：只写两边都有的列，冲突就整行覆盖（幂等）。"""
    if not rows:
        return 0
    target_cols = _target_columns(conn, table)
    cols = [c for c in target_cols if c in rows[0]]
    missing = sorted(set(target_cols) - set(cols))
    if missing:
        print(f"  ! {table}: 旧库缺列 {missing}（按 NULL 入库）")
    marks = ", ".join(f"%({c})s" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in key)
    sql = (f'INSERT INTO "{conn.schema}".{table} ({', '.join(cols)}) VALUES ({marks}) '
           f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {updates}")
    with conn.raw.cursor() as cur:
        cur.executemany(sql, [{c: row.get(c) for c in cols} for row in rows])
    return len(rows)


def _target_count(conn, table: str) -> int:
    with conn.raw.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) FROM "{conn.schema}".{table}')
        return int(cur.fetchone()[0])


def _target_span(conn, table: str, column: str) -> tuple[str | None, str | None]:
    with conn.raw.cursor() as cur:
        cur.execute(f'SELECT MIN({column}), MAX({column}) FROM "{conn.schema}".{table}')
        row = cur.fetchone()
        return (row[0], row[1])


def _print_report(rows: list[Reconciliation], *, schema: str | None, dry_run: bool) -> bool:
    """逐表对账表 + 结论。返回 `all_ok`。"""
    head = f"{'表':<16}{'旧库开始':>9}{'旧库结束':>9}{'实时增量':>9}"
    if not dry_run:
        head += f"{'PG 之前':>9}{'PG 之后':>9}{'本次写入':>9}"
    head += "  时间跨度（旧库）"
    print()
    print(head)
    print("-" * 100)
    total_src = total_tgt = total_written = 0
    all_ok = True
    for r in rows:
        total_src += r.source_before
        total_tgt += r.target_after
        total_written += r.written
        line = f"{r.table:<16}{r.source_before:>9}{r.source_after:>9}{r.live_growth:>9}"
        if not dry_run:
            line += f"{r.target_before:>9}{r.target_after:>9}{r.written:>9}"
        if r.source_span[1]:
            line += f"  {r.source_span[0]} → {r.source_span[1]}"
        if dry_run:
            print(line)
            continue
        ok, note = r.verdict()
        all_ok = all_ok and ok
        print(line)
        print(f"{'':<16}→ {note}")
    print("-" * 100)
    scope = "（dry-run：一个字都没写）" if dry_run else f"schema {schema}"
    print(f"合计：旧库 {total_src} 行；本次写入 {total_written} 行；PG 侧 {total_tgt} 行 {scope}")
    return all_ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="一次性搬迁：mushrooms.db（SQLite）→ PostgreSQL（票 28 第 3 步）"
    )
    ap.add_argument("--source", required=True, help="旧 SQLite 库路径（**只读**打开）")
    ap.add_argument("--dsn", help="PG 连接串；缺省走 PGSQL_DSN_FILE（文件优先）/ PGSQL_DSN")
    ap.add_argument("--schema", help="PG schema；缺省 ANALYSIS_PG_SCHEMA，再缺省 analysis")
    ap.add_argument("--dry-run", action="store_true", help="只数行、看形状，不连 PG、不写库")
    ap.add_argument("--tables", help=f"只搬这几张（逗号分隔）；缺省全部：{','.join(TABLES)}")
    args = ap.parse_args(argv)

    if not Path(args.source).exists():
        print(f"旧库不存在：{args.source}")
        return 2
    tables = tuple(t.strip() for t in args.tables.split(",")) if args.tables else TABLES

    source = _sqlite_readonly(args.source)
    try:
        report = []
        for table in tables:
            item = Reconciliation(table=table, source_before=_count(source, table))
            if table in _TS_COLUMN:
                item.source_span = _span(source, table, _TS_COLUMN[table])
            if not _has_table(source, table):
                item.notes.append("旧库没有这张表")
            report.append(item)
        payload = {item.table: _read_rows(source, item.table) for item in report}

        if args.dry_run:
            for item in report:
                item.source_after = _count(source, item.table)
            _print_report(report, schema=None, dry_run=True)
            print("dry-run：没有连接 PG，也没有写任何东西。")
            return 0

        conn = store.connect(dsn=args.dsn, schema=args.schema)
        try:
            if conn.schema == "public":
                print("拒绝：public 里有遗留业务表，analysis 的表必须进独立 schema")
                return 2
            print(f"目标：schema {conn.schema!r}（--dsn 未给时按 PGSQL_DSN_FILE/PGSQL_DSN 解析）")
            for item in report:
                item.target_before = _target_count(conn, item.table)
                item.written = _write_table(conn, item.table, payload[item.table])
                item.target_after = _target_count(conn, item.table)
                if item.table in _TS_COLUMN:
                    item.target_span = _target_span(conn, item.table, _TS_COLUMN[item.table])
            # 旧库**结束**时再数一次：这段时间里的新增就是"实时增量"
            for item in report:
                item.source_after = _count(source, item.table)
        finally:
            conn.close()
    finally:
        source.close()

    ok = _print_report(report, schema=args.schema, dry_run=False)
    if ok:
        print("对账通过。旧库若仍在被写：把增量再搬一次（本工具幂等），然后归档旧库。")
        return 0
    print("对账**没通过**：先查上面哪张表对不上，别急着切流量。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""把累积的 outbox 补传到 prod —— 端点确定后本该有的那条命令。

## 为什么要有它

ADR-0001 的 at-least-once 承诺是"同步失败就留着，端点好了再补传"，但在此之前**没有
一个正经的补传入口**：只能手写一段 Python 把 `JsonlStore` + `SyncClient` 拼起来。
现场最需要补传的时刻（端点刚接通、断网恢复）恰恰是最不该即兴写代码的时刻。

## 语义

* 推成功才动文件：`SyncClient.flush` 内部是"全部批次成功 ⇒ 清空"，中途失败原文件一个
  字节都不变，重跑即可（幂等由 prod 侧 `INSERT OR REPLACE` 保证）。
* 推成功后**归档**原文件到 `sent/`，而不是直接删。outbox 是同步成功后会被清空的记账本，
  而补传这种一次性操作总有人想回头核对"到底送出去了什么"——现场只剩空文件就查不了。
* `--dry-run` 只读不算：打印每个文件多少行、都是些什么 `kind`，**不发一个请求**。
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from patrol.links import RetryingTransport
from patrol.store import JsonlStore
from patrol.sync import PROD_INGEST_URL, SyncClient

DEFAULT_OUTBOX = "/opt/mushroom-patrol/outbox.jsonl"
ARCHIVE_DIRNAME = "sent"


@dataclass(frozen=True)
class FlushResult:
    """一个文件的补传结果。``rows`` 为 0 表示没东西可传（不是错误）。"""

    path: Path
    rows: int
    kinds: dict[str, int]
    sent: bool
    archived_to: Path | None = None


def describe(store: JsonlStore) -> tuple[int, dict[str, int]]:
    """只读地数一数待传内容（`kind` 分布），用于 `--dry-run` 与日志。"""
    rows = store.pending()
    return len(rows), dict(Counter(str(r.get("kind", "?")) for r in rows))


def archive_path(path: Path, archive_dir: Path | None) -> Path:
    """归档目标：默认同目录 `sent/`；重名则加 `-2`、`-3`……，绝不覆盖已有取证。"""
    target_dir = archive_dir if archive_dir is not None else path.parent / ARCHIVE_DIRNAME
    candidate = target_dir / path.name
    n = 2
    while candidate.exists():
        candidate = target_dir / f"{path.name}-{n}"
        n += 1
    return candidate


def flush_file(path: Path, *, endpoint: str, transport, archive_dir: Path | None = None,
               do_archive: bool = True) -> FlushResult:
    """补传一个 outbox 文件。传输失败会抛出，且**不改动**原文件。"""
    store = JsonlStore(str(path))
    rows, kinds = describe(store)
    if rows == 0:
        return FlushResult(path=path, rows=0, kinds={}, sent=False)

    # 必须在 flush **之前**把原文抓在手里：`SyncClient.flush` 成功后会 `replace([])`
    # 把文件清空，之后再 `shutil.move` 搬走的就只是一个空壳（这个坑写这模块时踩过一次，
    # test_flush_pushes_then_archives 现在钉着它）。
    raw = path.read_text(encoding="utf-8")

    client = SyncClient(endpoint=endpoint, transport=transport)
    client.flush(store)          # 失败抛错 ⇒ 文件保持原样

    archived = None
    if do_archive:
        archived = archive_path(path, archive_dir)
        archived.parent.mkdir(parents=True, exist_ok=True)
        archived.write_text(raw, encoding="utf-8")
    return FlushResult(path=path, rows=rows, kinds=kinds, sent=True, archived_to=archived)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="deploy-flush-outbox",
        description="把本地累积的 outbox 补传到 prod /ingest（成功即归档原文件）",
    )
    ap.add_argument("--outbox", action="append", default=None,
                    help=f"outbox 文件，可重复（默认 {DEFAULT_OUTBOX}）")
    ap.add_argument("--ingest", default=PROD_INGEST_URL, help="prod 接收端点")
    ap.add_argument("--archive-dir", default=None,
                    help=f"归档目录（默认各文件同级的 {ARCHIVE_DIRNAME}/）")
    ap.add_argument("--no-archive", action="store_true", help="推成功后直接清空，不归档")
    ap.add_argument("--dry-run", action="store_true", help="只报告待传内容，不发请求")
    ap.add_argument("--attempts", type=int, default=3, help="每个批次的尝试次数")
    return ap


def make_transport(endpoint: str, *, attempts: int):
    """真实 HTTP 传输（延迟导入 httpx：`--dry-run` 无需装 httpx）。"""
    from deploy.transport import HttpxTransport, host_port

    return RetryingTransport(
        HttpxTransport(allowed_hosts={host_port(endpoint)}), attempts=attempts, backoff_s=2.0
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = [Path(p) for p in (args.outbox or [DEFAULT_OUTBOX])]
    archive_dir = Path(args.archive_dir) if args.archive_dir else None

    transport = None
    if not args.dry_run:
        try:
            transport = make_transport(args.ingest, attempts=args.attempts)
        except RuntimeError as e:      # 缺 httpx 之类
            print(f"! {e}", file=sys.stderr)
            return 2

    total = 0
    failed = 0
    for path in paths:
        if not path.exists():
            print(f"- {path}：不存在，跳过")
            continue
        if args.dry_run:
            rows, kinds = describe(JsonlStore(str(path)))
            print(f"- {path}：{rows} 行待传 {kinds or ''}")
            total += rows
            continue
        try:
            r = flush_file(path, endpoint=args.ingest, transport=transport,
                           archive_dir=archive_dir, do_archive=not args.no_archive)
        except Exception as e:         # noqa: BLE001 - 传输层任何失败都不该动文件
            print(f"! {path}：补传失败，文件保持原样（{type(e).__name__}: {e}）", file=sys.stderr)
            failed += 1
            continue
        if r.rows == 0:
            print(f"- {path}：没有待传内容")
        else:
            where = f" → 归档 {r.archived_to}" if r.archived_to else "（未归档）"
            print(f"✔ {path}：已补传 {r.rows} 行 {r.kinds}{where}")
            total += r.rows

    if args.dry_run:
        print(f"（演练）合计待传 {total} 行，未发送任何请求")
        return 0
    print(f"补传完成：{total} 行，{failed} 个文件失败")
    return 2 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

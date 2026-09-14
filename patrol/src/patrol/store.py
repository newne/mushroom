"""本地 outbox：待同步记录的 JSONL 存储（票 06，评审 #5 加固耐久性）。

- append 直接追加（进程崩溃最多撕裂最后一行，不再整体重写）；
- pending() 读取时容忍并隔离撕裂行：坏行丢弃并计数（daemon 负责日志上报），
  好数据照常同步——outbox 不再可能被一行坏 JSON 永久卡死。
"""

from __future__ import annotations

import json
from pathlib import Path


class JsonlStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self.corrupt_lines = 0  # 最近一次 pending() 读到的坏行数（自愈前）

    def append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def pending(self) -> list[dict]:
        if not self.path.exists():
            return []
        records: list[dict] = []
        good_lines: list[str] = []
        corrupt = 0
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
                good_lines.append(line)
            except json.JSONDecodeError:
                corrupt += 1  # 撕裂/损坏行：隔离，不阻塞好数据
        self.corrupt_lines = corrupt
        if corrupt:
            self._write_text("\n".join(good_lines) + ("\n" if good_lines else ""))
        return records

    def replace(self, records: list[dict]) -> None:
        self._write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))

    def __len__(self) -> int:
        return len(self.pending())

    def _write_text(self, payload: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(payload, encoding="utf-8")

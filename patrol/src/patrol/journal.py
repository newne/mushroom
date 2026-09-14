"""轮次日志（run journal）：一轮巡检的**现场取证**，与 outbox 分工不同。

## 为什么必须有它

2026-09-13 那次整轮上机：进程跑了 2 分 46 秒、抓到第 14 站被中断，结果
**outbox 里一条记录都没有**（`outbox_full.jsonl` 根本不存在）——现场能拿到的
只有"它死了"这一个事实，连它走到哪一站、为什么死都查不出来。

根因是 `PatrolDaemon.run_cycle` 只在 `PatrolRound.run()` **正常返回之后**才写第一条
记录。任何异常/中断路径 ⇒ 零记录、零文件、零痕迹。对一台会自己动 4.5 米导轨的机器，
"失败静默"是最坏的可观测性形态。

## 与 outbox 的分工

| | outbox（`JsonlStore`） | 本轮日志（本模块） |
| --- | --- | --- |
| 用途 | **要同步到 prod 的数据**（round 汇总 / 图像索引 / 测量值） | **本地取证**，不外传 |
| 生命周期 | 同步成功后清空（at-least-once 待补传） | 每轮一个文件，只增不删，按天留存 |
| 内容 | 业务行 | 开始 / 逐站心跳 / 结束（含异常文本与堆栈） |

刻意**不**把心跳塞进 outbox：那会给 prod 的 `/ingest` 灌一堆它不关心的行，而且
同步成功会清空 outbox，取证记录反而跟着没了。

## fsync 是这里的重点

每写一行都 `flush()` + `os.fsync()`。这一条不是洁癖：被 `kill -9` / 断电的进程
**不会**把缓冲里的字节交给内核，而本模块存在的全部意义就是"进程没了好歹留下它走到哪"。
只 flush 不 fsync 的话，进程级崩溃保得住、机器级断电保不住；成本是每站一次 fsync
（一轮 60 次），相对单站 8.8 秒可忽略。
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import IO, Self

UNKNOWN = "unknown"


class RoundJournal:
    """一轮一个 JSONL 取证文件：``<dir>/<timestamp>-<pid>.jsonl``。

    文件名带 pid 是为了区分**同一秒启动的两个进程**——并发驱动同一台控制器已经够糟了，
    别再让两者的取证记录互相覆盖。
    """

    def __init__(self, directory: str | Path, *, now: datetime | None = None,
                 pid: int | None = None) -> None:
        self.dir = Path(directory)
        self.started_at = now or datetime.now()
        self.pid = os.getpid() if pid is None else pid
        self.path = self.dir / f"{self.started_at:%Y%m%d-%H%M%S}-{self.pid}.jsonl"
        self._fh: IO[str] | None = None
        self._ended = False

    # ---------- 写 ----------

    def append(self, record: dict) -> None:
        """追加一行并落盘（fsync）。写失败**不抛**——取证不该拖垮巡检。"""
        if self._fh is None:
            self.dir.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
        try:
            self._fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except OSError:
            # 磁盘满 / 只读挂载：取证写不进去就算了，绝不能因此中断巡检
            pass

    def start(self, *, stations: int, extra: dict | None = None) -> None:
        self.append({"event": "start", "ts": self._now(), "pid": self.pid,
                     "stations": stations, **(extra or {})})

    def station(self, index: int, total: int, **fields) -> None:
        """逐站心跳：这一站是什么、结果如何、耗时多少。"""
        self.append({"event": "station", "index": index, "total": total, **fields})

    def end(self, **fields) -> None:
        """本轮结束（正常返回、被中止、或捕获到异常都走这里）。"""
        self._ended = True
        self.append({"event": "end", "ts": self._now(), **fields})

    def exception(self, exc: BaseException, *, where: str) -> None:
        """异常路径：把类型、文本与堆栈都留下——否则现场只剩"它死了"。"""
        self.end(
            status="exception",
            where=where,
            error=f"{type(exc).__name__}: {exc}",
            traceback="".join(traceback.format_exception(exc))[-4000:],
        )

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    @property
    def ended(self) -> bool:
        return self._ended

    # ---------- 内部 ----------

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

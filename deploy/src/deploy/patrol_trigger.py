"""触发"跑一轮巡检"：单飞（single-flight）+ 后台执行 + 可查询状态。

## 为什么触发是写文件、不是直接调硬件

控制器是**单会话**设备，而 `console` 容器**不挂**厂商 SDK、也不该碰控制器（页面的状态
查询就是靠这条活着的）。所以"跑一轮"这件事不在这里执行，而是**投一个触发请求进去**：

* 触发请求 = 共享数据目录里的一个 JSON 文件（`data/trigger/run.json`）；
* 真正跑轮的是**持有硬件的那一个进程**（`patrol-serve` 角色，见 entrypoint）；
* 本模块只负责**收请求、去重、记账**，因此天然不阻塞 —— 这正是调度器要的：
  它 `max_instances=1`、`misfire_grace_time=300s`，而我们一轮 11 分钟，job 里等结果
  会让下一次触发被判 misfire 丢掉。

单飞的含义：同一时刻**只允许一个待处理请求**。重复触发返回 409 + 现有请求，
而不是排队——排队会让 3 小时后那次与上一次挤在一起，最后连跑两轮 22 分钟。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

#: 请求多久还没被消费就算过期（消费方每几秒轮询一次；超过这个时间说明它没在跑）
STALE_AFTER_S = 900.0


@dataclass
class RunRequest:
    """一次"跑一轮"的请求。`id` 用时间戳，便于现场按时间对账。"""

    id: str
    created_at: str
    created_by: str = "manual"
    reason: str = ""
    consumed_at: str | None = None
    note: str = ""

    @property
    def pending(self) -> bool:
        return self.consumed_at is None


@dataclass
class TriggerStore:
    """触发请求的落盘与读走（两端都是文件操作，坏文件一律当作"没有请求"）。"""

    dir_path: str = "/app/data/trigger"
    file_name: str = "run.json"
    history_name: str = "runs.jsonl"
    now: object = field(default=datetime.now)

    @property
    def path(self) -> Path:
        return Path(self.dir_path) / self.file_name

    def _history(self) -> Path:
        return Path(self.dir_path) / self.history_name

    def current(self) -> RunRequest | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return RunRequest(**raw)
        except TypeError:
            return None

    def age_s(self, req: RunRequest | None = None) -> float | None:
        """请求投出多久了。**用注入的时钟**，不掺 `time.time()`——
        两套时钟混用会让"过期"的判断随机器时间漂移，也让测试失去意义。
        """
        req = req or self.current()
        if req is None:
            return None
        try:
            return (self.now() - datetime.fromisoformat(req.created_at)).total_seconds()
        except (TypeError, ValueError):
            return None

    def is_stale(self, req: RunRequest | None = None) -> bool:
        age = self.age_s(req)
        return age is not None and age > STALE_AFTER_S

    def request(self, *, by: str = "scheduler", reason: str = "") -> tuple[RunRequest, bool]:
        """投一个请求。返回 `(请求, 是否新建)`；已有未消费的请求时**不覆盖**它。"""
        existing = self.current()
        if existing is not None and existing.pending and not self.is_stale(existing):
            return existing, False
        stamp = self.now()
        req = RunRequest(
            id=stamp.strftime("%Y%m%d-%H%M%S"),
            created_at=stamp.isoformat(timespec="seconds"),
            created_by=by,
            reason=reason,
            note="上一次请求已过期，这一条取而代之" if existing is not None else "",
        )
        p = self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        # 原子落盘：消费方随时可能来读，读到半截 JSON 会被当成"没有请求"
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(asdict(req), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        with self._history().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(req), ensure_ascii=False) + "\n")
        return req, True

    def consume(self) -> RunRequest | None:
        """取走待处理请求并标记已消费（消费方调用）。返回 None 表示没有待处理的。"""
        req = self.current()
        if req is None or not req.pending:
            return None
        req.consumed_at = self.now().isoformat(timespec="seconds")
        try:
            self.path.write_text(json.dumps(asdict(req), ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        except OSError:
            pass
        return req

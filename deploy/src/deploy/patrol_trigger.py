"""触发"跑一轮"：单飞（single-flight）+ 后台执行 + 可查询状态 + **时间偏置**。

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

## 时间偏置（2026-10-12 加）：加湿期不许拍

现场口径：**加湿过程水汽重，图像过曝、对比度低；开蓝光也一样**。这种时候拍出来的
48 张判不了长势，还要白动机架 11 分钟。所以触发要能"晚点再跑"：

* **单次偏置** `defer_s`：这一轮晚 N 秒再跑（`POST /api/patrol/run?defer_s=1200`）；
* **保持（hold）**：一段时间内"现在不该拍"——这期间来的触发**一律**等到保持结束后
  再偏置 `defer_s`。保持必须对**调度器**也生效：它打的 `/api/patrol/run` 不带参数，
  偏置只能在服务端按保持状态算，靠调用方自觉是没用的。

偏置写在请求自己的 `not_before` 上，**不在 console 的内存里**：console 重启不丢，
消费方 `consume()` 到点才取得到。设保持时会把**已在排队**的请求一起推后——否则
"先触发、后设保持"这个常见顺序会让那一轮照原时间撞上进湿段。

两个容易写错的地方，代码里都有对应约束：

1. `STALE_AFTER_S` 必须从 `max(created_at, not_before)` 起算。若仍从 `created_at`
   算，一个偏置 20 分钟的请求在第 15 分钟就被判过期，随后到的触发会把它**替换成一条
   立即执行的新请求**——偏置被静默吃掉，而且页面上看不出来。
2. 单次 `defer_s` 与保持期**取更大**，不是覆盖：保持是"现在不该拍"的强约束，
   单次参数只能延后、不能提前。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

#: 请求多久还没被消费就算过期（消费方每几秒轮询一次；超过这个时间说明它没在跑）。
#: 偏置过的请求从 `not_before` 起算，所以"偏置 20 分钟"不会被这条误杀。
STALE_AFTER_S = 900.0

#: 保持期间默认再偏置多少秒。现场口径：20 分钟。
DEFAULT_DEFER_S = 1200.0

#: 单次触发允许的最大偏置（1 小时）。再长就不该叫"偏置"，该叫改排班——
#: 那种需求应该去调调度器，而不是在这里堆一个 6 小时的定时器。
MAX_DEFER_S = 3600.0


@dataclass
class RunRequest:
    """一次"跑一轮"的请求。`id` 用时间戳，便于现场按时间对账。

    `not_before` = 这一轮**最早**可以开始执行的时刻（ISO）；None 表示立即。
    它由 `defer_s` 与保持期共同决定，落盘后 console 重启也不丢。
    """

    id: str
    created_at: str
    created_by: str = "manual"
    reason: str = ""
    consumed_at: str | None = None
    note: str = ""
    not_before: str | None = None

    @property
    def pending(self) -> bool:
        return self.consumed_at is None

    @property
    def due_at(self) -> str:
        """这一轮的应到时刻：偏置过的算 `not_before`，没偏置的算创建时刻。"""
        return self.not_before or self.created_at


@dataclass
class TriggerStore:
    """触发请求与保持状态的落盘/读走（两端都是文件操作，坏文件一律当作"没有"）。"""

    dir_path: str = "/app/data/trigger"
    file_name: str = "run.json"
    history_name: str = "runs.jsonl"
    hold_name: str = "hold.json"
    now: object = field(default=datetime.now)

    # ---------- 路径 ----------

    @property
    def path(self) -> Path:
        return Path(self.dir_path) / self.file_name

    def _history(self) -> Path:
        return Path(self.dir_path) / self.history_name

    def _hold_path(self) -> Path:
        return Path(self.dir_path) / self.hold_name

    # ---------- 请求 ----------

    def current(self) -> RunRequest | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return RunRequest(**raw)
        except TypeError:
            return None

    def _base(self, req: RunRequest) -> datetime | None:
        """请求的起算时刻：偏置过的从 `not_before`，没偏置的从 `created_at`。

        这一条是偏置能工作的前提，见模块 docstring 第 1 条。
        """
        for key in (req.not_before, req.created_at):
            if key:
                try:
                    return datetime.fromisoformat(key)
                except ValueError:
                    continue
        return None

    def age_s(self, req: RunRequest | None = None) -> float | None:
        """请求投出多久了。**用注入的时钟**，不掺 `time.time()`——
        两套时钟混用会让"过期"的判断随机器时间漂移，也让测试失去意义。
        """
        req = req or self.current()
        if req is None:
            return None
        base = self._base(req)
        return None if base is None else (self.now() - base).total_seconds()

    def is_stale(self, req: RunRequest | None = None) -> bool:
        age = self.age_s(req)
        return age is not None and age > STALE_AFTER_S

    def wait_s(self, req: RunRequest | None = None) -> float:
        """还要多久才到点（偏置剩余秒数）；已到点返回 0。"""
        req = req or self.current()
        if req is None:
            return 0.0
        base = self._base(req)
        if base is None:
            return 0.0
        return max(0.0, (base - self.now()).total_seconds())

    def is_due(self, req: RunRequest | None = None) -> bool:
        return self.wait_s(req) <= 0.0

    def request(self, *, by: str = "scheduler", reason: str = "",
                defer_s: float = 0.0) -> tuple[RunRequest, bool]:
        """投一个请求。返回 `(请求, 是否新建)`；已有未消费的请求时**不覆盖**它。"""
        existing = self.current()
        if existing is not None and existing.pending and not self.is_stale(existing):
            return existing, False
        stamp = self.now()
        defer = self._effective_defer(defer_s)
        due = stamp + timedelta(seconds=defer) if defer > 0 else None
        req = RunRequest(
            id=stamp.strftime("%Y%m%d-%H%M%S"),
            created_at=stamp.isoformat(timespec="seconds"),
            created_by=by,
            reason=reason,
            note="上一次请求已过期，这一条取而代之" if existing is not None else "",
            not_before=due.isoformat(timespec="seconds") if due else None,
        )
        self._write(req)
        with self._history().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(req), ensure_ascii=False) + "\n")
        return req, True

    def consume(self) -> RunRequest | None:
        """取走待处理请求并标记已消费（消费方调用）。

        **没到 `not_before` 就取不到**：偏置过的请求会一直躺在这里，消费方每几秒
        来问一次，到点才领走。返回 None 表示"现在没有可跑的请求"——可能是真没有，
        也可能是还没到点，消费方不需要分辨这两者。
        """
        req = self.current()
        if req is None or not req.pending or not self.is_due(req):
            return None
        req.consumed_at = self.now().isoformat(timespec="seconds")
        try:
            self.path.write_text(json.dumps(asdict(req), ensure_ascii=False, indent=1),
                                 encoding="utf-8")
        except OSError:
            pass
        return req

    def _write(self, req: RunRequest) -> None:
        p = self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        # 原子落盘：消费方随时可能来读，读到半截 JSON 会被当成"没有请求"
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(asdict(req), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)

    # ---------- 保持（加湿等：这段时间别拍） ----------

    def hold(self) -> dict | None:
        """当前生效的保持；没有、已过期、或文件坏了都算没有。"""
        try:
            raw = json.loads(self._hold_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or not raw.get("until"):
            return None
        try:
            until = datetime.fromisoformat(raw["until"])
        except ValueError:
            return None
        return None if until <= self.now() else raw

    def hold_summary(self) -> dict:
        """给页面/接口用的保持状态（带上剩余秒数，省得前端再算一次时钟）。"""
        hold = self.hold()
        if hold is None:
            return {"active": False}
        try:
            left = max(0.0, (datetime.fromisoformat(hold["until"]) - self.now()).total_seconds())
        except ValueError:
            left = 0.0
        return {"active": True, **hold, "seconds_left": left}

    def set_hold(self, *, until: datetime, defer_s: float = DEFAULT_DEFER_S,
                 reason: str = "", by: str = "operator") -> dict:
        """设一个保持，并把**已在排队**的请求一起推后（先触发、后设保持是常见顺序）。"""
        if defer_s < 0 or defer_s > MAX_DEFER_S:
            raise ValueError(f"defer_s 必须在 0…{MAX_DEFER_S:.0f} 秒之间")
        if until <= self.now():
            raise ValueError("until 必须在将来：保持过去的时刻等于没有保持")
        hold = {
            "until": until.isoformat(timespec="seconds"),
            "defer_s": float(defer_s),
            "reason": reason,
            "set_by": by,
            "set_at": self.now().isoformat(timespec="seconds"),
        }
        p = self._hold_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(hold, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        self._push_pending()
        return hold

    def clear_hold(self) -> bool:
        """解除保持；返回"之前有没有生效的保持"。**不动已偏置的请求**——
        那条请求的 `not_before` 是它自己的事实，解除保持只影响之后来的触发。
        """
        try:
            self._hold_path().unlink()
            return True
        except OSError:
            return False

    def _push_pending(self) -> None:
        """把已在排队的请求推到保持期之后：否则它会照原时间撞上进湿段。"""
        req = self.current()
        if req is None or not req.pending:
            return
        want = self.now() + timedelta(seconds=self._effective_defer(0.0))
        base = self._base(req)
        if base is None or base >= want:
            return
        req.not_before = want.isoformat(timespec="seconds")
        self._write(req)

    def _effective_defer(self, defer_s: float) -> float:
        """这次触发实际偏置多少秒：单次传参与保持期**取更大**的那个。"""
        defer = max(0.0, float(defer_s))
        hold = self.hold()
        if hold is None:
            return defer
        try:
            until = datetime.fromisoformat(hold["until"])
        except ValueError:
            return defer
        wait = max(0.0, (until - self.now()).total_seconds())
        return max(defer, wait + float(hold.get("defer_s", DEFAULT_DEFER_S)))

    # ---------- 汇总 ----------

    def state(self) -> dict:
        """一次把"请求 + 偏置 + 保持"说清楚，给 `/api/patrol/run` 与 `/api/status` 用。"""
        req = self.current()
        hold = self.hold_summary()
        if req is None:
            return {"job": None, "pending": False, "age_s": None, "stale": False,
                    "due_at": None, "wait_s": None, "hold": hold}
        return {
            "job": {**asdict(req), "pending": req.pending},
            "pending": bool(req.pending),
            "age_s": self.age_s(req),
            "stale": self.is_stale(req),
            "due_at": req.due_at,
            "wait_s": self.wait_s(req) if req.pending else None,
            "hold": hold,
        }

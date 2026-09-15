"""手动控制的通道：会话 + 指令 + 急停（console 与执行方之间的唯一接口）。

## 为什么手动控制也走文件

FMC4030 是**单会话**控制器，而按 ADR-0013 的语义，巡检期间它归执行方（`patrol-serve`）。
如果 console 自己也去连控制器，两者必然抢：轻则手动指令失败，重则把正在跑的那一轮
踢掉，60 张图作废。所以：

* **只有执行方碰控制器**（它已经是唯一持有硬件、且单实例的那个进程）；
* console 把"想做什么"写进 `data/cmd/`，执行方轮询领走、执行、写回结果；
* 页面因此是**异步**的：提交后轮询结果，而不是等一个同步响应。

## 急停是唯一的例外

急停不能排在队列里（"前面还有一条 goto 在跑"是不可接受的答案），也不能等执行方
按 5 秒的轮询节奏才发现。所以急停写成**一个独立的标志文件**，执行方在
**每次动作之前、以及移动过程中**都要看它一眼；`stop` 也永远不受"同时只允许一条指令"
的限制。这个上限约 1 秒的延迟是这条路线的固有代价，写在这里以免被当成"实时急停"。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

SESSION_TTL_S = 300          # 会话有效期（秒）：到期自动退回，避免"人走了还在授权"
MOTION_KINDS = ("goto", "jog", "home")     # 会动机构的指令（受急停与会话双重约束）
ALL_KINDS = (*MOTION_KINDS, "lamp", "capture", "stop")
#: 急停是**闩锁**：置位期间只放行"停下来"与"关灯"，其余一律拒绝（ADR-0016）。
#: `stop` 自己要能再发一次（可能第一次没送达）；关灯是为了让人能安全地靠近设备。
ESTOP_ALLOWED = ("stop", "lamp_off")


def estop_allows(kind: str, args: dict | None = None) -> bool:
    """急停置位时这条指令还允许吗？

    判断只有这一处：console 用它给出**当场**的话（页面要立刻知道为什么不能动），
    执行方用它做**最终**把关（指令可能是别处写进来的）。两处各写一份，迟早会漂移成
    "页面说能、执行方说不能"。
    """
    if kind == "stop":
        return True
    return kind == "lamp" and (args or {}).get("on") is False


@dataclass
class Command:
    id: str
    kind: str
    args: dict = field(default_factory=dict)
    by: str = "operator"
    session: str = ""
    created_at: str = ""
    started_at: str | None = None

    @property
    def moving(self) -> bool:
        return self.kind in MOTION_KINDS


@dataclass
class CommandResult:
    id: str
    ok: bool
    detail: str = ""
    ended_at: str = ""
    kind: str = ""
    args: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)


@dataclass
class Session:
    """操作者对导轨的独占持有（ADR-0004 的会话，一期只做超时与续期）。"""

    token: str
    opened_at: str
    expires_at: str

    @staticmethod
    def open(token: str, *, now: datetime, ttl_s: int = SESSION_TTL_S) -> Session:
        return Session(token=token, opened_at=now.isoformat(timespec="seconds"),
                       expires_at=(now + timedelta(seconds=ttl_s)).isoformat(timespec="seconds"))

    def valid(self, *, now: datetime) -> bool:
        try:
            return now < datetime.fromisoformat(self.expires_at)
        except ValueError:
            return False

    def renewed(self, *, now: datetime, ttl_s: int = SESSION_TTL_S) -> Session:
        return Session.open(self.token, now=now, ttl_s=ttl_s)


class ManualChannel:
    """`data/cmd/` 下的指令与结果（原子落盘；坏文件一律当作"没有"）。"""

    def __init__(self, dir_path: str = "/app/data/cmd", *, now=datetime.now):
        self.dir_path = Path(dir_path)
        self.now = now

    # ---------- 路径 ----------

    @property
    def cmd_path(self) -> Path:
        return self.dir_path / "cmd.json"

    @property
    def result_path(self) -> Path:
        return self.dir_path / "result.json"

    @property
    def session_path(self) -> Path:
        return self.dir_path / "session.json"

    @property
    def estop_path(self) -> Path:
        return self.dir_path / "ESTOP"

    def _write(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    def _read(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    # ---------- 会话 ----------

    def open_session(self, token: str) -> Session:
        s = Session.open(token, now=self.now())
        self._write(self.session_path, asdict(s))
        return s

    def session(self) -> Session | None:
        raw = self._read(self.session_path)
        if not raw:
            return None
        try:
            return Session(**raw)
        except TypeError:
            return None

    def active_session(self) -> Session | None:
        s = self.session()
        return s if s is not None and s.valid(now=self.now()) else None

    def renew_session(self) -> Session | None:
        s = self.active_session()
        if s is None:
            return None
        return self.open_session(s.token)

    def close_session(self) -> None:
        try:
            self.session_path.unlink()
        except OSError:
            pass

    # ---------- 急停 ----------

    def raise_estop(self, *, by: str = "operator", reason: str = "") -> None:
        """置急停标志。**不受任何队列/会话限制**——它必须永远能置上。"""
        self._write(self.estop_path, {"by": by, "reason": reason,
                                      "at": self.now().isoformat(timespec="seconds")})

    def estop(self) -> dict | None:
        return self._read(self.estop_path)

    def raised(self) -> bool:
        return self.estop_path.exists()

    def clear_estop(self) -> None:
        try:
            self.estop_path.unlink()
        except OSError:
            pass

    # ---------- 指令 ----------

    def inflight(self) -> Command | None:
        """当前未被取走结果的那条指令（同时只允许一条）。"""
        raw = self._read(self.cmd_path)
        if not raw:
            return None
        try:
            cmd = Command(**raw)
        except TypeError:
            return None
        done = self._read(self.result_path)
        if done and done.get("id") == cmd.id:
            return None
        return cmd

    def submit(self, kind: str, *, args: dict | None = None, by: str = "operator",
               session: str = "") -> tuple[Command, str]:
        """提交一条指令。返回 `(指令, 说明)`；被拒时指令是**当前在跑的那条**。

        会在三种情况下拒绝：机构正在动一条别的指令、需要会话但会话无效、
        kind 不认识。急停不走这里（见 `raise_estop`）。
        """
        if kind not in ALL_KINDS:
            raise ValueError(f"未知指令 {kind!r}（可用：{'/'.join(ALL_KINDS)}）")
        busy = self.inflight()
        if busy is not None:
            return busy, f"上一条指令 {busy.id}（{busy.kind}）还没结束"
        if kind in MOTION_KINDS and self.active_session() is None:
            raise PermissionError("没有有效会话：手动移动需要先接管（会话 5 分钟，指令可续期）")
        now = self.now()
        cmd = Command(id=now.strftime("%Y%m%d-%H%M%S-%f")[:21], kind=kind, args=args or {},
                      by=by, session=session, created_at=now.isoformat(timespec="seconds"))
        self._write(self.cmd_path, asdict(cmd))
        try:
            self.result_path.unlink()      # 清掉上一条的结果，避免与这条混淆
        except OSError:
            pass
        return cmd, "已提交"

    def claim(self) -> Command | None:
        """执行方领走指令（标记 started_at）。已经在跑的同一条不会被重复领。"""
        cmd = self.inflight()
        if cmd is None or cmd.started_at is not None:
            return None
        cmd.started_at = self.now().isoformat(timespec="seconds")
        self._write(self.cmd_path, asdict(cmd))
        return cmd

    def complete(
        self, cmd: Command, *, ok: bool, detail: str = "", data: dict | None = None
    ) -> CommandResult:
        res = CommandResult(
            id=cmd.id,
            ok=ok,
            detail=detail,
            ended_at=self.now().isoformat(timespec="seconds"),
            kind=cmd.kind,
            args=dict(cmd.args),
            data=data or {},
        )
        self._write(self.result_path, asdict(res))
        return res

    def result(self) -> CommandResult | None:
        raw = self._read(self.result_path)
        if not raw:
            return None
        try:
            return CommandResult(**raw)
        except TypeError:
            return None

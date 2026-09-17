"""手动指令的**执行方**：把 `data/cmd/` 里的一条指令变成控制器上的一个动作。

`deploy.manual.ManualChannel` 定义的是"怎么写、怎么读"（通道），本模块定义的是
"读到之后做什么"（执行）。分开的理由：通道要能被 console（无控制器）与执行方
（有控制器）同时使用，而执行只可能发生在持有硬件的那个进程里。

## 四条硬规矩

1. **急停先看，动作后发**。每条指令在**下发任何运动之前**先看急停标志；运动过程中
   由 `patrol.fmc.client` 的 ``abort`` 回调持续检查（等待原语的轮询周期，实测量级
   0.1–0.2 s）。急停置位期间只允许 `stop` 与"关灯"这两件事，其余一律拒绝——**急停
   是个闩锁，不是一次性动作**，必须显式复位才能再动。
2. **指令要新鲜**。提交后超过 ``STALE_COMMAND_S`` 才被领走的指令**不执行**：手动操作
   是"此时此地"的动作，十分钟前点的"点动 5mm"现在执行是最坏的一种惊喜。执行方按
   5 秒节奏轮询，正常指令都远在阈值内；超过阈值只可能意味着"中间有一轮巡检挡着"。
3. **一次只做一件**。单飞由通道保证（`submit` 在 inflight 时就拒绝），这里不再排队。
4. **行程与轴名都要校验**。越界目标在**下发之前**拒绝（`check_travel`），不认识轴名
   直接报错——不做"猜一个最接近的轴"这种事。

## 手动抓拍的口径

`capture` **不移动机构**：拍的就是"现在这个位置"，站位号是操作者给的归属（他正站在
那一站前面调），而记录里的坐标是**实际拍摄位置**。两者不一致时以坐标为准——照片在
MinIO 里叫什么名字是给人认的，坐标才是可回溯的事实。落索引进 outbox 后随即同步 prod，
所以"刚拍的那张"立刻能在历史里看到（`docs/patrol/console-ui/spec.md` §7）。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from patrol.capture_client import CaptureClient, CaptureError
from patrol.fmc import (
    Fmc4030,
    FmcError,
    HomeTimeoutError,
    MotionAborted,
    MotionTimeoutError,
)
from patrol.fmc.status import MachineStatus
from patrol.motion_profile import HOME_DIR_NEGATIVE, M1
from patrol.stations import Station, image_object_name

from deploy.manual import Command, ManualChannel, estop_allows

MANUAL_TIMEOUT_S = 60.0     # 手动定位/回零的到位确认预算（比巡检档宽松：人看着）
JOG_WAIT_S = 30.0           # 点动的到位确认预算
STALE_COMMAND_S = 60.0      # 提交后多久没被领走就不再执行（见模块注释第 2 条）
#: 运动中实时位置/速度的写回节流（秒）。等待原语本身以 20–50Hz 轮询状态字，
#: 文件写回压到约 4Hz：页面按 300ms 轮询 /api/cmd，再快它也看不见。
PROGRESS_WRITE_S = 0.25

#: 轴名 → 控制器轴号（从参数单源派生，改接线不会漏改；与 M0 的 `patrol.debug` 同源）
AXIS_BY_NAME: Mapping[str, int] = {spec.name.upper(): spec.index for spec in M1.axes}


class CommandError(ValueError):
    """指令本身不可执行（参数缺失/类型不对/站位不存在）。"""


@dataclass
class ExecOutcome:
    ok: bool
    detail: str
    data: dict = field(default_factory=dict)


def _require_float(args: dict, key: str, *, where: str) -> float:
    if key not in args:
        raise CommandError(f"{where} 缺参数 {key!r}")
    raw = args[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise CommandError(f"{where} 的参数 {key!r} 需要数字，收到 {raw!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise CommandError(f"{where} 的参数 {key!r} 必须是有限数值，收到 {raw!r}")
    return value


def axis_of(args: dict, *, where: str) -> int:
    """从 ``{"axis": "Y"|"Z"|1|2}`` 取轴号（大小写不敏感；不认识就拒绝，不猜）。"""
    raw = args.get("axis")
    if isinstance(raw, str) and raw.strip().upper() in AXIS_BY_NAME:
        return AXIS_BY_NAME[raw.strip().upper()]
    if isinstance(raw, int) and not isinstance(raw, bool) and raw in M1.axis_indices:
        return raw
    raise CommandError(f"{where} 的 axis 需要 {'/'.join(AXIS_BY_NAME)} 之一，收到 {raw!r}")


class ManualExecutor:
    """执行一条手动指令。**只应由持有控制器的那个进程构造**。

    依赖全部注入：`connect` 连控制器（每条指令连一次，用完即关——FMC4030 有 60 秒
    空闲断链语义，长连接反而要自己维护重连）。采图相关的依赖可以不给，那时 `capture`
    指令会被明确拒绝，而不是静默什么都不做。
    """

    def __init__(
        self,
        channel: ManualChannel,
        *,
        connect: Callable[[], Fmc4030],
        stations: list[Station] | None = None,
        capture: CaptureClient | None = None,
        append_index: Callable[[dict], None] | None = None,
        flush: Callable[[], object] | None = None,
        room_fields: Callable[[], dict] | None = None,
        log: Callable[[str], None] = print,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = datetime.now,
        stale_after_s: float = STALE_COMMAND_S,
        progress_write_s: float = PROGRESS_WRITE_S,
    ) -> None:
        self.channel = channel
        self.connect = connect
        self.stations = list(stations or [])
        self.capture = capture
        self.append_index = append_index
        self.flush = flush
        self.room_fields = room_fields or (dict)
        self.log = log
        self._sleep = sleep
        self.now = now
        self.stale_after_s = stale_after_s
        #: 实时位置写回的节流间隔（测试可给 0，逐次断言每一次状态回调）
        self.progress_write_s = progress_write_s

    # ---------- 对外：领一条、执行、写回 ----------

    def service_once(self) -> Command | None:
        """领一条待执行指令并执行完（结果写回通道）。没有可领的就返回 None。

        返回**已处理**的指令（包括被拒绝的那些），供调用方记日志。
        """
        cmd = self.channel.claim()
        if cmd is None:
            return None
        self.log(f"手动指令 {cmd.id} {cmd.kind} {cmd.args or ''}（{cmd.by}）——开始执行")
        try:
            outcome = self.execute(cmd)
        except Exception as e:  # noqa: BLE001 - 执行方不能被一条指令带走
            outcome = ExecOutcome(False, f"执行异常（{type(e).__name__}: {e}）")
            self.log(f"手动指令 {cmd.id} 执行异常：{type(e).__name__}: {e}")
        self.channel.complete(
            cmd, ok=outcome.ok, detail=outcome.detail, data=outcome.data
        )
        # 进度文件只服务于"这条还在跑"的窗口期；结果已落，实时流就收场——
        # 留着它页面会把上一条的残值当成现在。
        self.channel.clear_progress()
        self.log(f"手动指令 {cmd.id} {'完成' if outcome.ok else '失败'}：{outcome.detail}")
        return cmd

    def execute(self, cmd: Command) -> ExecOutcome:
        """执行一条已领走的指令，返回结果（不写通道——那是 `service_once` 的事）。"""
        expired = self._expired_detail(cmd)
        if expired is not None:
            return ExecOutcome(False, expired)

        # 急停：**闩锁**。置位期间只放行"停下来"与"关灯"，别的一律拒绝。
        # 判断来自 `deploy.manual.estop_allows`——console 用它给出当场的话，这里用它把关；
        # 两处各写一份，迟早漂移成"页面说能、执行方说不能"。
        if self.channel.raised() and not estop_allows(cmd.kind, cmd.args):
            return ExecOutcome(
                False,
                "急停已置位：先复位急停再操作（复位是独立动作，刻意不随指令自动清掉）",
            )

        if cmd.kind == "stop":
            return self._with_client(self._stop, cmd)
        if cmd.kind == "lamp":
            return self._with_client(lambda fmc: self._lamp(fmc, cmd), cmd)
        if cmd.kind == "home":
            return self._with_client(self._home, cmd)
        if cmd.kind == "jog":
            return self._with_client(lambda fmc: self._jog(fmc, cmd), cmd)
        if cmd.kind == "goto":
            return self._with_client(lambda fmc: self._goto(fmc, cmd), cmd)
        if cmd.kind == "capture":
            return self._with_client(lambda fmc: self._capture(fmc, cmd), cmd)
        return ExecOutcome(False, f"未知指令 {cmd.kind!r}")

    # ---------- 内部 ----------

    def _expired_detail(self, cmd: Command) -> str | None:
        if not cmd.created_at:
            return None
        try:
            created = datetime.fromisoformat(cmd.created_at)
        except ValueError:
            return None
        age = (self.now() - created).total_seconds()
        if age > self.stale_after_s:
            return (
                f"指令已过期：提交后 {age:.0f} 秒才被领走（上限 {self.stale_after_s:.0f} 秒）。"
                "手动操作是「此时此地」的动作，过期指令一律不执行，请重新提交"
            )
        return None

    def _with_client(self, action: Callable[[Fmc4030], ExecOutcome], cmd: Command) -> ExecOutcome:
        """连控制器 → 执行 → 关闭。连接失败如实回给操作者（页面要能看见原因）。

        执行期间给客户端挂上**实时位置监听器**（`_progress_listener`）：等待原语
        轮询状态字时顺手把位置/速度写回通道，页面因此能看到"正在往哪动"——
        控制器本来就出实时位置与速度（厂商软件就是这么显示的），不另开轮询。
        """
        try:
            fmc = self.connect()
        except FmcError as e:
            return ExecOutcome(False, f"连接控制器失败：{e}")
        except Exception as e:  # noqa: BLE001 - 例如厂商动态库缺失
            return ExecOutcome(False, f"连接控制器失败（{type(e).__name__}: {e}）")
        try:
            fmc.status_listener = self._progress_listener(cmd)
            return action(fmc)
        except MotionAborted as e:
            # **急停**：先把轴真的停住，再如实上报；停不干净也必须一起报出去。
            failed = self._safe_stop(fmc)
            tail = f"；已急停（未确认：{'、'.join(failed)}）" if failed else "；已急停"
            return ExecOutcome(False, f"{e}{tail}")
        except HomeTimeoutError as e:
            return ExecOutcome(False, f"回零失败：{e}（原点不可信，请查限位开关与回零超时时间）")
        except MotionTimeoutError as e:
            return ExecOutcome(False, f"到位确认超时：{e}")
        except (CaptureError, CommandError, FmcError) as e:
            return ExecOutcome(False, f"{e}")
        finally:
            try:
                fmc.status_listener = None
            except Exception:  # noqa: BLE001,S110 - 摘钩子失败不掩盖动作结论
                pass
            try:
                fmc.close()
            except Exception as e:  # noqa: BLE001 - 关闭失败不改变结论
                self.log(f"关闭控制器连接异常：{e}")

    def _progress_listener(self, cmd: Command) -> Callable[[MachineStatus], None]:
        """生成一个**节流**的状态监听器：把实时位置/速度写进 `progress.json`。

        节流的时钟用单调时钟（与注入的业务时钟分开——后者在测试里是冻结的）。
        写失败只记日志：这是观测通道，绝不能反过来把运动搞挂。
        """
        last = {"t": 0.0}

        def listener(st: MachineStatus) -> None:
            now = time.monotonic()
            if now - last["t"] < self.progress_write_s:
                return
            last["t"] = now
            try:
                self.channel.write_progress({
                    "id": cmd.id,
                    "kind": cmd.kind,
                    "ts": self.now().isoformat(timespec="seconds"),
                    "position_yz": [round(st.real_pos[M1.y.index], 3),
                                    round(st.real_pos[M1.z.index], 3)],
                    "speed_yz": [round(st.real_speed[M1.y.index], 2),
                                 round(st.real_speed[M1.z.index], 2)],
                    "moving": any(st.axes[a].running for a in M1.axis_indices),
                })
            except Exception as e:  # noqa: BLE001 - 观测通道的故障不该影响运动
                self.log(f"实时位置写回失败（不影响本次动作）：{type(e).__name__}: {e}")

        return listener

    def _safe_stop(self, fmc: Fmc4030) -> list[str]:
        try:
            return list(fmc.stop_everything())
        except Exception as e:  # noqa: BLE001 - 急停失败也要给出结论
            return [f"stop_everything 异常（{type(e).__name__}: {e}）"]

    def _where(self, fmc: Fmc4030) -> str:
        y, z = fmc.current_yz()
        return f"Y={y:.2f} Z={z:.2f}"

    @staticmethod
    def _position_data(y: float, z: float) -> dict:
        return {"position_yz": [round(y, 3), round(z, 3)]}

    # ---------- 各指令 ----------

    def _stop(self, fmc: Fmc4030) -> ExecOutcome:
        failed = self._safe_stop(fmc)
        if failed:
            return ExecOutcome(
                False,
                f"急停有 {len(failed)} 项未确认成功：{'、'.join(failed)}"
                "——请立即确认轴已停下，必要时断电",
            )
        y, z = fmc.current_yz()
        return ExecOutcome(
            True,
            f"已停止（插补停止 + 两轴立即停止）；Y={y:.2f} Z={z:.2f}",
            self._position_data(y, z),
        )

    def _lamp(self, fmc: Fmc4030, cmd: Command) -> ExecOutcome:
        on = cmd.args.get("on")
        if not isinstance(on, bool):
            raise CommandError(f"lamp 的 on 需要 true/false，收到 {on!r}")
        fmc.lamp(on)
        return ExecOutcome(True, "补光灯 " + ("开" if on else "关"), {"lamp_on": on})

    def _home(self, fmc: Fmc4030) -> ExecOutcome:
        fmc.home_all(timeout_s=MANUAL_TIMEOUT_S, abort=self.channel.raised)
        y, z = fmc.current_yz()
        # 方向文案**从参数派生**：2026-09-15 Z 的回零方向改过一次（ADR-0018），
        # 写死的"Z 正限位"当场变成了谎话——现场排障时最不该被这种东西误导。
        where = " / ".join(f"{s.name} {'负' if s.home_dir == HOME_DIR_NEGATIVE else '正'}限位"
                           for s in M1.axes)
        return ExecOutcome(
            True,
            f"回零完成（{where}，落点为原点）；Y={y:.2f} Z={z:.2f}",
            self._position_data(y, z),
        )

    def _jog(self, fmc: Fmc4030, cmd: Command) -> ExecOutcome:
        axis = axis_of(cmd.args, where="jog")
        dist = _require_float(cmd.args, "mm", where="jog")
        spec = M1.by_index(axis)
        # 相对运动的落点要**先读当前位置**才知道：不能拿"上次记下的位置"去算行程。
        y, z = fmc.current_yz()
        landing = (y + dist) if axis == M1.axes[0].index else (z + dist)
        fmc.check_axis_travel(axis, landing)     # 越界在下发前拒绝
        fmc.jog(axis, dist)
        if not fmc.wait_stop(axes=(axis,), timeout_s=JOG_WAIT_S, abort=self.channel.raised):
            raise MotionTimeoutError(
                f"点动 {spec.name} {dist:+.2f} mm 未在 {JOG_WAIT_S:.0f}s 内停稳"
            )
        y, z = fmc.current_yz()
        return ExecOutcome(
            True,
            f"点动 {spec.name} {dist:+.2f} mm；Y={y:.2f} Z={z:.2f}",
            self._position_data(y, z),
        )

    def _goto(self, fmc: Fmc4030, cmd: Command) -> ExecOutcome:
        y = _require_float(cmd.args, "y", where="goto")
        z = _require_float(cmd.args, "z", where="goto")
        fmc.check_travel(y, z)      # 越界在**下发前**拒绝（控制器会把越界目标静默截断）
        fmc.goto(y, z, timeout_s=MANUAL_TIMEOUT_S, abort=self.channel.raised)
        actual_y, actual_z = fmc.current_yz()
        return ExecOutcome(
            True,
            f"已到位 Y={y:.2f} Z={z:.2f}；Y={actual_y:.2f} Z={actual_z:.2f}",
            self._position_data(actual_y, actual_z),
        )

    def _capture(self, fmc: Fmc4030, cmd: Command) -> ExecOutcome:
        if self.capture is None or self.append_index is None:
            raise CommandError("手动抓拍未配置（缺采图服务或 outbox）——这一条不执行")
        station = self._station_for(cmd)
        ts = self.now()
        y, z = fmc.current_yz()      # **先读位置**：这是这张照片唯一可回溯的事实
        object_name = image_object_name(station, ts)
        try:
            fmc.lamp(True)
            self._sleep(M1.lamp_settle_s)
            self.capture.capture(
                ip=station.camera_ip,
                user=station.camera_user,
                pwd=station.camera_pwd,
                filename=object_name,
            )
        finally:
            fmc.lamp(False)
        self.append_index({
            "kind": "image_index",
            "ts": ts.isoformat(timespec="seconds"),
            "ok": True,
            "station_id": station.id,
            "box_id": station.box_id,
            "angle_profile": station.angle_profile,
            "camera_ip": station.camera_ip,
            "yz": [round(y, 3), round(z, 3)],
            "object_name": object_name,
            "manual": True,     # 额外字段：prod 的 images 表只取约定列，多的会被忽略
            **self.room_fields(),
        })
        if self.flush is not None:
            try:
                self.flush()
            except Exception as e:  # noqa: BLE001 - 同步失败不该把"拍到了"变成失败
                self.log(f"手动抓拍的索引同步失败（记录仍在 outbox，可补传）：{e}")
        return ExecOutcome(
            True,
            f"已抓拍 {station.id} → {object_name}（拍摄位置 Y={y:.2f} Z={z:.2f}）",
            {
                "station_id": station.id,
                "object_name": object_name,
                "position_yz": [round(y, 3), round(z, 3)],
                "lamp_on": False,
            },
        )

    def _station_for(self, cmd: Command) -> Station:
        sid = cmd.args.get("station_id")
        if not isinstance(sid, str) or not sid.strip():
            raise CommandError(
                "手动抓拍需要 station_id：照片要归到某个站位才能在历史里找到"
                "（名字按站位给，坐标记的是实际拍摄位置）"
            )
        want = sid.strip().upper()
        for st in self.stations:
            if st.id.upper() == want:
                return st
        raise CommandError(f"站位 {sid!r} 不在站位表里（共 {len(self.stations)} 个）")

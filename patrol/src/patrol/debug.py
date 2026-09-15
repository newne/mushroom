"""M0 手动调试控制台：交互式调试两轴模组（点动 / 绝对定位 / 性能参数）。

入口：``python -m patrol.debug``（真机）或 pytest 注入假库测试。

本机两轴为 **Y（轴 1，水平 0…4492 mm，向右为正，左端为原点）** 与
**Z（轴 2，竖直 0…212 mm，向下为正，顶端为原点）**；轴号与行程取自
``patrol.motion_profile``。两轴原点都在**靠近电机**的一端，回零都找负限位（ADR-0018）。

命令集（大小写不敏感，每行以 ``#`` 注释开头）：

    status                显示当前位置/速度/IO/运行模式
    para                  读回控制器设备参数（细分/导程/软限位/回零超时）并核对
    home                  两轴回零（都找负限位：Y 往左、Z 往上），阻塞到到位
    jog <Y|Z> <mm>        单轴点动（相对运动，可负）
    abs <Y|Z> <mm>        单轴绝对定位（越出行程即拒绝）
    goto <y> <z>          双轴直线插补绝对定位（单段直达）
    speed <mm/s>          覆盖速度档（原样下发，不做按轴折算）
    acc <mm/s2>           覆盖加速度档
    dec <mm/s2>           覆盖减速度档
    timeout <秒>          覆盖到位确认超时（回零 / 点动 / 定位共用）
    lamp <on|off>         控制补光灯（OUT0）
    stop                  急停：插补停止 + 两轴立即停止，并报未确认成功的动作
    help                  列出命令
    quit / exit / EOF     退出

速度/加/减速档默认**按该轴整定值**（目标速度/加减速的派生点动档）；一旦用
``speed``/``acc``/``dec`` 显式设定，即作为合成量原样下发，仅供排障使用。

**回零**走 ``home_all()``，**默认阻塞**到两轴都到位。这是刻意的：控制器若在
「回零超时时间」（设备参数，ms）内没等到限位开关会自行终止回零并置位「轴回零
超时」，此时点位**不是**原点，继续下发绝对坐标会整体偏移。所以 ``home`` 把
``HomeTimeoutError`` 单独打印，并提示去查限位开关与回零超时时间。

**软限位**是控制器里独立于本程序的另一层保护，出厂默认只有 ±200mm，而 Y 行程是
4492mm：未整定时控制器会把 Y 目标**自行截断到 200mm 且返回成功**，业务侧完全看不
出来。因此连接后立刻读回一次设备参数，不符即警告（不阻断，M0 本就是排障用），
``para`` 可随时复查；见 ``patrol.fmc.device_para`` 与 ADR-0008。
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

from patrol.fmc import Fmc4030, FmcError, HomeTimeoutError, MotionTimeoutError
from patrol.motion_profile import M1 as _PROFILE

# 轴名 → 控制器轴号（从参数单源派生，改接线不会漏改）
AXIS_NAME = {spec.name: spec.index for spec in _PROFILE.axes}
_AXIS_HINT = "|".join(AXIS_NAME)
_HELP = __doc__


class ParseError(ValueError):
    """命令语法/数值错误。"""


@dataclass(frozen=True)
class Command:
    kind: str
    axis: int | None = None
    value: float | None = None
    x: float | None = None
    y: float | None = None
    on: bool | None = None


def parse_command(line: str) -> Command | None:
    """把一行原始输入解析为 Command；空行/注释返回 None，非法语法抛 ParseError。

    纯逻辑，可独立测试；数值与轴名均做校验。
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    parts = text.split()
    verb = parts[0].lower()

    def need_axis() -> int:
        if len(parts) < 2 or parts[1].upper() not in AXIS_NAME:
            raise ParseError(f"语法: {verb} <{_AXIS_HINT}> <数值>")
        return AXIS_NAME[parts[1].upper()]

    def number(idx: int) -> float:
        if len(parts) <= idx:
            raise ParseError(f"语法: {verb}… 处第 {idx} 个参数需要数字")
        raw = parts[idx]
        try:
            v = float(raw)
        except ValueError as e:
            raise ParseError(f"语法: {verb}… 处第 {idx} 个参数 {raw} 需要数字") from e
        if not math.isfinite(v):
            raise ParseError(f"语法: {verb}… 处第 {idx} 个参数 {raw} 必须是有限数值")
        return v

    if verb in ("quit", "exit"):
        return Command("quit")
    if verb == "help":
        return Command("help")
    if verb == "status":
        return Command("status")
    if verb == "para":
        return Command("para")
    if verb == "home":
        return Command("home")
    if verb == "stop":
        return Command("stop")
    if verb == "speed":
        if len(parts) != 2:
            raise ParseError("语法: speed <mm/s>")
        return Command("speed", value=number(1))
    if verb in ("acc", "dec"):
        if len(parts) != 2:
            raise ParseError(f"语法: {verb} <mm/s2>")
        return Command(verb, value=number(1))
    if verb == "timeout":
        if len(parts) != 2:
            raise ParseError("语法: timeout <秒>")
        return Command("timeout", value=number(1))
    if verb == "lamp":
        if len(parts) != 2 or parts[1].lower() not in ("on", "off"):
            raise ParseError("语法: lamp <on|off>")
        return Command("lamp", on=(parts[1].lower() == "on"))
    if verb == "jog":
        if len(parts) != 3:
            raise ParseError(f"语法: jog <{_AXIS_HINT}> <mm>")
        return Command("jog", axis=need_axis(), value=number(2))
    if verb == "abs":
        if len(parts) != 3:
            raise ParseError(f"语法: abs <{_AXIS_HINT}> <mm>")
        return Command("abs", axis=need_axis(), value=number(2))
    if verb == "goto":
        if len(parts) != 3:
            raise ParseError("语法: goto <y> <z>")
        return Command("goto", x=number(1), y=number(2))
    raise ParseError(f"未知命令: {verb}")


class DebugConsole:
    """在 Fmc4030 之上提供 M0 交互式调试：容纳当前运动档位并执行命令。

    ``execute`` 返回 bool 表示是否继续运行（quit 返回 False）。命令解析后的
    调度是唯一真话，可直接用假库测试。

    ``speed``/``acc``/``dec`` 为 ``None`` 表示"按该轴整定值"（默认，安全）；
    一旦被 ``speed``/``acc``/``dec`` 命令赋值，即成为所有运动的合成量覆盖。
    """

    def __init__(self, client: Fmc4030, *, stdin=None, stdout=None) -> None:
        self.client = client
        self.stdin = stdin or sys.stdin
        self.stream = stdout or sys.stdout
        self.speed: float | None = None
        self.acc: float | None = None
        self.dec: float | None = None
        self.timeout: float = _PROFILE.jog_timeout

    # ---------- 输出 ----------

    def _positive(self, v: float, name: str) -> None:
        if v <= 0:
            raise ParseError(f"{name} 必须为正数（当前 {v}）")

    def _check_params(self) -> None:
        for _v, _n in ((self.speed, "速度"), (self.acc, "加速度"), (self.dec, "减速度")):
            if _v is not None:
                self._positive(_v, _n)

    def _out(self, msg: str) -> None:
        print(msg, file=self.stream)

    # ---------- 命令执行 ----------

    def execute(self, cmd: Command | None) -> bool:
        if cmd is None:
            return True
        if cmd.kind == "quit":
            return False
        if cmd.kind == "help":
            self._out(_HELP or "")
            return True
        if cmd.kind == "status":
            self._print_status()
            return True
        if cmd.kind == "para":
            self._print_para()
            return True
        if cmd.kind == "home":
            self._do_home()
            return True
        if cmd.kind == "jog":
            if not self.client.check_stop(cmd.axis):
                self._out(f"轴 {cmd.axis} 仍在运动，请先 stop 或等待到位")
                return True
            self._check_params()
            self.client.jog(cmd.axis, cmd.value, speed=self.speed, acc=self.acc, dec=self.dec)
            self.client.wait_stop(axes=(cmd.axis,), timeout_s=self.timeout)
            self._out(f"点动到位 -> {self._where()}")
            return True
        if cmd.kind == "abs":
            self._check_params()
            self.client.move_axis(
                cmd.axis, cmd.value, speed=self.speed, acc=self.acc, dec=self.dec,
                timeout_s=self.timeout,
            )
            self._out(f"绝对定位完成 -> {self._where()}")
            return True
        if cmd.kind == "goto":
            self._check_params()
            self.client.goto_2axis(
                cmd.x, cmd.y, speed=self.speed, acc=self.acc, dec=self.dec,
                timeout_s=self.timeout,
            )
            self._out(f"已到 -> {self._where()}")
            return True
        if cmd.kind == "speed":
            self._positive(cmd.value, "速度")
            self.speed = cmd.value
            self._out(f"速度档 = {cmd.value} mm/s（合成量覆盖，原样下发）")
            return True
        if cmd.kind == "acc":
            self._positive(cmd.value, "加速度")
            self.acc = cmd.value
            self._out(f"加速度档 = {cmd.value} mm/s2（合成量覆盖，原样下发）")
            return True
        if cmd.kind == "dec":
            self._positive(cmd.value, "减速度")
            self.dec = cmd.value
            self._out(f"减速度档 = {cmd.value} mm/s2（合成量覆盖，原样下发）")
            return True
        if cmd.kind == "timeout":
            self._positive(cmd.value, "超时时间")
            self.timeout = cmd.value
            self._out(f"超时档 = {cmd.value} s（点动 / 定位 / 回零共用）")
            return True
        if cmd.kind == "lamp":
            self.client.lamp(bool(cmd.on))
            self._out("补光灯 " + ("开" if cmd.on else "关"))
            return True
        if cmd.kind == "stop":
            self._do_stop()
            return True
        self._out(f"未处理命令: {cmd.kind}")
        return True

    # ---------- 单条命令的实现 ----------

    def _do_home(self) -> None:
        """两轴回零：home_all 默认阻塞到到位，把两类失败分开报。"""
        try:
            self.client.home_all(timeout_s=self.timeout)
        except HomeTimeoutError as e:
            self._out(f"! {e}")
            self._out("  原点不可信，请检查限位开关与控制器「回零超时时间」，修好后重试 home")
        except MotionTimeoutError as e:
            self._out(f"! {e}")
            self._out("  轴可能仍在运动，请用 status 查看")
        else:
            self._out("回零完成（Y 负限位 / Z 正限位，落点为原点）")

    def _do_stop(self) -> None:
        """急停：stop_everything 收集失败动作而非抛出，非空即必须上报。"""
        failed = self.client.stop_everything()
        if failed:
            self._out(f"! 急停有 {len(failed)} 项未确认成功：{'；'.join(failed)}")
            self._out("  请立即用 status 确认轴已停下，必要时断电")
        else:
            self._out("已停止（插补停止 + 两轴单轴立即停止）")

    def _print_para(self) -> None:
        """打印控制器整定参数，并核对软限位是否窄于机械行程。"""
        try:
            para = self.client.get_device_para()
        except FmcError as e:
            self._out(f"! 读取设备参数失败：{e}")
            return
        self._out(para.describe())
        try:
            issues = self.client.soft_limit_issues(para)
        except FmcError as e:
            self._out(f"! 核对软限位失败：{e}")
            return
        if not issues:
            self._out("软限位与机械行程一致（或已取消软限位，交由本程序行程校验兜底）")
            return
        for issue in issues:
            self._out(f"! {issue}")
        self._out("  控制器会把这些轴的越界目标自行截断到软限位并返回成功，请在控制器参数页整定")

    def _where(self) -> str:
        y, z = self.client.current_yz()
        return f"Y={y:.2f} Z={z:.2f}"

    def _print_status(self) -> None:
        ms = self.client.get_status()
        axes = ", ".join(
            f"{spec.name}{'运行' if ms.axes[spec.index].running else '停'}"
            for spec in _PROFILE.axes
        )
        pos = " ".join(f"{spec.name}={ms.real_pos[spec.index]:.2f}" for spec in _PROFILE.axes)
        spd = " ".join(f"{spec.name}={ms.real_speed[spec.index]:.2f}" for spec in _PROFILE.axes)
        self._out(
            f"位置 {pos} | 速度 {spd} | "
            f"模式={ms.run_mode} 轴[{axes}] | "
            f"IN={ms.inputs:#06x} OUT={ms.outputs:#06x}"
        )

    # ---------- 交互循环 ----------

    def run(self) -> None:
        """从标准输入逐行读取并执行，直到 quit / EOF。"""
        self._out("FMC4030 M0 手动调试（输入 help 查看命令）")
        for raw in self.stdin:
            line = raw.rstrip("\n")
            try:
                cmd = parse_command(line)
            except ParseError as e:
                self._out(f"! {e}")
                continue
            if cmd is None:
                continue
            try:
                cont = self.execute(cmd)
            except FmcError as e:
                self._out(f"! {e}")
                continue
            except KeyboardInterrupt:
                self.client.stop_everything()
                self._out("! 已中止并停止轴（Ctrl+C）")
                continue
            if not cont:
                break


def warn_soft_limits(client: Fmc4030, *, stream=None) -> None:
    """连接后读回控制器软限位，窄于行程即警告；**不阻断**（M0 本就是排障入口）。

    与 ``Fmc4030.check_soft_limits()``（抛错、用于巡检启动前）区分开：这里只在
    人机界面提示，让调试者自己能看见"下发成功但只走一小段"的根因。
    """
    stream = stream or sys.stderr
    try:
        issues = client.soft_limit_issues()
    except FmcError as e:
        print(f"! 读取控制器软限位失败：{e}", file=stream)
        return
    if not issues:
        return
    for issue in issues:
        print(f"! 控制器软限位未整定：{issue}", file=stream)
    print(
        "  越界目标会被控制器截断到软限位并返回成功，请先在控制器「参数」页整定，"
        "否则长行程轴（Y 4492mm）只能走一小段（见 ADR-0008）",
        file=stream,
    )


def main() -> None:
    """CLI 入口：连接控制器并进入 M0 交互调试。"""
    import argparse

    from patrol.fmc.loader import load_library
    from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID

    ap = argparse.ArgumentParser(prog="patrol-debug", description="FMC4030 M0 手动调试控制台")
    ap.add_argument("--ip", default=CONTROLLER_IP, help="控制器 IP")
    ap.add_argument("--port", type=int, default=CONTROLLER_PORT, help="控制器端口")
    ap.add_argument("--device", type=int, default=DEVICE_ID, help="设备 ID")
    ap.add_argument("--lib", default=None, help="SDK 动态库路径（或设 FMC4030_LIB_PATH）")
    args = ap.parse_args()

    try:
        lib = load_library(args.lib)
    except RuntimeError as e:
        print(f"! {e}", file=sys.stderr)
        raise SystemExit(2)

    client = Fmc4030.connect(lib=lib, device_id=args.device, ip=args.ip, port=args.port)
    try:
        warn_soft_limits(client)
        console = DebugConsole(client)
        console.run()
    finally:
        client.close()


if __name__ == "__main__":
    main()

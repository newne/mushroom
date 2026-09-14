"""站位示教 CLI：Jog 到位 → 记录 → 保存站位表（票 04 的前置，补上 gap-list 的 J3）。

入口：``patrol-teach``（或 ``python -m patrol.teach``）。

站位坐标本来是**推导**的（``stations.build_grid``：两轴行程整除 12 框 × 5 层），
所以不示教也能开跑。本工具存在的理由只有一个：**实际框位与均分不符**时（层高不等分、
框距不匀、机械装配偏差），把真实坐标量出来写进站位表，覆盖推导值。票 04 的现场核对
（"box_id 映射经现场核对"）也在这里落地——``record`` 把层号/框号一并记下。

命令集（大小写不敏感，``#`` 开头为注释）：

    status                        显示位置/速度/IO/运行模式（与 M0 调试台同一实现）
    pos                           只打印当前坐标（抄数用，输出最短）
    jog <Y|Z> <mm>                单轴点动，相对当前位置（示教的主力动作，10 mm/s）
    abs <Y|Z> <mm>                单轴绝对定位
    goto <y> <z>                  双轴直线插补定位（单段直达，M0 语义）
    home                          两轴回零（重新建立坐标系，落点为原点）
    lamp <on|off>                 补光灯（对准时看照明效果）
    stop                          急停
    record <id> <box_id> <层> <框> [角度档]
                                  以**当前坐标**记录站位；同 id 覆盖
    list                          列出本会话的全部站位
    drop <id>                     删除一个站位
    reset                         丢弃全部站位，从推导网格重新开始
    save [路径]                   写盘（省略路径用启动时的 --stations）
    help / quit / exit / EOF

刻意**不提供** ``speed`` / ``acc`` / ``dec`` / ``timeout`` 覆盖：点动档是现场整定值
（Y 10 mm/s，见 ``motion_profile`` 注释），示教时提速会让人肉眼看不清而对不准。
要排障请用 ``patrol-debug``，那是它的职责。

典型一次示教（现场，人在机器旁）::

    patrol-teach --stations /opt/mushroom-patrol/stations.yaml
    >>> reset                     # 从推导网格起步（沿用整定的相机 IP）
    >>> jog Y 20                  # 看着滑块往第 1 框挪
    >>> record S101 B101 1 1
    >>> jog Y 374                 # 挪到同层第 2 框
    >>> record S102 B102 1 2
    >>> list
    >>> save
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from patrol.debug import DebugConsole, ParseError
from patrol.debug import (
    parse_command as _parse_motion_command,
)
from patrol.fmc import Fmc4030, FmcError
from patrol.stations import (
    CAMERA_IP,
    DEFAULT_ANGLE,
    GRID_ANGLES,
    GRID_COLS,
    GRID_LAYERS,
    Station,
    build_grid,
    cell_of,
    drop_station,
    load_or_init,
    station_at,
    upsert_station,
)

DEFAULT_STATIONS_PATH = "stations.yaml"


@dataclass(frozen=True)
class TeachCommand:
    """示教命令：``kind`` 取值见模块 docstring。

    ``axis`` / ``value`` / ``x`` / ``y`` / ``on`` 与 ``debug.Command`` 语义一致，
    示教专属字段是 ``station_id`` / ``box_id`` / ``layer`` / ``col`` / ``angle`` / ``path``。
    """

    kind: str
    axis: int | None = None
    value: float | None = None
    x: float | None = None
    y: float | None = None
    on: bool | None = None
    station_id: str | None = None
    box_id: str | None = None
    layer: int | None = None
    col: int | None = None
    angle: str | None = None
    path: str | None = None


def parse_teach_command(line: str) -> TeachCommand | None:
    """解析一行输入；空行/注释返回 None，非法语法抛 ParseError。

    运动类动词**复用** ``patrol.debug.parse_command``——示教与 M0 调试台的
    ``jog`` / ``abs`` / ``goto`` / ``home`` 必须逐字同义，各写一套迟早漂移。
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    parts = text.split()
    verb = parts[0].lower()

    if verb == "pos":
        return TeachCommand("pos")
    if verb == "record":
        if len(parts) not in (5, 6):
            raise ParseError(
                f"语法: record <站位id> <框id> <层 1…{GRID_LAYERS}> <框 1…{GRID_COLS}> [角度档]"
            )
        station_id, box_id, layer_s, col_s = parts[1], parts[2], parts[3], parts[4]
        if not station_id or not box_id:
            raise ParseError("站位 id 与框 id 不能为空")
        try:
            layer, col = int(layer_s), int(col_s)
        except ValueError as e:
            raise ParseError(f"层号/框号必须是整数：{layer_s} / {col_s}") from e
        if not 1 <= layer <= GRID_LAYERS:
            raise ParseError(f"层号越界 {layer}（1…{GRID_LAYERS}，第 1 层在最上）")
        if not 1 <= col <= GRID_COLS:
            raise ParseError(f"框号越界 {col}（1…{GRID_COLS}，沿 Y 递增）")
        angle = parts[5] if len(parts) == 6 else DEFAULT_ANGLE
        if angle not in GRID_ANGLES:
            raise ParseError(f"未知角度档 {angle}（当前 GRID_ANGLES={GRID_ANGLES}）")
        return TeachCommand("record", station_id=station_id, box_id=box_id,
                            layer=layer, col=col, angle=angle)
    if verb == "list":
        return TeachCommand("list")
    if verb == "drop":
        if len(parts) != 2:
            raise ParseError("语法: drop <站位id>")
        return TeachCommand("drop", station_id=parts[1])
    if verb == "reset":
        return TeachCommand("reset")
    if verb == "save":
        if len(parts) > 2:
            raise ParseError("语法: save [路径]")
        return TeachCommand("save", path=parts[1] if len(parts) == 2 else None)

    # 其余动词全部委托给 M0 的解析器；不认识的会抛 ParseError（含 help/quit）
    motion = _parse_motion_command(text)
    if motion is None:
        return None
    return TeachCommand(
        motion.kind, axis=motion.axis, value=motion.value,
        x=motion.x, y=motion.y, on=motion.on,
    )


def _help_text() -> str:
    """把模块 docstring 的命令表单独取出来（顶部的 ``::`` 示例块之前的部分）。"""
    body = (__doc__ or "").split("::")[0]
    return body.strip()


class TeachConsole(DebugConsole):
    """带站位记录的 M0 控制台：运动能力全部继承，只新增"记录/查看/写盘"。

    刻意**继承** ``DebugConsole`` 而不是另写一套运动指令：示教与调试驱动的是同一台
    控制器、同一组整定档位，两套实现必然漂移（例如 ``goto`` 是否走单段直达）。
    示教专属的运动差异都已经封在 ``jog`` / ``goto`` 里，本类不需要知道。
    """

    def __init__(
        self,
        client: Fmc4030,
        *,
        stations: list[Station] | None = None,
        stations_path: str | None = None,
        camera_ip: str = CAMERA_IP,
        camera_user: str = "admin",
        camera_pwd: str = "",
        stdin=None,
        stdout=None,
    ) -> None:
        super().__init__(client, stdin=stdin, stdout=stdout)
        self.stations: list[Station] = list(stations or [])
        self.stations_path = stations_path or DEFAULT_STATIONS_PATH
        self.camera_ip = camera_ip
        self.camera_user = camera_user
        self.camera_pwd = camera_pwd
        self.dirty = False

    # ---------- 命令分发 ----------

    def execute(self, cmd) -> bool:
        """先处理示教专属动词，其余交回 ``DebugConsole`` 的运动指令。"""
        if isinstance(cmd, TeachCommand):
            return self._execute_teach(cmd)
        return super().execute(cmd)

    def _execute_teach(self, cmd: TeachCommand) -> bool:
        handler = {
            "pos": self._do_pos,
            "record": self._do_record,
            "list": self._do_list,
            "drop": self._do_drop,
            "reset": self._do_reset,
            "save": self._do_save,
        }.get(cmd.kind)
        if handler is None:
            self._out(f"未处理命令: {cmd.kind}")
            return True
        handler(cmd)
        return True

    # ---------- 示教原子动作 ----------

    def _do_pos(self, _cmd: TeachCommand) -> None:
        self._out(self._where())

    def _do_record(self, cmd: TeachCommand) -> None:
        try:
            station = station_at(
                self.client,
                station_id=cmd.station_id,
                box_id=cmd.box_id,
                layer=cmd.layer,
                col=cmd.col,
                angle_profile=cmd.angle,
                camera_ip=self.camera_ip,
                camera_user=self.camera_user,
                camera_pwd=self.camera_pwd,
            )
        except ValueError as e:
            self._out(f"! {e}")
            return
        state = "覆盖" if any(s.id == station.id for s in self.stations) else "新增"
        upsert_station(self.stations, station)
        self.dirty = True
        self._out(
            f"{state} {station.id} ← {station.box_id} 第 {station.layer} 层第 {station.col} 框 "
            f"Y={station.y:.2f} Z={station.z:.2f} {station.angle_profile}（未写盘，save 生效）"
        )

    def _do_list(self, _cmd: TeachCommand) -> None:
        if not self.stations:
            self._out("（空表——add 之前可先 reset 从推导网格起步）")
            return
        for s in self.stations:
            layer_col = f"{s.layer}-{s.col}" if s.layer and s.col else "-"
            self._out(
                f"{s.id:<8} {s.box_id:<6} {layer_col:<5} "
                f"Y={s.y:8.2f} Z={s.z:8.2f}  {s.angle_profile:<6} {s.camera_ip}"
            )
        self._out(f"共 {len(self.stations)} 个站位"
                  + ("（有未保存的改动）" if self.dirty else ""))

    def _do_drop(self, cmd: TeachCommand) -> None:
        before = len(self.stations)
        drop_station(self.stations, cmd.station_id)
        if len(self.stations) == before:
            self._out(f"! 没有站位 {cmd.station_id}")
            return
        self.dirty = True
        self._out(f"已删除 {cmd.station_id}，剩 {len(self.stations)} 个站位（未写盘）")

    def _do_reset(self, _cmd: TeachCommand) -> None:
        self.stations = build_grid(camera_ip_of=lambda _l, _c: self.camera_ip)
        self.dirty = True
        derived = sum(1 for s in self.stations if cell_of(s.id) == (s.layer, s.col))
        self._out(
            f"已重置为推导网格：{len(self.stations)} 个站位"
            f"（{GRID_LAYERS} 层 × {GRID_COLS} 框 × {len(GRID_ANGLES)} 角度档，"
            f"其中 {derived} 个可反解出格子）。**尚未写盘**——重示教完再 save。"
        )

    def _do_save(self, cmd: TeachCommand) -> None:
        from patrol.stations import save_stations

        path = cmd.path or self.stations_path
        if not self.stations:
            self._out("! 站位表为空，拒绝写盘（避免把现场表清成空文件）")
            return
        ids = [s.id for s in self.stations]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            self._out(f"! 站位 id 重复，拒绝写盘：{dupes}")
            return
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        save_stations(self.stations, path)
        self.stations_path = path
        self.dirty = False
        self._out(f"已写盘 {path}：{len(self.stations)} 个站位")

    # ---------- 交互循环 ----------

    def run(self) -> None:
        self._out("站位示教（输入 help 查看命令；record 记录当前坐标，save 写盘）")
        for raw in self.stdin:
            line = raw.rstrip("\n")
            if line.strip().lower() in ("help", "?"):
                self._out(_help_text())
                continue
            try:
                cmd = parse_teach_command(line)
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
        if self.dirty:
            self._out("! 有未保存的改动（退出前请 save）")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：连接控制器并进入站位示教。"""
    import argparse

    from patrol.debug import warn_soft_limits
    from patrol.fmc.loader import load_library
    from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID

    ap = argparse.ArgumentParser(
        prog="patrol-teach",
        description="站位示教：Jog 到位 → record → save（票 04 前置）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--stations", default=DEFAULT_STATIONS_PATH, help="站位表 YAML 路径")
    ap.add_argument("--from-grid", action="store_true",
                    help="忽略已有站位表，从推导网格重新开始（等价于开场先 reset）")
    ap.add_argument("--camera-ip", default=CAMERA_IP, help="全场相机 IP（写入新记录的站位）")
    ap.add_argument("--camera-user", default="admin", help="相机用户名")
    ap.add_argument("--camera-pwd", default="", help="相机口令（现场无密码，留空）")
    ap.add_argument("--ip", default=CONTROLLER_IP, help="控制器 IP")
    ap.add_argument("--port", type=int, default=CONTROLLER_PORT, help="控制器端口")
    ap.add_argument("--device", type=int, default=DEVICE_ID, help="设备 ID")
    ap.add_argument("--lib", default=None, help="SDK 动态库路径（或设 FMC4030_LIB_PATH）")
    args = ap.parse_args(argv)

    try:
        lib = load_library(args.lib)
    except RuntimeError as e:
        print(f"! {e}", file=sys.stderr)
        return 2

    if args.from_grid:
        stations: list[Station] = build_grid(camera_ip_of=lambda _l, _c: args.camera_ip)
        print(f"已从推导网格起步：{len(stations)} 个站位（--from-grid）", file=sys.stderr)
    else:
        try:
            stations = load_or_init(args.stations)
        except (OSError, ValueError) as e:
            print(f"! 读取站位表失败：{e}", file=sys.stderr)
            return 2
        if stations:
            print(f"已载入 {len(stations)} 个站位：{args.stations}", file=sys.stderr)
        else:
            print(f"站位表不存在或为空（{args.stations}）："
                  f"可用 reset 从推导网格起步，或直接 record 逐个记录", file=sys.stderr)

    client = Fmc4030.connect(lib=lib, device_id=args.device, ip=args.ip, port=args.port)
    try:
        warn_soft_limits(client)
        station_ids = {s.id for s in stations}
        print(f"当前站位 id：{'、'.join(sorted(station_ids)) if station_ids else '（无）'}",
              file=sys.stderr)
        print(f"网格：{GRID_LAYERS} 层 × {GRID_COLS} 框，层号自顶向下；角度档 {GRID_ANGLES}",
              file=sys.stderr)
        TeachConsole(
            client,
            stations=stations,
            stations_path=args.stations,
            camera_ip=args.camera_ip,
            camera_user=args.camera_user,
            camera_pwd=args.camera_pwd,
        ).run()
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""M1（主机调度巡检）的装配入口 —— 补上 gap-list 的 J1。

patrol 包里 M1 的**逻辑**早已齐备：``round.PatrolRound``（一轮的生命周期：回零 →
逐站采图 → 回原位）、``orchestrator.StationCapture``（单站闭环：运动 → 补光 → 采图
→ 灭灯 → 元数据）、``daemon.PatrolDaemon``（重连、落 outbox、同步 prod）。缺的只是
**装配**：没人把依赖注入进去，也没有时刻表循环。本模块只做装配，不掺业务逻辑。

::

    python -m deploy.m1 --check        # 只读预检：连接 + 软限位 + 站位表 + 端点
    python -m deploy.m1 --once         # 跑一轮就退出（首次上机验证用这个）
    python -m deploy.m1                # 常驻：每小时第 5 分钟跑一轮

**为什么默认 :05 开始**：采图服务与老系统**共用**同一台相机（192.168.1.238），而老系统
每小时 :01:0x–:02:00 会批量采 6 台（611 → 238 等）。错开那两分钟，避免抢相机会话。
一轮实测约 6–7 min（单次采图 5.2 s × 60 站位是主要开销），故周期下限取 15 min。
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from patrol.capture_client import CaptureClient
from patrol.daemon import PatrolDaemon
from patrol.fmc import Fmc4030, FmcError
from patrol.fmc.loader import load_library
from patrol.framing import clamp_trim
from patrol.links import RetryingTransport, Transport
from patrol.motion_profile import CONTROLLER_IP, CONTROLLER_PORT, DEVICE_ID, M1
from patrol.room import RoomStateError, load_room_state
from patrol.stations import (
    CAMERA_IP,
    Station,
    build_grid,
    fill_camera_ip,
    load_stations,
    save_stations,
)
from patrol.store import JsonlStore
from patrol.sync import PROD_INGEST_URL, SyncClient

from deploy.framing_wire import build as build_framing
from deploy.framing_wire import make_apply_trim
from deploy.manual import ManualChannel
from deploy.transport import HttpxTransport, host_port

DEFAULT_STATIONS_PATH = "/opt/mushroom-patrol/stations.yaml"
DEFAULT_ROOM_PATH = "/opt/mushroom-patrol/room.yaml"   # 入库日期 ⇒ 巡检准入门禁
DEFAULT_OUTBOX_PATH = "/opt/mushroom-patrol/outbox.jsonl"
DEFAULT_LOG_PATH = "/opt/mushroom-patrol/m1.log"   # 人读日志（journal 记结构化取证）
CAPTURE_HOST = "127.0.0.1:7003"  # 同机采图服务（spec §2 架构图，端口固定）
# 容器里 127.0.0.1 不是宿主：采图服务发布在宿主上，得走网桥网关地址（现场惯例 172.17.0.1）。
# 环境变量与 --capture-host 都能改，默认值保持"裸机直跑"的那一个。
DEFAULT_CAPTURE_HOST = os.environ.get("PATROL_CAPTURE_HOST", CAPTURE_HOST)
DEFAULT_AT_MINUTE = 5            # 每小时的第几分钟启动（避开老系统 :01–:02 批量采图）


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def make_logger(log_path: str | None):
    """返回日志函数；给了 ``--log`` 就**同时**写文件与 stdout。

    为什么默认就该有文件：2026-09-13 那次上机的 stdout 是管道，接在启动它的那个会话里；
    会话一收，现场只剩"它死了"这一个事实——连它跑到第几站都查不出来。日志本来就该
    落在机器上（``journal`` 记结构化取证，这里记人读的行文，两者互补）。
    """
    if not log_path:
        return log

    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a", encoding="utf-8")

    def both(msg: str) -> None:
        line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
        print(line, flush=True)
        fh.write(line + "\n")
        fh.flush()

    return both


class _NullSync:
    """同步被显式禁用时的占位：不动 outbox，也不产生"同步失败"的噪音日志。

    与"transport 缺失"（``SyncClient.flush`` 抛 NotImplementedError）区分开——后者
    是配置漏了，前者是部署者的明确选择（例如 prod 端点未定时先只跑巡检）。
    """

    def flush(self, store: JsonlStore) -> int:
        return 0


def make_capture(args) -> CaptureClient:
    """采图客户端。抽成工厂是因为**手动抓拍要用同一个**（同一台相机、同一个服务）：
    两处各建一个客户端，迟早会在"重试次数/错误分类"上漂移。

    accept_json_errors：采图服务用 HTTP 500 表示"**这一次**没拍成"（body 仍是完整
    信封），不是"服务挂了"。放它按链路故障抛错会把业务失败误判成基础设施故障，
    也会丢掉重试所需的 error_code/message。交给 CaptureClient._interpret 分类。
    """
    transport: Transport = HttpxTransport(
        allowed_hosts={args.capture_host}, accept_json_errors=("success",)
    )
    return CaptureClient(transport=transport)


def make_sync(args) -> SyncClient | _NullSync:
    """outbox → prod 的同步器；``--no-sync`` 时给一个什么都不做的占位。"""
    if args.no_sync:
        return _NullSync()
    sync_transport = RetryingTransport(
        HttpxTransport(allowed_hosts={host_port(args.ingest)}), attempts=3, backoff_s=2.0
    )
    return SyncClient(endpoint=args.ingest, transport=sync_transport)


def make_estop_check(cmd_dir: str | None, *, disabled: bool = False, log=None):
    """急停标志文件的检查函数（`deploy.manual.ManualChannel` 的 ESTOP）。

    为什么在 deploy 层做：`patrol` 库里不许有"标志文件"这种部署概念，它只接受一个
    ``abort()`` 回调。这个函数就是那个回调的**部署侧实现**——console 写文件、执行方
    读文件，两边对路径的理解都来自 `ManualChannel`（一处定义）。

    关掉它（``--no-estop``）只在排障时说得通：正常运行下，"有人按了急停"必须能打断
    正在跑的那一轮。
    """
    if disabled or not cmd_dir:
        return None
    channel = ManualChannel(cmd_dir)
    if log is not None:
        log(f"急停标志：{channel.estop_path}（巡检中每等一步都看它一眼）")
    return channel.raised


def next_run_delay(now: datetime, at_minute: int) -> float:
    """距下一个 ``:at_minute`` 还有多少秒（今天已过则顺延到明天同一分钟）。"""
    if not 0 <= at_minute <= 59:
        raise ValueError(f"at_minute 越界: {at_minute}（0–59）")
    nxt = now.replace(minute=at_minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(hours=1)
    return (nxt - now).total_seconds()


def load_station_list(path: str, *, camera_ip: str, log_fn=None) -> list[Station]:
    """读站位表；文件不存在则按行程推导生成一份并落盘（避免每次重算）。

    网格坐标是**推导**出来的（``stations.build_grid``：12 框 × 5 层，两轴行程整除
    该网格），因此不需要逐点示教就能开跑；示教只在"实际框位与均分不符"时才需要，
    届时用 ``patrol-teach`` 到位后 ``record``、``save``，覆盖这里的 YAML。

    已存在的表里若有**显式**空 ``camera_ip``（``camera_ip: ""``），用 ``--camera-ip``
    补上：本机只有一台相机，它是全局配置，漏这一列不该让人上不了机。省略该列的站位
    在装载时已由 dataclass 默认值填好；这里补的是写了空串的那种。**只补空值**，
    显式写了 IP 的站位一律保留，分机位的能力不被剥夺。
    """
    log_fn = log_fn or log
    p = Path(path)
    if p.exists():
        stations = load_stations(path)
        if not stations:
            raise SystemExit(f"站位表为空: {path}")
        stations, filled = fill_camera_ip(stations, camera_ip)
        if filled:
            log_fn(f"站位表 {path}：{len(stations)} 个站位，其中 {filled} 个 camera_ip 为空，"
                   f"已补默认相机 {camera_ip}（要固化进文件就 save 一次）")
        else:
            log_fn(f"站位表 {path}：{len(stations)} 个站位")
        return stations
    stations = build_grid(camera_ip_of=lambda _layer, _col: camera_ip)
    save_stations(stations, path)
    log_fn(f"站位表不存在，已按行程推导生成 {len(stations)} 个站位并写入 {path}")
    return stations


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="patrol-m1",
        description="M1 主机调度巡检（装配入口）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="跑一轮就退出（首次上机验证用）")
    mode.add_argument("--check", action="store_true", help="只读预检后退出，不动机构")
    mode.add_argument("--why", action="store_true",
                      help="只打印本轮准入判定（在库天数/为什么不动）后退出，不碰控制器")

    ap.add_argument("--room", default=DEFAULT_ROOM_PATH,
                    help="库房在库状态 YAML（入库日期 ⇒ 巡检准入门禁）")
    ap.add_argument("--at-minute", type=int, default=DEFAULT_AT_MINUTE,
                    help="常驻模式：每小时的第几分钟启动（避开老系统采图窗口）")
    ap.add_argument("--interval", type=float, default=None,
                    help="常驻模式：固定间隔秒数（给定时忽略 --at-minute）")
    ap.add_argument("--max-rounds", type=int, default=None, help="最多跑几轮后退出（调试用）")

    ap.add_argument("--stations", default=DEFAULT_STATIONS_PATH, help="站位表 YAML 路径")
    ap.add_argument("--outbox", default=DEFAULT_OUTBOX_PATH, help="待同步记录 JSONL 路径")
    ap.add_argument("--log", default=DEFAULT_LOG_PATH,
                    help="人读日志落盘路径（同时仍打到 stdout）；留空则只打 stdout")
    ap.add_argument("--camera-ip", default=CAMERA_IP, help="全场相机 IP（生成站位表时写入）")
    ap.add_argument("--ingest", default=os.environ.get("PATROL_INGEST", PROD_INGEST_URL),
                    help="prod 接收端点")
    ap.add_argument("--capture-host", default=DEFAULT_CAPTURE_HOST,
                    help="采图服务的 host:port（容器里要用宿主网桥地址，如 172.17.0.1:7003）")
    ap.add_argument("--cmd-dir", default=os.environ.get("PATROL_CMD_DIR", "/app/data/cmd"),
                    help="手动指令通道目录：其中的 ESTOP 标志文件是**急停**，"
                         "巡检中每等一步都看它一眼（没有该文件就退化成纯巡检）")
    ap.add_argument("--no-estop", action="store_true",
                    help="忽略急停标志文件（排障用；正常运行**不要**关掉它）")
    ap.add_argument("--no-sync", action="store_true",
                    help="禁用向 prod 同步（outbox 只累积，待端点确定后补传）")

    # 图像微调（第二步定位，见 patrol.framing）：默认关闭——它需要一份"像素→偏移"的
    # 换算（--mm-per-px）和一个检测器实现；两者缺一就不该动机构。
    ap.add_argument("--framing", choices=("off", "once", "always"), default="off",
                    help="图像微调：off=只走到推导坐标；once=只微调还没学过的站位；"
                         "always=每站每轮都闭环（60 站各多拍一张 ≈ 每轮多约 10 分钟）")
    ap.add_argument("--mm-per-px", type=float, default=None,
                    help="图像像素 → mm 的标定换算（横向/Y）；给了才可能开启微调")
    ap.add_argument("--mm-per-px-z", type=float, default=None,
                    help="纵向/Z 的换算；省略则与 --mm-per-px 相同")
    ap.add_argument("--framing-sign-y", type=float, default=1.0, choices=(1.0, -1.0),
                    help="图像 x 正方向对应的 Y 方向（相机反装时取 -1）")
    ap.add_argument("--framing-sign-z", type=float, default=-1.0, choices=(1.0, -1.0),
                    help="图像 y 正方向对应的 Z 方向（默认：目标偏画面下 ⇒ 相机往下挪）")

    ap.add_argument("--ip", default=CONTROLLER_IP, help="控制器 IP")
    ap.add_argument("--port", type=int, default=CONTROLLER_PORT, help="控制器端口")
    ap.add_argument("--device", type=int, default=DEVICE_ID, help="设备 ID")
    ap.add_argument("--lib", default=None, help="SDK 动态库路径（或设 FMC4030_LIB_PATH）")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # 之后的每一行日志都同时落盘（--log）与 stdout。写在这里而不是一开始就设常量：
    # 走 --help / 参数错误时不该建文件。
    global log
    log = make_logger(args.log)

    # ---------- 准入（在库状态门禁）：**任何**连接动作之前 ----------
    # 第 0–1 天不动、第 26 天起不自动巡检；读不到入库日期也不动（见 patrol.room）。
    # 放在最前面是刻意的：判定不过就连控制器都不碰，不给"误动一下"留任何路径。
    #
    # 这一份只是**给人看的开场白**（也供 --why 用）。真正决定动不动的是两处：
    # `--once` 用它挡一次，常驻模式由 `PatrolDaemon` **每轮**现读 room.yaml 重新判定。
    try:
        room = load_room_state(args.room)
    except RoomStateError as e:
        room = None
        log(f"! 库房在库状态不可用：{e}")
    if room is not None:
        allowed, reason = room.verdict(date.today())
        log(f"准入判定：{reason}" + ("" if allowed else "  ⇒ 本轮不巡检"))
    else:
        allowed = False
        log("准入判定：拿不到入库日期 ⇒ 本轮不巡检（不会移动机构）")

    if args.why:
        return 0 if allowed else 1

    # ---------- 只读预检（不动机构）----------
    # `--check` 是运维的**显式**动作（"这机器现在能不能跑"），所以不受门禁影响：
    # 门禁管的是"无人值守时自动动"，不是"人想看它一眼"。反过来，常驻模式在门禁
    # 不过时**跳过**预检——不为一次不打算跑的巡检去连控制器，也不去加载厂商 SDK。
    fmc_ready = False
    _sdk = None

    def open_fmc() -> Fmc4030:
        """连接控制器。SDK **首次用到时才加载**——门禁不过的常驻进程不该加载它。"""
        nonlocal _sdk
        if _sdk is None:
            _sdk = load_library(args.lib)
        return Fmc4030.connect(
            lib=_sdk, device_id=args.device, ip=args.ip, port=args.port
        )

    if args.check or allowed:
        try:
            load_library(args.lib)      # 提前一次，把"库找不到"变成一句清楚的话
        except RuntimeError as e:
            log(f"! {e}")
            return 2

        log(f"连接控制器 {args.ip}:{args.port} …")
        try:
            fmc = open_fmc()
        except FmcError as e:
            log(f"! 连接失败: {e}")
            return 2
        try:
            try:
                fmc.check_soft_limits()   # ADR-0008：巡检启动前必须校验，否则长行程被静默截断
            except FmcError as e:
                log(f"! 软限位未整定，拒绝启动: {e}")
                return 2
            st = fmc.get_status()
            pos = " ".join(f"{s.name}={st.real_pos[s.index]:.2f}" for s in M1.axes)
            log(f"控制器就绪：位置 {pos}，模式={st.run_mode}")
            fmc_ready = True
        finally:
            fmc.close()
    else:
        log("准入未通过：跳过启动预检（本轮不碰控制器）")

    stations = load_station_list(args.stations, camera_ip=args.camera_ip)
    if len(stations) != len({s.id for s in stations}):
        log("! 站位表存在重复 id")
        return 2
    if any(not s.camera_ip for s in stations):
        log("! 站位表存在未指定相机 IP 的站位（老 YAML 需补 camera_ip）")
        return 2

    # ---------- 图像微调（第二步定位）：判定放在 --check 之前 ----------
    # 起不来不该拦住巡检（缺标定/缺解码器时只记一行日志，退回"只走推导坐标"），
    # 但**必须让 `--check` 也能看到结论**——否则运维以为开了，实际每轮都在走推导坐标。
    framing_wire = build_framing(args, log=log)
    log(framing_wire.reason)
    stations = [s.with_trim(clamp_trim((s.trim_y, s.trim_z))) for s in stations]
    apply_trim = make_apply_trim(stations, args.stations, log=log)

    if args.check:
        if not fmc_ready:
            log("! 预检未完成")
            return 2
        gate = "准入窗口内" if allowed else "不在准入窗口（不影响预检结论）"
        log(f"预检通过（{len(stations)} 站位，摄入端点 {args.ingest}，"
            f"同步{'禁用' if args.no_sync else '启用'}；今日{gate}）")
        return 0

    # ---------- 装配依赖 ----------
    capture = make_capture(args)
    store = JsonlStore(args.outbox)
    log(f"outbox {args.outbox}：{len(store.pending())} 条待同步")

    if args.no_sync:
        sync: object = _NullSync()
        log("同步已禁用（outbox 只累积，待 prod 端点确定后用同一 store 补传）")
    else:
        sync = make_sync(args)

    daemon = PatrolDaemon(
        open_fmc=open_fmc,
        stations=stations,
        capture_client=capture,
        store=store,          # type: ignore[arg-type]
        sync=sync,            # type: ignore[arg-type]
        # 传**路径**而不是读好的对象：常驻进程要每轮现读 room.yaml。传对象等于把准入
        # 依据冻结在启动那一刻——换批次（`deploy-fetch-room` 改文件）后进程还按旧日期
        # 判定，天数是错的，而它自己不知道。
        room_state_path=args.room,
        framing=framing_wire.hook,
        apply_trim=apply_trim,
        # 急停：console 按下 → 标志文件 → 这里每等一步看一眼 → 正在跑的那一轮被打断。
        abort=make_estop_check(args.cmd_dir, disabled=args.no_estop, log=log),
        log=log,
    )

    # ---------- 单轮 ----------
    if args.once:
        log("开始单轮巡检（--once）")
        t0 = time.monotonic()
        result = daemon.run_cycle()
        log(f"单轮结束：{result}，耗时 {time.monotonic() - t0:.1f} s")
        if result == "skipped":
            return 0            # 准入没过是"明确不动"，不是故障
        return 0 if result == "ok" else 1

    # ---------- 常驻 ----------
    # ⚠️ 常驻模式下**不因门禁未通过而退出**。曾经这里是 `if not allowed: return 0`，
    #    于是 systemd（Restart=on-failure）看到的是"正常退出"⇒ 不再拉起 ⇒ 库里一直
    #    没有新批次就永远没人巡检；等新批次真来了，进程早就不在了，除非有人发现。
    #    现在的语义：进程常驻，每个调度时刻重新读 room.yaml 判定；判定不过就不连控制器
    #    （run_cycle 的第一件事就是判定），窗口一开自动开始跑。
    stop = {"flag": False}

    def _on_signal(signum, _frame):  # pragma: no cover - 信号路径
        stop["flag"] = True
        log(f"收到信号 {signum}：本轮结束后退出")

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    schedule = (f"固定间隔 {args.interval} s" if args.interval is not None
                else f"每小时第 {args.at_minute} 分钟")
    log(f"进入常驻模式：{schedule}，站位 {len(stations)}，单轮约 6–7 min")
    if not allowed:
        log("准入未通过：进程保持常驻，每个调度时刻重新判定（期间不碰控制器）")

    rounds = 0
    while not stop["flag"]:
        if args.interval is not None:
            delay = args.interval
        else:
            delay = next_run_delay(datetime.now(), args.at_minute)
        log(f"下一次巡检在 {delay / 60:.1f} min 后")
        # 分片睡眠：便于及时响应停止信号，且不阻塞信号处理
        deadline = time.monotonic() + delay
        while not stop["flag"] and time.monotonic() < deadline:
            time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
        if stop["flag"]:
            break
        t0 = time.monotonic()
        result = daemon.run_cycle()
        rounds += 1
        log(f"第 {rounds} 轮结束：{result}，耗时 {time.monotonic() - t0:.1f} s")
        if args.max_rounds is not None and rounds >= args.max_rounds:
            log(f"已达 --max-rounds={args.max_rounds}，退出")
            break

    log("已退出")
    return 0


if __name__ == "__main__":
    sys.exit(main())

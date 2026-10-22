"""触发请求与手动指令的消费方：**持有硬件的那一个进程**。

`console` 只负责收请求（写文件），不碰控制器；真正跑轮、真正动手的是这里。三件事必须
同时成立：

1. **单实例**。FMC4030 是单会话控制器：两个消费方同时跑，第二个连接会把第一个踢掉，
   那一轮 48 张图就废了。所以这个进程只允许一份（compose 里 `replicas: 1` + 不重复起）。
2. **空闲时几乎不耗资源**。轮询触发目录（默认 5 秒一次），没有请求就睡——不是常驻
   跑巡检，巡检由算法侧每 3 小时投一次请求驱动（ADR-0012 §6）。
3. **手动指令优先于触发请求**。来巡检请求时若操作者正在手动操作，先把这一条做完再
   开轮——反过来（把人的动作堵在约 8 分钟的一轮后面）在现场是不可接受的。

为什么不做成"每轮一个容器"：那样每轮都要重新加载厂商 SDK、重建连接，而且两个容器
重叠的瞬间就可能双连接。一个常驻进程 + 单飞，是这台设备上最省心的形状。

## 巡检与手动为什么不会撞车（ADR-0013）

同一个线程：**跑轮的时候不领手动指令**。于是轮内提交的指令只有两种下场：

* 在本轮结束前 60 秒内提交的（`deploy.manual_exec.STALE_COMMAND_S`）会在轮末被领走
  并执行——操作者刚点的，执行是对的；
* 更早提交的（含整轮期间一直挂着的那条）一律判"已过期"拒绝——十分钟前点的"点动
  5mm"现在突然动一下，是最坏的一种惊喜。

更好的做法是**根本不让它提交进来**：console 在巡检进行中直接回 409
（`deploy.console` 的 `api_cmd`），这里的过期判定只是兜底：无论指令从哪条路进来，
都不会"十分钟后突然动一下"。
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from deploy.patrol_trigger import RunRequest, TriggerStore

DEFAULT_POLL_S = 5.0
#: 没有触发请求时，多久照看一次手动通道。人点了"点动"等 5 秒才动是不可接受的，
#: 而 0.5 秒一次 stat() 的开销可以忽略。
DEFAULT_MANUAL_POLL_S = 0.5
#: 条件不具备时多久探一次。用户口径（2026-10-22）：**每分钟看一次**——灯与加湿都是
#: 分钟级周期，等 3 小时的下一次触发等于白丢一轮。
DEFAULT_PROBE_POLL_S = 60.0
#: 条件一直不解除时最多守多久（分钟）。默认 180 min ≈ 一个调度周期：守到下一次触发
#: 就该让位，而不是把一轮挂一整天。设 0 = 不设上限。
DEFAULT_PROBE_WINDOW_MIN = 180.0


def serve_forever(
    *,
    store: TriggerStore,
    run_once: Callable[[], int],
    manual=None,
    poll_s: float = DEFAULT_POLL_S,
    manual_poll_s: float = DEFAULT_MANUAL_POLL_S,
    max_rounds: int | None = None,
    gate_ready: Callable[[], tuple[bool, str | None]] | None = None,
    probe_poll_s: float = DEFAULT_PROBE_POLL_S,
    probe_window_s: float = DEFAULT_PROBE_WINDOW_MIN * 60.0,
    log: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """轮询触发目录与手动通道；领到请求就跑一轮。返回跑过的轮数（`max_rounds` 用完即退）。

    ``manual`` 给的是 `deploy.manual_exec.ManualExecutor`（构造签名见那里）。不给它
    时行为与从前**完全一致**：只轮询触发目录、每次睡满 ``poll_s``。

    ``gate_ready`` 给的是"此刻能不能拍"的探针（`PatrolGate.ready_now`）。给了它时，
    领到请求**先探条件**：不具备就每分钟再看一次，确认具备才真正起轮。这样"到点遇上
    蓝光/加湿"不再等于丢掉这一轮（轮里那套 30 min 预算耗尽就会回原点跳过），而等待
    期间手动通道照常服务、机构不动一格。
    """
    stop = {"flag": False}

    def _on_signal(signum, _frame):        # pragma: no cover - 信号路径
        stop["flag"] = True
        log(f"收到信号 {signum}：本轮结束后退出")

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _on_signal)
        except ValueError:                 # 非主线程（测试里）
            pass

    def _service_manual() -> bool:
        """照看一次手动通道。返回是否真的处理了一条指令。"""
        if manual is None:
            return False
        try:
            return manual.service_once() is not None
        except Exception as e:  # noqa: BLE001 - 一条指令的意外不能带走执行方
            log(f"手动指令处理异常（{type(e).__name__}: {e}）——继续等下一次触发")
            return False

    what = "触发目录" + (" + 手动通道" if manual is not None else "")
    log(f"巡检执行方就绪：轮询 {store.dir_path}（{what}，触发每 {poll_s:.0f} 秒），单实例")
    rounds = 0
    while not stop["flag"]:
        _service_manual()
        req: RunRequest | None = store.consume()
        if req is None:
            # 空闲等待：按 poll_s 的节奏等下一次触发，**中间继续照看手动通道**。
            deadline = clock() + poll_s
            while not stop["flag"] and clock() < deadline:
                if manual is None:
                    sleep(poll_s)       # 没接手动通道 ⇒ 一次睡满（老行为）
                    break
                if _service_manual():
                    break               # 刚给操作者做完一件事，顺手再看一眼触发目录
                sleep(min(manual_poll_s, max(0.0, deadline - clock())))
            continue
        log(f"领到巡检请求 {req.id}（来自 {req.created_by}"
            f"{'：' + req.reason if req.reason else ''}），开始跑一轮")
        if gate_ready is not None:
            # 条件不具备 ⇒ **先别起轮**：每分钟探一次（不动机构、不连控制器），
            # 条件一好立刻跑。老做法是照跑、在轮里等满 30 min 再"回原点跳过"，
            # 一次蓝光/加湿就等于永久丢一轮数据（2026-10-22 用户口径）。
            waiting_from = clock()
            give_up = False
            while not stop["flag"]:
                ready, reason = gate_ready()
                waited = clock() - waiting_from
                if ready:
                    if waited >= probe_poll_s / 2:
                        log(f"  条件已具备（等了 {waited / 60:.1f} min），起轮")
                    break
                if probe_window_s > 0 and waited >= probe_window_s:
                    log(f"避让：{reason}，已守 {waited / 60:.0f} min 仍未解除"
                        f"（上限 {probe_window_s / 60:.0f} min）—— 本轮放弃")
                    give_up = True
                    break
                log(f"避让：{reason}，{probe_poll_s:.0f} s 后再探（本轮不消耗）")
                deadline = clock() + probe_poll_s
                while not stop["flag"] and clock() < deadline:
                    if manual is None:
                        sleep(min(probe_poll_s, max(0.0, deadline - clock())))
                        continue
                    if _service_manual():
                        break           # 刚给操作者做完一件事，顺手再看一眼条件
                    sleep(min(manual_poll_s, max(0.0, deadline - clock())))
                newer = store.consume()
                if newer is not None:
                    # 守了一轮多，下一班的请求已经来了：以最新为准，旧的不再补跑。
                    log(f"  避让期间来了新请求 {newer.id}（{req.id} 作废，按最新这条跑）")
                    req = newer
                    waiting_from = clock()
            if give_up:
                continue
        if req.not_before:
            # 偏置过的请求要留痕：现场问"这轮为什么晚了两分钟"时，答案在这里，
            # 而不是去翻 console 的内存（它早重启过了）。
            log(f"  该请求带偏置：创建于 {req.created_at}，应到 {req.not_before}")
        started = datetime.now()
        try:
            rc = run_once()
        except Exception as e:  # noqa: BLE001 - 一轮炸了不能把执行方带走
            log(f"巡检请求 {req.id} 执行异常（{type(e).__name__}: {e}）——继续等下一次触发")
            continue
        rounds += 1
        spent = (datetime.now() - started).total_seconds()
        log(f"巡检请求 {req.id} 完成：rc={rc}，耗时 {spent:.0f} s")
        if max_rounds is not None and rounds >= max_rounds:
            log(f"已达 max_rounds={max_rounds}，退出")
            break
    log("巡检执行方已退出")
    return rounds


def parse_args(argv: list[str] | None = None):
    """解析执行方自己的参数，**其余原样透给 `deploy.m1`**。

    为什么用 `parse_known_args`：入口脚本（`docker/patrol-entrypoint.sh` 的 `patrol-serve`
    角色）把 m1 的参数**跟在后面**：

        patrol-serve --trigger-dir X --room Y --stations Z --outbox …

    而 argparse 不支持"位置参数与可选参数交替"——2026-09-15 上机时正是这里炸的：容器
    反复重启，日志里只有一句 ``unrecognized arguments: --room …``。改成 parse_known_args
    之后，顺序随便、`--` 分隔符可有可无，未识别的部分原样交给 m1（真写错了 m1 会自己报错）。
    """
    import argparse

    ap = argparse.ArgumentParser(prog="deploy.patrol_serve",
                                 description="触发请求与手动指令的执行方（单实例，持有控制器）",
                                 # ⚠️ 关掉前缀缩写：我们新加的 `--room-id` 会让 `--room`
                                 # 被 argparse 当成它的缩写吃掉，而 `--room` 是**透传给 m1 的
                                 # 路径**（2026-10-14 加 `--room-id` 时踩到：两条既有用例当场红）。
                                 allow_abbrev=False)
    ap.add_argument("--trigger-dir", default=os.environ.get("PATROL_TRIGGER_DIR",
                                                            "/app/data/trigger"))
    ap.add_argument("--cmd-dir", default=os.environ.get("PATROL_CMD_DIR", "/app/data/cmd"),
                    help="手动指令通道目录（console 往这里写，执行方从这里领；"
                         "其中的 ESTOP 文件也会透给巡检那一轮）")
    ap.add_argument("--poll", type=float, default=float(os.environ.get("PATROL_POLL_S",
                                                                      DEFAULT_POLL_S)))
    ap.add_argument("--manual-poll", type=float,
                    default=float(os.environ.get("PATROL_MANUAL_POLL_S",
                                                 DEFAULT_MANUAL_POLL_S)))
    ap.add_argument("--probe-poll", type=float,
                    default=float(os.environ.get("PATROL_PROBE_POLL_S",
                                                 DEFAULT_PROBE_POLL_S)),
                    help="条件不具备时多久探一次（秒）。探针只抓一帧判蓝光 + 查加湿，"
                         "不动机构、不连控制器、不消耗触发请求")
    ap.add_argument("--probe-window-min", type=float,
                    default=float(os.environ.get("PATROL_PROBE_WINDOW_MIN",
                                                 DEFAULT_PROBE_WINDOW_MIN)),
                    help="条件一直不解除时最多守多久（分钟）；0 = 不设上限")
    ap.add_argument("--no-manual", action="store_true",
                    help="不接手动通道（只跑巡检；排障时用来排除干扰）")
    ap.add_argument("--max-rounds", type=int, default=None, help="跑够几轮就退出（调试用）")
    ap.add_argument("--room-id", default=os.environ.get("PATROL_ROOM_ID", ""),
                    help="库房号（如 611）：每轮开始前用它去台账端点刷新 room.yaml。"
                         "没给就退回读 room.yaml 里的 room_id（unset 视为没给）")
    ap.add_argument("--no-room-refresh", action="store_true",
                    help="关掉『轮次前刷新 room.yaml』（排障用；默认开）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只做预检：解析参数、装配执行方并打印结论后退出，"
                         "**不连控制器、不进循环**（上机第一步用它）")
    # 其余参数（--room/--stations/--outbox/--log/--capture-host/--ingest/--no-estop …）
    # 原样透给 deploy.m1
    args, passthrough = ap.parse_known_args(argv)
    # 分隔符本身不留（没有位置参数时 argparse 会把它留在 extras 里）；m1 那边不需要它，
    # 留着只会让"透传了什么"这件事多一个要看懂的符号。
    args.m1_args = [a for a in passthrough if a != "--"]
    return args


def preflight(args, *, store: TriggerStore, manual=None, log=print,
              load_sdk=None) -> int:
    """`--dry-run` 的实现：把"待会儿要用的东西"逐条打出来，**不连控制器、不动机构**。

    上机时的第一道检查——它会暴露"路径写错/挂载没生效/急停文件不在共享目录"这类问题，
    而这些问题在真跑一轮时才会以别的方式（比如"拍了 48 张但一张都没进 outbox"）暴露。

    其中**厂商动态库要真的 load 一次**：2026-09-15 上机时页面报的
    ``GLIBCXX_3.4.32 not found`` 就是"库在那儿、但容器里的 C++ 运行时太旧"，而
    "库在那儿"这件事光看路径存在是看不出来的。load 不等于连控制器（后者要
    ``FMC4030.connect``），所以这一步仍然是只读的。
    """
    from deploy.m1 import build_parser

    m1 = build_parser().parse_args(list(args.m1_args))
    log(f"触发目录   {store.dir_path}（每 {args.poll:.0f} 秒看一次）")
    log(f"指令目录   {args.cmd_dir}{'（手动通道已关闭 --no-manual）' if manual is None else ''}")
    if manual is not None:
        log(f"急停标志   {manual.channel.estop_path}"
            f"{'（已存在：当前处于急停闩锁）' if manual.channel.raised() else ''}")
        log(f"站位表     {len(manual.stations)} 个站位"
            f"{'（读不到，抓拍会被拒绝）' if not manual.stations else ''}")
    log(f"库房状态   {m1.room}")
    log(f"站位表     {m1.stations}")
    log(f"outbox     {m1.outbox}")
    log(f"日志       {m1.log or '(只打 stdout)'}")
    log(f"采图服务   {m1.capture_host}")
    log(f"摄入端点   {m1.ingest}{'（同步已禁用）' if m1.no_sync else ''}")
    log(f"控制器     {m1.ip}:{m1.port}（device={m1.device}，本轮不连）")
    log(f"图像微调   {m1.framing}")

    # 测量接线（票 12）：走的是 m1 装配时**同一个**判定函数——`--dry-run` 看到的那一行
    # 就是真跑时打的那一行。测量起不来只记一行、不拦巡检；唯一例外是 `--measure on`
    # 却拿不到 room_id（m1 会 fail-closed 返回 2，这里提前把同一结论说出来）。
    from deploy.measure_wire import build as build_measure

    room_id = resolve_room_id("", m1.room)
    measure = build_measure(m1, room_id=room_id, log=log)
    log(f"测量接线   {measure.reason}")
    measure_blocked = False
    if m1.measure == "on" and not measure.enabled:
        from deploy.measure_wire import room_id_is_usable

        if not room_id_is_usable(room_id):
            log("!! 测量：--measure on 但 room.yaml 里没有可用的 room_id"
                " —— 真启动会拒绝（fail-closed），本轮不接测量")
            measure_blocked = True

    if load_sdk is None:                       # 真正 load 一次厂商库（不连接）
        from patrol.fmc.loader import load_library
        load_sdk = lambda: load_library(m1.lib)
    try:
        load_sdk()
    except RuntimeError as e:
        log(f"!! 厂商库加载失败：{e}")
        log("   —— 路径不对/没挂进来，或者 C++ 运行时太旧（GLIBCXX 版本不够）：")
        log("      `ldd <库>` 会直接指出缺哪个 GLIBCXX；本镜像基于 trixie 就是为这个。")
        log("预检失败：厂商库都加载不了，手动指令与巡检都跑不起来")
        return 2
    log(f"厂商库     {m1.lib or '(环境变量 FMC4030_LIB_PATH)'} —— 已加载 ✅")

    if measure_blocked:
        log("预检结论：**不能按当前参数启动**（测量 fail-closed，见上）")
        return 2
    log("预检结束：**没有连接控制器，也没有进循环**")
    return 0


def resolve_room_id(explicit: str, room_path: str) -> str:
    """库房号：``--room-id``/``PATROL_ROOM_ID`` → room.yaml 里的 ``room_id``（``unset`` 不算）。

    为什么要有显式来源：**文件缺失时也得知道去查哪个库房**（票 18 §2 第 5 条：
    文件缺失 + 端点可用 ⇒ 写一份）。读不到就返回空串，调用方跳过刷新并告警，**不猜**。
    """
    from patrol.room import RoomStateError, load_room_state

    text = (explicit or "").strip()
    if text:
        return text
    try:
        candidate = load_room_state(room_path).room_id.strip()
    except (RoomStateError, OSError):
        return ""
    return "" if candidate in ("", "unset") else candidate


@dataclass
class RefreshStats:
    """轮次前刷新的**进程内**计数（不新增持久化；票 18 §2.3）。"""

    failures: int = 0
    last_ok_note: str = ""
    last_ok_at: str = ""


def refresh_before_round(*, room_id: str, room_path: str, stats: RefreshStats,
                         log=print, refresh=None) -> None:
    """轮次前的台账刷新：**失败只告警 + 计数，绝不停轮**（Chief 2026-10-14 裁定）。

    "失败也继续跑"的理由：停轮会丢掉用户要的连续影像数据；而"批次刚换 + 端点恰好不可用"
    这个窗口会被下一次成功轮询在 ≤1 个轮间隔内抓到。**但不许静默**——每轮都要有一行。
    """
    if not room_id:
        log("！room-refresh 跳过：没拿到库房号（--room-id / PATROL_ROOM_ID 都没给，"
            "room.yaml 里也没有可用的 room_id）——本轮沿用现有 room.yaml")
        return
    fn = refresh or _default_refresh()
    try:
        result = fn(room_id, room_path, log=log)
    except Exception as exc:      # noqa: BLE001 - 刷新自己出意外也不许拖垮巡检
        result = {"error": f"{type(exc).__name__}: {exc}", "entry_date": None, "note": ""}
    if result.get("error"):
        stats.failures += 1
        log(f"！room-refresh 失败第 {stats.failures} 次（{result['error']}）；"
            f"沿用最后已知批次 entry_date={result.get('entry_date') or '(未知)'}，本轮照跑")
        return
    stats.failures = 0
    stats.last_ok_note = str(result.get("note") or "")
    stats.last_ok_at = f"{datetime.now():%Y-%m-%d %H:%M:%S}"
    log(f"room-refresh ok：{stats.last_ok_note or result.get('entry_date')}；"
        f"最后一次成功刷新 {stats.last_ok_at}")


def _default_refresh():
    from deploy.fetch_room import refresh_room_file

    return refresh_room_file


def make_run_once(*, m1_main, m1_args: list[str], room_id: str, room_path: str,
                  stats: RefreshStats, log=print, refresh=None):
    """轮次入口：**先刷新 room.yaml，再起这一轮**（票 18）。刷新失败不影响起轮。"""

    def run_once() -> int:
        refresh_before_round(room_id=room_id, room_path=room_path, stats=stats,
                             log=log, refresh=refresh)
        return m1_main(list(m1_args))

    return run_once


def build_gate_probe(m1_parsed, *, room_id: str, log=print):
    """造"此刻能不能拍"的探针；装不上就返回 None（退回老行为：到点就跑）。

    **复用 `deploy.patrol_gate` 的同一套判定**（加湿走 `/env/humid`、蓝光走一帧的
    B/R），不另写一份：两份判定迟早漂移成"外层说能拍、轮里说不能拍"，现场只会更难查。
    探针只建采图客户端——不加载厂商库、不连控制器。
    """
    try:
        from deploy.m1 import make_capture
        from deploy.patrol_gate import build_gate

        capture = make_capture(m1_parsed)
        gate = build_gate(m1_parsed, capture=capture, room_id=room_id, log=log)
        if gate is None:
            return None
        return gate.ready_now
    except Exception as e:  # noqa: BLE001 - 探针装不上不该把执行方带停
        log(f"! 条件探针装配失败（{type(e).__name__}: {e}）——退回'到点就跑'")
        return None


def main(argv: list[str] | None = None) -> int:
    """命令行入口（容器里的 `patrol-serve` 角色）。

    跑一轮用的就是 `deploy.m1 --once`：准入判定、软限位校验、急停链、取证与同步
    全都在那一条路径上，这里**不复制**任何一条——复制出来的第二份迟早会与第一份不一致。
    """
    from deploy.m1 import build_parser as m1_parser
    from deploy.m1 import main as m1_main

    args = parse_args(argv)

    store = TriggerStore(dir_path=args.trigger_dir)
    manual = None if args.no_manual else build_manual(args)

    if args.dry_run:
        return preflight(args, store=store, manual=manual)

    # 跑一轮用的仍是 `deploy.m1 --once`（准入/软限位/急停链/取证全在那条路上），只把
    # **指令目录**显式带过去：两边必须看同一个 ESTOP 文件，否则"按了急停"拦不住正在
    # 跑的那一轮。操作者若自己传了 --cmd-dir，以他的为准。
    m1_args = list(args.m1_args)
    if "--cmd-dir" not in m1_args:
        m1_args += ["--cmd-dir", args.cmd_dir]
    # 保持目录同样要带过去：**整轮避让**的判定发生在 m1 那一轮里（读 hold.json），
    # 执行方这边只知道"到点该跑"，不知道跑的中途有人设了加湿保持。
    if "--trigger-dir" not in m1_args:
        m1_args += ["--trigger-dir", args.trigger_dir]

    # 轮次入口：**先刷新 room.yaml，再起这一轮**（票 18）。房间路径与库房号都从 m1 的
    # 同一套参数里取——两个进程读的必须是同一份 room.yaml，参数名一旦分叉就会各读各的。
    m1_parsed = m1_parser().parse_args(list(m1_args))
    stats = RefreshStats()

    def log(msg: str) -> None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)

    if args.no_room_refresh:
        log("！room-refresh 已关闭（--no-room-refresh）：本轮沿用现有 room.yaml")
        gate_ready = None                         # 排障旁路不接探针（与老行为一致）

        def run_once() -> int:                    # 排障旁路：不刷新，直接起轮
            return m1_main([*m1_args, "--once"])
    else:
        from deploy.fetch_room import analysis_base_url

        room_id = resolve_room_id(args.room_id, m1_parsed.room)
        log(f"room-refresh：库房 {room_id or '(未拿到)'}，文件 {m1_parsed.room}，"
            f"端点 {analysis_base_url()}")
        gate_ready = build_gate_probe(m1_parsed, room_id=room_id, log=log)
        if gate_ready is not None:
            log(f"条件探针就位：起轮前每 {args.probe_poll:.0f} s 看一眼（最多守 "
                f"{args.probe_window_min:.0f} min；不动机构、不连控制器）")
        run_once = make_run_once(m1_main=m1_main, m1_args=[*m1_args, "--once"],
                                 room_id=room_id, room_path=m1_parsed.room,
                                 stats=stats, log=log)

    rounds = serve_forever(
        store=store,
        run_once=run_once,
        manual=manual,
        poll_s=args.poll,
        manual_poll_s=args.manual_poll,
        max_rounds=args.max_rounds,
        gate_ready=gate_ready,
        probe_poll_s=args.probe_poll,
        probe_window_s=args.probe_window_min * 60.0,
    )
    return 0 if rounds >= 0 else 1


def build_manual(args):
    """按 `patrol-m1` 的同一套参数装配手动执行方（返回 `ManualExecutor`）。

    刻意**复用 m1 的参数解析**（`build_parser`）而不是另立一套 `--stations/--outbox/…`：
    同机两个进程读的必须是同一份站位表、同一份 outbox、同一份入库日期，参数名一旦
    分叉，迟早会出现"手动用的站位表和巡检的不是同一份"这种事。
    """
    from patrol.fmc import Fmc4030
    from patrol.fmc.loader import load_library
    from patrol.room import RoomStateError, load_room_state
    from patrol.store import JsonlStore

    from deploy.m1 import build_parser, load_station_list, make_capture, make_sync
    from deploy.manual import ManualChannel
    from deploy.manual_exec import ManualExecutor

    m1_args = build_parser().parse_args(list(args.m1_args))

    def log(msg: str) -> None:
        print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)

    # 厂商库**首次用到时才加载**：没人碰手动通道时不该白白加载它。
    sdk: dict = {"lib": None}

    def connect() -> Fmc4030:
        if sdk["lib"] is None:
            sdk["lib"] = load_library(m1_args.lib)
        return Fmc4030.connect(lib=sdk["lib"], device_id=m1_args.device,
                               ip=m1_args.ip, port=m1_args.port)

    # 站位表读不出来**不该拖垮手动移动**：机动（点动/回零/定位）根本不需要站位表，
    # 只有"抓拍归到哪个站位"需要。所以这里降级成空表 + 一句明说，让运维至少还能把
    # 机器挪回来。（巡检那边该失败还是会失败——它读的是同一份文件。）
    try:
        stations = load_station_list(m1_args.stations, camera_ip=m1_args.camera_ip, log_fn=log)
    except Exception as e:  # noqa: BLE001 - 降级，不退出
        stations = []
        log(f"! 站位表不可用（{type(e).__name__}: {e}）——手动移动仍可用，抓拍会被拒绝")

    def room_fields() -> dict:
        """库房维度（与巡检写入的行同源）：读不到就写 NULL，不编默认值。"""
        try:
            rs = load_room_state(m1_args.room)
        except RoomStateError:
            return {"room_id": None, "entry_date": None, "batch_no": None}
        return {"room_id": rs.room_id, "entry_date": rs.entry_date.isoformat(),
                "batch_no": rs.batch_no}

    store = JsonlStore(m1_args.outbox)
    sync = make_sync(m1_args)

    def flush() -> object:
        return sync.flush(store)

    return ManualExecutor(
        ManualChannel(args.cmd_dir),
        connect=connect,
        stations=stations,
        capture=make_capture(m1_args),
        append_index=store.append,
        flush=flush,
        room_fields=room_fields,
        log=log,
    )


if __name__ == "__main__":      # pragma: no cover
    import sys

    sys.exit(main())

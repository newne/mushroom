"""触发请求的执行方：领请求 → 跑一轮 → 继续等。

这里测的三条都是"现场会疼"的性质：
1. 没有请求时**什么都不做**（不能变成常驻巡检，那会挤掉算法侧的节奏）；
2. 一轮炸了**不能把执行方带走**（否则之后所有触发都没人接）；
3. 一轮只跑一次（重复领同一条请求 = 多跑一轮 = 白占 11 分钟）。
"""

from __future__ import annotations

from datetime import datetime

from deploy.patrol_serve import serve_forever
from deploy.patrol_trigger import TriggerStore

T0 = datetime(2026, 9, 14, 10, 0, 0)


def make_store(tmp_path) -> TriggerStore:
    return TriggerStore(dir_path=str(tmp_path / "trigger"), now=lambda: T0)


def test_idle_loop_does_not_run_anything(tmp_path):
    """没有请求就只睡觉——执行方不是"常驻巡检"。"""
    store = make_store(tmp_path)
    slept: list[float] = []
    runs: list[int] = []

    def fake_sleep(s):
        slept.append(s)
        if len(slept) >= 3:
            raise KeyboardInterrupt          # 用中断结束循环

    try:
        serve_forever(store=store, run_once=lambda: runs.append(1) or 0,
                      sleep=fake_sleep, log=lambda _m: None)
    except KeyboardInterrupt:
        pass
    assert runs == [], "没有请求时一轮都不该跑"
    assert slept == [5.0, 5.0, 5.0]


def test_consumes_the_request_and_runs_exactly_one_round(tmp_path):
    store = make_store(tmp_path)
    store.request(by="scheduler", reason="每 3 小时")
    runs: list[int] = []

    def run_once():
        runs.append(1)
        return 0

    rounds = serve_forever(store=store, run_once=run_once, max_rounds=1,
                           sleep=lambda _s: None, log=lambda _m: None)

    assert rounds == 1 and len(runs) == 1
    assert store.current().pending is False, "请求必须被标记为已领走"
    assert store.consume() is None, "同一条请求不能再被领一次"


def test_a_failing_round_does_not_kill_the_runner(tmp_path):
    """一轮异常只是这一轮的事：执行方要继续活着等下一次触发。"""
    store = make_store(tmp_path)
    logs: list[str] = []
    calls: list[int] = []

    def run_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("控制器掉线")
        return 0

    # 第一次触发：抛异常；第二次触发：正常
    store.request(by="scheduler")
    state = {"n": 0}

    def loop_sleep(_s):
        state["n"] += 1
        if state["n"] == 1:
            store.request(by="scheduler")     # 再投一次，验证它还能领
        elif state["n"] >= 3:
            raise KeyboardInterrupt

    try:
        serve_forever(store=store, run_once=run_once, sleep=loop_sleep, log=logs.append)
    except KeyboardInterrupt:
        pass

    assert len(calls) == 2, "第一次抛异常后，第二次触发仍然跑了"
    assert any("执行异常" in m for m in logs)


def test_max_rounds_stops_the_loop(tmp_path):
    """`--max-rounds` 是调试用的闸门：跑够就退，不要赖在循环里。"""
    store = make_store(tmp_path)
    store.request(by="scheduler")
    runs: list[int] = []

    rounds = serve_forever(store=store, run_once=lambda: runs.append(1) or 0,
                           max_rounds=1, sleep=lambda _s: None, log=lambda _m: None)

    assert rounds == 1 and len(runs) == 1


def test_reason_is_logged_for_the_field(tmp_path):
    """现场要能回答"这一轮是谁触发的、为什么"——日志里必须带上。"""
    store = make_store(tmp_path)
    store.request(by="scheduler", reason="每 3 小时定时巡检")
    logs: list[str] = []
    serve_forever(store=store, run_once=lambda: 0, max_rounds=1,
                  sleep=lambda _s: None, log=logs.append)
    joined = "\n".join(logs)
    assert "scheduler" in joined and "每 3 小时定时巡检" in joined


# ---------- 手动通道（ADR-0013：轮间随时可用，轮内不让动） ----------


class FakeManual:
    """假执行方：只统计"被照看了几次、处理了几条"，以及处理的次序。"""

    def __init__(self, pending: int = 0, order: list[str] | None = None, boom: bool = False):
        self.pending = pending
        self.order = order if order is not None else []
        self.boom = boom
        self.calls = 0

    def service_once(self):
        self.calls += 1
        self.order.append("manual")
        if self.boom:
            raise RuntimeError("通道文件读坏了")
        if self.pending > 0:
            self.pending -= 1
            return object()             # 处理了一条
        return None


def fake_clock():
    """可控时钟：sleep 会推进它，于是"空闲分片"是可断言的行为而不是靠真实等待。"""
    state = {"t": 0.0}
    slept: list[float] = []

    def clock() -> float:
        return state["t"]

    def sleep(s: float) -> None:
        slept.append(s)
        state["t"] += s
        if len(slept) > 40:
            raise KeyboardInterrupt

    return clock, sleep, slept


def test_manual_channel_is_serviced_while_idle(tmp_path):
    """没有巡检请求时，手动通道照样被高频照看：人点了"点动"不该等 5 秒。"""
    store = make_store(tmp_path)
    manual = FakeManual()
    clock, sleep, slept = fake_clock()
    try:
        serve_forever(store=store, run_once=lambda: 0, manual=manual,
                      sleep=sleep, clock=clock, log=lambda _m: None)
    except KeyboardInterrupt:
        pass
    assert manual.calls >= 3
    assert slept and all(s == 0.5 for s in slept), "空闲时按 manual_poll_s 分片，而不是睡满 5 秒"


def test_manual_command_goes_first_when_a_round_is_waiting(tmp_path):
    """人的动作优先于巡检请求：两者同时在，先做人的那一条。"""
    store = make_store(tmp_path)
    store.request(by="scheduler")
    order: list[str] = []
    manual = FakeManual(pending=1, order=order)

    def run_once():
        order.append("round")
        return 0

    serve_forever(store=store, run_once=run_once, manual=manual, max_rounds=1,
                  sleep=lambda _s: None, clock=lambda: 0.0, log=lambda _m: None)
    assert order[0] == "manual" and "round" in order


def test_a_broken_manual_channel_does_not_kill_the_runner(tmp_path):
    """手动通道出问题只是它自己的事：执行方要继续活着等下一次触发。"""
    store = make_store(tmp_path)
    logs: list[str] = []
    clock, sleep, _slept = fake_clock()
    try:
        serve_forever(store=store, run_once=lambda: 0, manual=FakeManual(boom=True),
                      sleep=sleep, clock=clock, log=logs.append)
    except KeyboardInterrupt:
        pass
    assert any("手动指令处理异常" in m for m in logs)
    assert any("就绪" in m for m in logs)


def test_no_manual_channel_keeps_the_plain_sleep(tmp_path):
    """不接手动通道时，循环行为与从前**完全一致**（一次睡满 poll_s）。"""
    store = make_store(tmp_path)
    slept: list[float] = []

    def sleep(s):
        slept.append(s)
        if len(slept) >= 3:
            raise KeyboardInterrupt

    try:
        serve_forever(store=store, run_once=lambda: 0, sleep=sleep, log=lambda _m: None)
    except KeyboardInterrupt:
        pass
    assert slept == [5.0, 5.0, 5.0]


# ---------- 参数解析：入口脚本的调用形状（2026-09-15 上机时炸的就是这里） ----------


def test_m1_options_after_our_own_are_passed_through():
    """入口脚本把 m1 的参数**跟在后面**，混合顺序也必须能吃下。

    现场表现：容器反复重启，日志只有一句 ``unrecognized arguments: --room …``。
    根因是 m1 参数原先声明成位置参数（`nargs="*"`），而 argparse 不支持"位置参数与
    可选参数交替"。
    """
    from deploy.patrol_serve import parse_args

    argv = ["--trigger-dir", "/t", "--cmd-dir", "/c",
            "--room", "/r.yaml", "--stations", "/s.yaml", "--outbox", "/o.jsonl",
            "--log", "/l.log", "--capture-host", "172.17.0.1:7003",
            "--ingest", "http://172.17.0.1:8000/ingest"]
    args = parse_args(argv)

    assert args.trigger_dir == "/t" and args.cmd_dir == "/c"
    assert args.m1_args == argv[4:], "m1 的参数要原样、按序透传"


def test_double_dash_separator_is_accepted_too():
    """`--` 分隔符在不在都行（两种写法都要活）。"""
    from deploy.patrol_serve import parse_args

    args = parse_args(["--trigger-dir", "/t", "--", "--room", "/r.yaml"])
    assert args.trigger_dir == "/t" and args.m1_args == ["--room", "/r.yaml"]


def test_our_own_flags_are_not_swallowed_by_the_passthrough():
    """我们自己的开关不能被当成 m1 的参数漏过去（否则执行方会去跑巡检的常驻模式）。"""
    from deploy.patrol_serve import parse_args

    args = parse_args(["--max-rounds", "2", "--no-manual", "--manual-poll", "1.5",
                       "--dry-run", "--room", "/r.yaml"])
    assert args.max_rounds == 2 and args.no_manual and args.manual_poll == 1.5 and args.dry_run
    assert args.m1_args == ["--room", "/r.yaml"]


def test_dry_run_preflight_touches_nothing_and_reports_paths(tmp_path, capsys):
    """`--dry-run` 是上机第一道检查：把要用的路径与配置打出来，**不连控制器、不进循环**。"""
    from deploy.patrol_serve import parse_args, preflight

    args = parse_args(["--trigger-dir", str(tmp_path / "trigger"),
                       "--cmd-dir", str(tmp_path / "cmd"), "--dry-run",
                       "--stations", str(tmp_path / "s.yaml"), "--room", str(tmp_path / "r.yaml"),
                       "--outbox", str(tmp_path / "o.jsonl"), "--log", ""])
    logs: list[str] = []
    rc = preflight(args, store=make_store(tmp_path), manual=None, log=logs.append)

    assert rc == 0
    joined = "\n".join(logs)
    assert str(tmp_path / "trigger") in joined
    assert "没有连接控制器" in joined
    assert "手动通道已关闭" in joined

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

"""触发"跑一轮"：投请求 / 去重 / 单飞 / **时间偏置与保持**，以及 console 上的端点。

调度器（算法工程的 APScheduler）每 3 小时打一次 `POST /api/patrol/run`，
它 `max_instances=1`、`misfire_grace_time=300s`，而我们一轮 11 分钟 ——
所以这个端点**必须立刻返回**，绝不能在里面等巡检跑完。这些用例钉的就是这条。

偏置（2026-10-12 加）钉的是另一条：**加湿期不许拍**。水汽重的时候 48 张全过曝，
白动机架 11 分钟。所以"晚点再跑"必须落在请求文件里（console 重启不丢），
并且**不能被过期判断吃掉**——后者是这条设计里最容易静默失效的地方。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from deploy.console import ConsoleDeps, create_app
from deploy.patrol_trigger import (
    DEFAULT_DEFER_S,
    MAX_DEFER_S,
    STALE_AFTER_S,
    TriggerStore,
)
from fastapi.testclient import TestClient

T0 = datetime(2026, 9, 14, 10, 0, 0)


def store(tmp_path, *, now=T0) -> TriggerStore:
    return TriggerStore(dir_path=str(tmp_path / "trigger"), now=lambda: now)


def client(tmp_path, *, now=T0) -> TestClient:
    room = tmp_path / "room.yaml"
    room.write_text('room_id: "611"\nentry_date: "2026-09-04"\n', encoding="utf-8")
    stations = tmp_path / "stations.yaml"
    stations.write_text("stations:\n  - {id: S101, box_id: B101, y: 1.0, z: -1.0, layer: 1, col: 1}\n",
                        encoding="utf-8")
    deps = ConsoleDeps(room_path=str(room), stations_path=str(stations),
                       outbox_path=str(tmp_path / "outbox.jsonl"),
                       runs_dir=str(tmp_path / "runs"),
                       trigger_dir=str(tmp_path / "trigger"),
                       cmd_dir=str(tmp_path / "cmd"), now=lambda: now)
    return TestClient(create_app(deps))


# ---------- 存储层：单飞与过期 ----------


def test_request_creates_a_pending_job(tmp_path):
    s = store(tmp_path)
    req, created = s.request(by="scheduler", reason="每 3 小时")
    assert created is True and req.pending is True
    assert s.path.exists()
    assert s.current().id == req.id


def test_second_request_is_refused_not_queued(tmp_path):
    """不排队是刻意的：排队会让两次请求挤在一起连跑 22 分钟。"""
    s = store(tmp_path)
    first, _ = s.request(by="scheduler")
    second, created = s.request(by="operator")
    assert created is False
    assert second.id == first.id, "返回的是现有那条请求，不是新建的"


def test_stale_request_is_replaced(tmp_path):
    """请求过了很久没人消费（说明执行方没在跑）⇒ 允许新的取而代之，并留一句说明。"""
    s = store(tmp_path)
    s.request(by="scheduler")
    later = T0 + timedelta(seconds=STALE_AFTER_S + 1)
    s.now = lambda: later
    req, created = s.request(by="operator")
    assert created is True
    assert "过期" in req.note


def test_consume_marks_it_done_and_is_idempotent(tmp_path):
    s = store(tmp_path)
    s.request(by="scheduler")
    got = s.consume()
    assert got is not None and got.consumed_at is not None
    assert s.consume() is None, "同一条请求只能被取走一次"
    assert s.current().pending is False


def test_broken_trigger_file_reads_as_no_request(tmp_path):
    """半截 JSON（正在写）不能把消费方搞崩，也不能被当成有效请求。"""
    s = store(tmp_path)
    s.path.parent.mkdir(parents=True, exist_ok=True)
    s.path.write_text('{"id": "x", "created_at":', encoding="utf-8")
    assert s.current() is None
    assert s.is_stale() is False


def test_history_keeps_every_request(tmp_path):
    """历史留档：现场要能回答"这几轮是谁触发的"。"""
    s = store(tmp_path)
    s.request(by="scheduler", reason="第一次")
    s.consume()
    s.now = lambda: T0 + timedelta(hours=3)
    s.request(by="scheduler", reason="第二次")
    lines = (Path(s.dir_path) / s.history_name).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert [json.loads(ln)["reason"] for ln in lines] == ["第一次", "第二次"]


# ---------- HTTP 层 ----------


def test_post_returns_202_immediately(tmp_path):
    with client(tmp_path) as c:
        r = c.post("/api/patrol/run?reason=每3小时")
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] is True
    assert body["job"]["pending"] is True
    assert body["job"]["created_by"] == "scheduler"
    assert body["job"]["reason"] == "每3小时"


def test_duplicate_post_returns_409_with_the_existing_job(tmp_path):
    with client(tmp_path) as c:
        first = c.post("/api/patrol/run").json()["job"]
        second = c.post("/api/patrol/run")
    assert second.status_code == 409
    assert second.json()["accepted"] is False
    assert second.json()["job"]["id"] == first["id"]


def test_get_reports_pending_state(tmp_path):
    with client(tmp_path) as c:
        got = c.get("/api/patrol/run").json()
        assert got["job"] is None and got["pending"] is False
        assert got["age_s"] is None and got["stale"] is False
        assert got["due_at"] is None and got["wait_s"] is None
        assert got["hold"] == {"active": False}
        c.post("/api/patrol/run")
        after = c.get("/api/patrol/run").json()
    assert after["pending"] is True and after["stale"] is False and after["age_s"] is not None
    assert after["due_at"] == after["job"]["created_at"] and after["wait_s"] == 0
    assert after["hold"]["active"] is False


def test_trigger_survives_console_restart(tmp_path):
    """请求落在共享目录里：console 重启（或换一个进程）不影响已投的请求。"""
    with client(tmp_path) as c:
        job = c.post("/api/patrol/run?reason=x").json()["job"]
    with client(tmp_path) as c2:
        got = c2.get("/api/patrol/run").json()
    assert got["job"]["id"] == job["id"] and got["pending"] is True


def test_trigger_does_not_touch_the_controller(tmp_path):
    """触发接口只写文件：它不该让 console 去连控制器（那是执行方的事，单会话设备）。"""
    with client(tmp_path) as c:
        c.post("/api/patrol/run")
        st = c.get("/api/status").json()
    assert st["machine"]["connected"] is False
    assert st["machine"]["probe_suppressed"] is False


# ---------- 时间偏置：加湿期不许拍 ----------


def test_deferred_request_is_not_consumed_until_due(tmp_path):
    """没到 `not_before` 就领不到——消费方每秒来问，到点才取走。"""
    s = store(tmp_path)
    req, _ = s.request(by="operator", defer_s=600)
    assert req.not_before is not None
    assert s.consume() is None, "偏置中的请求不该被领走"
    assert s.current().pending is True

    s.now = lambda: T0 + timedelta(seconds=601)
    assert s.consume() is not None


def test_deferred_request_survives_the_stale_window(tmp_path):
    """**这条是偏置设计的地基**：过期必须从 `not_before` 起算。

    若还从 `created_at` 算，偏置 20 分钟的请求在第 15 分钟就被判过期，随后到的触发
    会把它替换成一条**立即执行**的新请求——偏置被静默吃掉，页面上看不出任何异常。
    """
    s = store(tmp_path)
    s.request(by="scheduler", defer_s=1200)
    s.now = lambda: T0 + timedelta(seconds=STALE_AFTER_S + 1)   # 创建后 15 分钟，距应到还有 5 分钟
    assert s.is_stale() is False, "偏置中的请求不许被判过期"
    req, created = s.request(by="scheduler", reason="3 小时到了")
    assert created is False, "该被单飞挡下，而不是替换成立即执行的新请求"
    assert req.not_before is not None


def test_deferred_request_is_reported_as_waiting(tmp_path):
    """页面要能回答"还有多久跑"：wait_s / due_at 由服务端算，前端不自己猜时钟。"""
    s = store(tmp_path)
    s.request(by="operator", defer_s=600)
    s.now = lambda: T0 + timedelta(seconds=100)
    st = s.state()
    assert st["pending"] is True and st["stale"] is False
    assert st["wait_s"] == pytest.approx(500)
    assert st["due_at"] == (T0 + timedelta(seconds=600)).isoformat(timespec="seconds")


def test_single_defer_cannot_beat_the_hold(tmp_path):
    """保持是强约束：单次 `defer_s` 只能更晚，不能更早。"""
    s = store(tmp_path)
    s.set_hold(until=T0 + timedelta(minutes=30), defer_s=DEFAULT_DEFER_S, reason="加湿")
    req, _ = s.request(by="scheduler", defer_s=0)
    want = T0 + timedelta(minutes=30, seconds=DEFAULT_DEFER_S)
    assert req.not_before == want.isoformat(timespec="seconds")


def test_hold_defers_every_incoming_request(tmp_path):
    """保持期内来的触发一律推到"保持结束 + 偏置"之后——调度器那条也照样中招。"""
    s = store(tmp_path)
    s.set_hold(until=T0 + timedelta(minutes=30), defer_s=1200, reason="加湿")
    req, _ = s.request(by="scheduler", reason="每 3 小时")
    want = T0 + timedelta(minutes=30, seconds=1200)
    assert req.not_before == want.isoformat(timespec="seconds")
    assert s.is_due(req) is False
    # 到点前 1 秒仍领不到，到点即领走
    s.now = lambda: want - timedelta(seconds=1)
    assert s.consume() is None
    s.now = lambda: want
    assert s.consume() is not None


def test_hold_pushes_a_request_that_is_already_queued(tmp_path):
    """"先触发、后设保持"是现场的常见顺序：已在排队的那条必须一起推后，
    否则它会照原时间撞上进湿段——正是这个功能要防的事。"""
    s = store(tmp_path)
    s.request(by="scheduler", reason="每 3 小时")      # 立即执行
    s.set_hold(until=T0 + timedelta(minutes=30), defer_s=1200, reason="加湿")
    want = T0 + timedelta(minutes=30, seconds=1200)
    assert s.current().not_before == want.isoformat(timespec="seconds")
    assert s.consume() is None
    s.now = lambda: want
    assert s.consume() is not None


def test_expired_hold_is_ignored(tmp_path):
    """`until` 过了就等于没有保持：过期文件不能一直压着巡检。"""
    s = store(tmp_path)
    s.set_hold(until=T0 + timedelta(minutes=30))
    s.now = lambda: T0 + timedelta(minutes=31)
    assert s.hold() is None
    req, _ = s.request(by="scheduler")
    assert req.not_before is None, "保持已过期，这一轮该立即跑"


def test_clear_hold_leaves_the_deferred_request_alone(tmp_path):
    """解除保持只影响之后来的触发；已偏置请求的 `not_before` 是它自己的事实。"""
    s = store(tmp_path)
    s.request(by="scheduler", defer_s=600)
    assert s.clear_hold() is False, "本来就没有保持"
    s.set_hold(until=T0 + timedelta(minutes=30), defer_s=1200)
    assert s.clear_hold() is True
    assert s.current().not_before is not None, "已偏置的请求不被解除保持改动"


def test_hold_rejects_nonsense(tmp_path):
    s = store(tmp_path)
    with pytest.raises(ValueError, match="defer_s"):
        s.set_hold(until=T0 + timedelta(minutes=5), defer_s=-1)
    with pytest.raises(ValueError, match="defer_s"):
        s.set_hold(until=T0 + timedelta(minutes=5), defer_s=MAX_DEFER_S + 1)
    with pytest.raises(ValueError, match="until"):
        s.set_hold(until=T0 - timedelta(minutes=1))
    assert s.hold() is None, "失败的设置不该留下文件"


def test_broken_hold_file_reads_as_no_hold(tmp_path):
    s = store(tmp_path)
    s._hold_path().parent.mkdir(parents=True, exist_ok=True)
    s._hold_path().write_text('{"until":', encoding="utf-8")
    assert s.hold() is None
    assert s.hold_summary() == {"active": False}


# ---------- HTTP 层：偏置与保持 ----------


def test_post_accepts_a_defer_offset(tmp_path):
    with client(tmp_path) as c:
        r = c.post("/api/patrol/run?by=operator&defer_s=600")
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] is True
    assert body["job"]["not_before"] is not None
    assert body["wait_s"] == pytest.approx(600)
    assert body["due_at"] == body["job"]["not_before"]


def test_post_rejects_an_absurd_defer(tmp_path):
    """偏置不是定时器：超过 1 小时的需求该去调调度器。"""
    with client(tmp_path) as c:
        assert c.post(f"/api/patrol/run?defer_s={MAX_DEFER_S + 1}").status_code == 422
        assert c.post("/api/patrol/run?defer_s=-1").status_code == 422


def test_put_hold_defers_the_next_trigger(tmp_path):
    with client(tmp_path) as c:
        r = c.put("/api/patrol/hold", json={"minutes": 30, "reason": "加湿"})
        assert r.status_code == 200 and r.json()["hold"]["active"] is True
        job = c.post("/api/patrol/run").json()
        got = c.get("/api/patrol/run").json()
    want = T0 + timedelta(minutes=30, seconds=DEFAULT_DEFER_S)
    assert job["job"]["not_before"] == want.isoformat(timespec="seconds")
    assert got["hold"]["reason"] == "加湿" and got["hold"]["active"] is True
    assert got["wait_s"] == pytest.approx(30 * 60 + DEFAULT_DEFER_S)


def test_put_hold_accepts_an_absolute_until(tmp_path):
    """脚本/排班给 ISO 时刻，比换算分钟数可靠。"""
    until = (T0 + timedelta(hours=2)).isoformat(timespec="seconds")
    with client(tmp_path) as c:
        r = c.put("/api/patrol/hold", json={"until": until, "defer_s": 60})
    assert r.status_code == 200
    assert r.json()["hold"]["until"] == until


@pytest.mark.parametrize("body", [
    {},                                     # minutes 和 until 都没给
    {"minutes": 0},                         # 非正
    {"minutes": -5},
    {"until": "不是时间"},
    {"minutes": 30, "defer_s": MAX_DEFER_S + 1},
])
def test_put_hold_rejects_bad_bodies(tmp_path, body):
    with client(tmp_path) as c:
        assert c.put("/api/patrol/hold", json=body).status_code == 422


def test_delete_hold_clears_it(tmp_path):
    with client(tmp_path) as c:
        c.put("/api/patrol/hold", json={"minutes": 30})
        r = c.delete("/api/patrol/hold")
        assert r.json()["cleared"] is True
        assert c.get("/api/patrol/run").json()["hold"]["active"] is False
        assert c.delete("/api/patrol/hold").json()["cleared"] is False


def test_status_carries_the_trigger_block(tmp_path):
    """偏置/保持跟着 status 走：页面 1 秒一拍已经在轮询它，不再多开一条轮询路径。"""
    with client(tmp_path) as c:
        c.put("/api/patrol/hold", json={"minutes": 20, "reason": "加湿"})
        c.post("/api/patrol/run?by=scheduler")
        st = c.get("/api/status").json()
    trig = st["trigger"]
    assert trig["pending"] is True and trig["hold"]["active"] is True
    assert trig["hold"]["reason"] == "加湿" and trig["wait_s"] > 0

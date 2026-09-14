"""触发"跑一轮"：投请求 / 去重 / 单飞，以及 console 上的两个端点。

调度器（算法工程的 APScheduler）每 3 小时打一次 `POST /api/patrol/run`，
它 `max_instances=1`、`misfire_grace_time=300s`，而我们一轮 11 分钟 ——
所以这个端点**必须立刻返回**，绝不能在里面等巡检跑完。这些用例钉的就是这条。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from deploy.console import ConsoleDeps, create_app
from deploy.patrol_trigger import STALE_AFTER_S, TriggerStore
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
                       trigger_dir=str(tmp_path / "trigger"), now=lambda: now)
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
        assert c.get("/api/patrol/run").json() == {
            "job": None, "pending": False, "age_s": None, "stale": False}
        c.post("/api/patrol/run")
        got = c.get("/api/patrol/run").json()
    assert got["pending"] is True and got["stale"] is False and got["age_s"] is not None


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

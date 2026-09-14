"""`deploy.console`：巡检台只读面。

测试重点不在"页面好看"，而在三条**安全与诚实**的性质：

1. 巡检进行中**绝不出现"已连控制器"**——单会话设备，第二个连接会踢掉守护那一轮；
2. 依赖坏掉时状态接口**仍然 200**（页面每秒轮询，一个坏文件不该让整页变错误）；
3. "正在跑"与"进程可能死了"必须分开报——否则页面会一直显示"巡检中"骗人。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from deploy.console import ConsoleDeps, create_app, patrol_state, room_state
from fastapi.testclient import TestClient

NOW = datetime(2026, 9, 14, 10, 0, 0)


def write_room(path: Path, entry: str) -> None:
    path.write_text(f'room_id: "611"\nentry_date: "{entry}"\nbatch_no: "mogu-100"\n',
                    encoding="utf-8")


def write_run(runs: Path, name: str, events: list[dict], *, age_s: float = 0.0) -> Path:
    runs.mkdir(parents=True, exist_ok=True)
    p = runs / name
    p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
                 encoding="utf-8")
    if age_s:
        import os
        import time
        stamp = time.time() - age_s
        os.utime(p, (stamp, stamp))
    return p


def make_client(tmp_path, *, entry="2026-09-04", transport=None) -> TestClient:
    room = tmp_path / "room.yaml"
    write_room(room, entry)
    stations = tmp_path / "stations.yaml"
    stations.write_text(
        "stations:\n"
        "  - {id: S101, box_id: B101, y: 187.1, z: -21.2, layer: 1, col: 1}\n"
        "  - {id: S102, box_id: B102, y: 561.5, z: -21.2, layer: 1, col: 2}\n",
        encoding="utf-8",
    )
    deps = ConsoleDeps(room_path=str(room), stations_path=str(stations),
                       outbox_path=str(tmp_path / "outbox.jsonl"),
                       runs_dir=str(tmp_path / "runs"),
                       transport=transport, now=lambda: NOW)
    return TestClient(create_app(deps))


# ---------- 门禁（现场第一疑问） ----------


def test_room_gate_open_when_inside_window(tmp_path):
    with make_client(tmp_path, entry="2026-09-04") as c:      # 第 10 天
        r = c.get("/api/room").json()
    assert r["ok"] and r["allowed"] is True
    assert r["day"] == 10 and r["room_id"] == "611"
    assert "第 10 天" in r["text"]


def test_room_gate_closed_when_expired(tmp_path):
    with make_client(tmp_path, entry="2026-03-16") as c:      # 第 182 天
        r = c.get("/api/room").json()
    assert r["allowed"] is False
    assert "超过第 25 天" in r["text"]


def test_missing_room_file_is_reported_not_crashed(tmp_path):
    deps_room = tmp_path / "nope.yaml"
    r = room_state(str(deps_room), now=NOW)
    assert r["ok"] is False and r["allowed"] is False
    assert "fail-closed" in r["text"]


# ---------- 巡检状态：从取证推，不靠连控制器 ----------


def test_no_runs_dir_reports_unknown_not_ok(tmp_path):
    st = patrol_state(str(tmp_path / "runs"))
    assert st["active"] is False and st["known"] is False


def test_finished_round_reports_result(tmp_path):
    write_run(tmp_path / "runs", "20260914-100000-1.jsonl", [
        {"event": "start", "ts": "2026-09-14T09:50:00", "stations": 60},
        {"event": "station", "index": 1, "total": 60, "station_id": "S101"},
        {"event": "station", "index": 60, "total": 60, "station_id": "S560"},
        {"event": "end", "ts": "2026-09-14T09:59:00", "status": "ok",
         "n_results": 60, "n_failures": 0, "aborted": False},
    ])
    st = patrol_state(str(tmp_path / "runs"))
    assert st["active"] is False and st["known"] is True
    assert st["status"] == "ok" and st["n_results"] == 60


def test_running_round_is_active_with_progress(tmp_path):
    write_run(tmp_path / "runs", "20260914-100000-2.jsonl", [
        {"event": "start", "ts": "2026-09-14T09:58:00", "stations": 60},
        {"event": "station", "index": 17, "total": 60, "station_id": "S105"},
    ])
    st = patrol_state(str(tmp_path / "runs"))
    assert st["active"] is True
    assert st["current_station"] == "S105" and st["station_index"] == 17


def test_stale_heartbeat_is_not_reported_as_running(tmp_path):
    """没 end 但心跳很旧 ⇒ 是"可能死了"，不是"正在跑"。这条不分开报，页面就会一直骗人。"""
    write_run(tmp_path / "runs", "20260914-090000-3.jsonl", [
        {"event": "start", "ts": "2026-09-14T08:00:00", "stations": 60},
        {"event": "station", "index": 3, "total": 60, "station_id": "S103"},
    ], age_s=3600)
    st = patrol_state(str(tmp_path / "runs"))
    assert st["active"] is False
    assert "可能" in st["reason"]


# ---------- 状态接口 ----------


def test_status_suppresses_controller_probe_while_patrolling(tmp_path):
    """巡检进行中：不许出现 connected=True —— 那是单会话设备的红线。"""
    write_run(tmp_path / "runs", "20260914-100000-4.jsonl", [
        {"event": "start", "ts": "2026-09-14T09:58:00", "stations": 60},
        {"event": "station", "index": 2, "total": 60, "station_id": "S102"},
    ])
    with make_client(tmp_path) as c:
        s = c.get("/api/status").json()
    assert s["patrol"]["active"] is True
    assert s["machine"]["state"] == "PATROLLING"
    assert s["machine"]["connected"] is False
    assert s["machine"]["probe_suppressed"] is True
    # 位置用站位目标值顶上，但必须标明来源不是控制器
    assert s["machine"]["pos_source"] == "station"
    assert s["machine"]["real_pos"] == [561.5, -21.2]


def test_status_stays_200_with_broken_dependencies(tmp_path):
    """room.yaml 坏 + 没有 runs + 站位表不存在 ⇒ 仍然 200，字段各自报错。"""
    deps = ConsoleDeps(room_path=str(tmp_path / "missing.yaml"),
                       stations_path=str(tmp_path / "missing_stations.yaml"),
                       outbox_path=str(tmp_path / "outbox.jsonl"),
                       runs_dir=str(tmp_path / "runs"), now=lambda: NOW)
    with TestClient(create_app(deps)) as c:
        r = c.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["room"]["ok"] is False
    assert body["patrol"]["known"] is False
    assert body["machine"]["state"] == "UNKNOWN"


def test_stations_endpoint_carries_grid_and_targets(tmp_path):
    with make_client(tmp_path) as c:
        got = c.get("/api/stations").json()
    assert got["ok"] is True
    assert got["grid"]["cols"] == 12 and got["grid"]["layers"] == 5
    assert len(got["stations"]) == 2
    assert got["stations"][0]["target_y"] == got["stations"][0]["y"]


def test_grid_endpoint(tmp_path):
    with make_client(tmp_path) as c:
        g = c.get("/api/grid").json()
    assert g["y_min"] == 0.0 and g["y_max"] == 4492.0
    assert g["z_min"] == -212.0 and g["z_max"] == 0.0


def test_page_is_served(tmp_path):
    with make_client(tmp_path) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "蘑菇房巡检台" in r.text


def test_healthz(tmp_path):
    with make_client(tmp_path) as c:
        assert c.get("/healthz").json() == {"ok": True}


# ---------- 历史图像：本地 + prod 合并 ----------


def test_images_merge_local_and_prod(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    outbox.write_text(json.dumps({"kind": "image_index", "ts": "2026-09-14T09:30:00",
                                  "station_id": "S101", "ok": True,
                                  "object_name": "20260914/B101_S101_top45_093000"}) + "\n",
                      encoding="utf-8")

    def transport(url, *, params=None, body=None):
        assert "/images" in url
        return [{"ts": "2026-09-13T10:00:00", "station_id": "S101", "ok": 1,
                 "object_name": "20260913/B101_S101_top45_100000", "cloud_url": "http://m/x.jpg"}]

    with make_client(tmp_path, transport=transport) as c:
        got = c.get("/api/images?station_id=S101").json()
    assert got["n_local"] == 1 and got["n_prod"] == 1
    # 倒序：本地的更新，排在前
    assert [r["source"] for r in got["rows"]] == ["local", "prod"]


def test_images_survive_prod_failure(tmp_path):
    def bad_transport(url, *, params=None, body=None):
        raise ConnectionError("prod 不通")

    with make_client(tmp_path, transport=bad_transport) as c:
        got = c.get("/api/images").json()
    assert got["ok"] is False and "prod 不通" in got["prod_error"]


def test_images_filter_by_station(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    rows = [{"kind": "image_index", "ts": "2026-09-14T09:30:00", "station_id": sid, "ok": True}
            for sid in ("S101", "S102")]
    outbox.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with make_client(tmp_path) as c:
        got = c.get("/api/images?station_id=S102").json()
    assert [r["station_id"] for r in got["rows"]] == ["S102"]

"""生长查询接口：`/growth` 与 `/growth/room`。

这两个端点是把 `analysis.growth` 的判词交给页面用的那一层，重点在两件事：
1. 判词、文案、阈值口径原样透出（页面不该自己拼"长了多少"）；
2. **框清单一个都不能少**——现场最怕"列表里少了几个框"，那分不清是没长还是没拍。
"""

from __future__ import annotations

from analysis.api import create_app
from fastapi.testclient import TestClient


def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "api.db")))


def measurement(ts, box_id, mean_len, *, n=6, cap=None) -> dict:
    return {"kind": "measurement", "ts": ts, "box_id": box_id, "n": n,
            "mean_len_mm": mean_len, "mean_cap_mm": cap, "p10_len": None,
            "p90_len": None, "quality": ""}


def test_growth_series_returns_per_point_verdicts(tmp_path):
    rows = [measurement("2026-09-13T08:00:00", "B101", 40.0),
            measurement("2026-09-13T14:00:00", "B101", 43.0),
            measurement("2026-09-13T20:00:00", "B101", 46.0)]
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": rows})
        got = c.get("/growth?box_id=B101").json()
    assert got["box_id"] == "B101"
    assert [p["verdict"] for p in got["points"]] == ["no_prev", "growing", "growing"]
    assert got["latest"]["verdict_text"] == "在长"
    assert got["latest"]["rate_len_mm_per_h"] == 0.5


def test_growth_unknown_box_returns_empty_not_500(tmp_path):
    with client(tmp_path) as c:
        got = c.get("/growth?box_id=B999")
    assert got.status_code == 200
    assert got.json() == {"box_id": "B999", "points": [], "latest": None}


def test_growth_room_covers_every_box_seen_in_the_image_index(tmp_path):
    """图像索引里出现过、但还没测量值的框，要以 `no_prev` 出现在列表里。"""
    images = [{"kind": "image_index", "ts": "2026-09-13T20:25:42", "ok": True,
               "box_id": box, "station_id": f"S10{i}", "angle_profile": "top45",
               "room_id": "611", "entry_date": "2026-03-16"}
              for i, box in enumerate(("B101", "B102"), start=1)]
    measures = [measurement("2026-09-13T08:00:00", "B101", 40.0),
                measurement("2026-09-13T20:00:00", "B101", 40.2)]
    with client(tmp_path) as c:
        c.post("/ingest", json={"rows": images + measures})
        got = c.get("/growth/room?room_id=611").json()
    assert got["room_id"] == "611"
    assert set(got["boxes"]) == {"B101", "B102"}
    assert got["boxes"]["B101"]["verdict"] == "stalled"
    assert got["boxes"]["B102"]["verdict"] == "no_prev"
    assert got["counts"] == {"stalled": 1, "no_prev": 1}


def test_growth_room_accepts_an_explicit_box_list(tmp_path):
    with client(tmp_path) as c:
        got = c.get("/growth/room?boxes=B101,B102,B103").json()
    assert got["n_boxes"] == 3
    assert all(v["verdict"] == "no_prev" for v in got["boxes"].values())

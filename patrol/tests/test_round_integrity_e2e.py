"""票 13/06 的**离线端到端彩排**：一轮含重复帧 → outbox 行 → analysis 入库 → 读回 → 复验全绿。

为什么要有它：21:27 那轮是**正常轮**，`dup_groups` 的"写读成对"在生产上**没被触发**
（正常轮该列是 NULL）。这条彩排在本地把**重复路径**整条走一遍：
`PatrolDaemon` 写行 → `analysis.db` 建库入库 → `fetch_rounds`/`fetch_images` 读回
→ 复验工具 `verify()` 六条全 PASS（含 `dup_groups` 读回来是 **list**）。

不连生产、不碰线上库、不改任何共享状态：库是 `tmp_path` 下的一个 sqlite 文件。
（跨域说明：这里只**读用** `analysis.db` 的建表/入库/取数函数，不改 analysis 侧任何文件。）
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from patrol.daemon import PatrolDaemon            # noqa: E402
from patrol.room import RoomState                 # noqa: E402
from patrol.stations import Station               # noqa: E402
from patrol.store import JsonlStore               # noqa: E402
from patrol.sync import SyncClient                # noqa: E402
from verify_round_integrity import verify         # noqa: E402

from fmc_fakes import commissioned_client         # noqa: E402

_ENTRY = date(2026, 1, 1)
ALLOWED_ROOM = RoomState(room_id="611", entry_date=_ENTRY)


def _allowed_now() -> datetime:
    return datetime(2026, 1, 11, 9, 0, 0)


class ReplayCapture:
    """票 06 的事故形状：前 13 站各不相同，之后 35 站**同一份字节**（服务侧如实上报）。"""

    def __init__(self, *, normal: int = 13):
        self.normal, self.i = normal, 0

    def capture(self, *, ip, user=None, pwd=None, filename):
        from patrol.capture_client import CaptureResult

        self.i += 1
        if self.i <= self.normal:
            sha, same, streak = f"{self.i:064d}", False, 0
        else:
            sha, same, streak = "a" * 64, True, self.i - self.normal
        return CaptureResult(
            object_name=f"{filename}.jpg", file_path=None,
            cloud_url=f"http://minio/mogu/{filename}.jpg",
            raw={"success": True, "filename": f"{filename}.jpg",
                 "image_sha256": sha, "same_as_previous": same,
                 "duplicate_streak": streak})


def test_duplicate_round_survives_the_whole_chain_into_the_db(tmp_path):
    """一轮 13 正常 + 35 重复：帧全入库、轮账对得上、`dup_groups` 读回来是 list。"""
    from analysis.db import (connect, fetch_images, fetch_rounds, insert_images,
                             insert_rounds, migrate)

    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    sent: list = []
    stations = [Station(id=f"S{i:03d}", box_id=f"B{i:03d}", y=float(i), z=0.0)
                for i in range(1, 49)]
    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM, now=_allowed_now,
        open_fmc=lambda: commissioned_client(), stations=stations,
        capture_client=ReplayCapture(), store=store,
        sync=SyncClient(transport=lambda url, body=None: sent.append(body["rows"])),
        log=lambda _m: None,
    )
    assert daemon.run_cycle() == "ok"          # 重复帧不中止整轮

    rows = [r for batch in sent for r in batch]
    rounds = [r for r in rows if r["kind"] == "round"]
    images = [r for r in rows if r["kind"] == "image_index"]
    assert len(images) == 48 and len(rounds) == 1

    conn = connect(str(tmp_path / "analysis.db"))
    migrate(conn)
    insert_images(conn, images)
    insert_rounds(conn, rounds)

    (got_round,) = fetch_rounds(conn)
    got_images = fetch_images(conn)

    # 轮级：账对得上，且 `suspect_duplicate` / `dup_groups` 落库
    assert got_round["n_results"] == 13 and got_round["n_duplicate"] == 35
    assert got_round["n_frames_recorded"] == 48
    assert bool(got_round["suspect_duplicate"]) is True
    assert isinstance(got_round["dup_groups"], list), "写读成对：读回来必须是 list"
    (group,) = got_round["dup_groups"]
    assert group["n"] == 35 and len(group["stations"]) == 35

    # 帧级：48 行全在、35 行带标记、指纹保留
    assert len(got_images) == 48
    assert sum(1 for r in got_images if r["frame_dup"]) == 35
    assert all(r["content_sha256"] for r in got_images)

    # 复验工具吃"库里拉回来的行"（没有 kind），六条全 PASS
    checks = verify([got_round, *got_images])
    assert all(ok for _n, ok, _d in checks), checks
    assert dict((n, ok) for n, ok, _ in checks)["dup_groups 读回来是列表（不是字符串）"] is True


def test_analysis_rounds_api_returns_the_columns_the_page_reads(tmp_path):
    """库读出来的五列形状：页面按"布尔按布尔、`dup_groups` 按对象"渲染的前提。"""
    from analysis.db import (connect, fetch_rounds, insert_rounds, migrate)

    conn = connect(str(tmp_path / "analysis.db"))
    migrate(conn)
    insert_rounds(conn, [{
        "kind": "round", "round_id": "r1", "ts": "2026-10-13T21:27:52", "ok": True,
        "n_results": 13, "n_failures": 0, "aborted": False, "room_id": "611",
        "suspect_duplicate": True, "n_duplicate": 35, "n_frames_recorded": 48,
        "dup_groups": [{"sha256_12": "a" * 12, "n": 35, "stations": ["S211", "S401"]}],
        "content_check_error": None,
    }])
    (row,) = fetch_rounds(conn)
    assert row["suspect_duplicate"] in (1, True)
    assert row["n_duplicate"] == 35 and row["n_frames_recorded"] == 48
    assert isinstance(row["dup_groups"], list) and row["dup_groups"][0]["sha256_12"] == "a" * 12
    assert row["content_check_error"] is None


if __name__ == "__main__":                     # pragma: no cover - 手工跑
    raise SystemExit(pytest.main([__file__, "-q"]))

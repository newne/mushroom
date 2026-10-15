"""票 13/06 的**离线端到端彩排**：一轮含重复帧 → outbox 行 → analysis 入库 → 读回 → 复验全绿。

为什么要有它：21:27 那轮是**正常轮**，`dup_groups` 的"写读成对"在生产上**没被触发**
（正常轮该列是 NULL）。这条彩排在本地把**重复路径**整条走一遍：
`PatrolDaemon` 写行 → `analysis.store` 入库 → `fetch_rounds`/`fetch_images` 读回
→ 复验工具 `verify()` 六条全 PASS（含 `dup_groups` 读回来是 **list**）。

**存储是 PG 的独立测试 schema**（票 28 contract：SQLite 已删除）。测试专用连接串是
`ANALYSIS_TEST_DSN` / `ANALYSIS_TEST_DSN_FILE`（**不是**生产用的 `PGSQL_DSN*`）；
没设就整组 skip —— **不回退临时文件库**。每个用例一个 `analysis_test_patrol_<随机>`
schema，跑完 `DROP SCHEMA … CASCADE` 并断言不存在；不碰 public、不碰遗留表、不碰 TDengine。
（跨域说明：这里只**用** `analysis.store` 的公开读写函数，不改 analysis 侧任何文件。）
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from analysis import store                        # noqa: E402
from patrol.daemon import PatrolDaemon            # noqa: E402
from patrol.room import RoomState                 # noqa: E402
from patrol.stations import Station               # noqa: E402
from patrol.store import JsonlStore               # noqa: E402
from patrol.sync import SyncClient                # noqa: E402
from verify_round_integrity import verify         # noqa: E402

from fmc_fakes import commissioned_client         # noqa: E402

#: 测试专用连接串的两个变量（与 `analysis/tests/pg_harness.py` 同一口径，故意不复用 PGSQL_DSN*）。
DSN_ENV = "ANALYSIS_TEST_DSN"
DSN_FILE_ENV = "ANALYSIS_TEST_DSN_FILE"


def _test_dsn() -> str | None:
    """没有测试连接串 ⇒ 返回 `None`（用例整组 skip）。"""
    import os

    dsn = (os.environ.get(DSN_ENV) or "").strip()
    if dsn:
        return dsn
    path = (os.environ.get(DSN_FILE_ENV) or "").strip()
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


@pytest.fixture
def conn():
    """本用例独占的 PG schema（与 `analysis/tests/conftest.py` 的 `pg` 同口径）。"""
    dsn = _test_dsn()
    if dsn is None:
        pytest.skip(
            f"没有 {DSN_ENV} / {DSN_FILE_ENV}：这条链路只在 PostgreSQL 上跑"
            "（票 28 contract 已删 SQLite，不回退临时文件库）"
        )
    import psycopg
    from uuid import uuid4

    schema = f"analysis_test_patrol_{uuid4().hex[:8]}"
    connection = store.connect(dsn=dsn, schema=schema)
    try:
        yield connection
    finally:
        connection.close()
        with psycopg.connect(dsn, autocommit=True) as raw, raw.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cur.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (schema,)
            )
            assert cur.fetchone() is None, f"测试 schema {schema} 没删干净"


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


def test_duplicate_round_survives_the_whole_chain_into_the_db(tmp_path, conn):
    """一轮 13 正常 + 35 重复：帧全入库、轮账对得上、`dup_groups` 读回来是 list。"""
    outbox = JsonlStore(str(tmp_path / "outbox.jsonl"))
    sent: list = []
    stations = [Station(id=f"S{i:03d}", box_id=f"B{i:03d}", y=float(i), z=0.0)
                for i in range(1, 49)]
    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM, now=_allowed_now,
        open_fmc=lambda: commissioned_client(), stations=stations,
        capture_client=ReplayCapture(), store=outbox,
        sync=SyncClient(transport=lambda url, body=None: sent.append(body["rows"])),
        log=lambda _m: None,
    )
    assert daemon.run_cycle() == "ok"          # 重复帧不中止整轮

    rows = [r for batch in sent for r in batch]
    rounds = [r for r in rows if r["kind"] == "round"]
    images = [r for r in rows if r["kind"] == "image_index"]
    assert len(images) == 48 and len(rounds) == 1

    store.insert_images(conn, images)
    store.insert_rounds(conn, rounds)

    (got_round,) = store.fetch_rounds(conn)
    got_images = store.fetch_images(conn)

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


def test_analysis_rounds_api_returns_the_columns_the_page_reads(conn):
    """库读出来的五列形状：页面按"布尔按布尔、`dup_groups` 按对象"渲染的前提。"""
    store.insert_rounds(conn, [{
        "kind": "round", "round_id": "r1", "ts": "2026-10-13T21:27:52", "ok": True,
        "n_results": 13, "n_failures": 0, "aborted": False, "room_id": "611",
        "suspect_duplicate": True, "n_duplicate": 35, "n_frames_recorded": 48,
        "dup_groups": [{"sha256_12": "a" * 12, "n": 35, "stations": ["S211", "S401"]}],
        "content_check_error": None,
    }])
    (row,) = store.fetch_rounds(conn)
    assert row["suspect_duplicate"] in (1, True)
    assert row["n_duplicate"] == 35 and row["n_frames_recorded"] == 48
    assert isinstance(row["dup_groups"], list) and row["dup_groups"][0]["sha256_12"] == "a" * 12
    assert row["content_check_error"] is None


if __name__ == "__main__":                     # pragma: no cover - 手工跑
    raise SystemExit(pytest.main([__file__, "-q"]))

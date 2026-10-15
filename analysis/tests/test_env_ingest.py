import pytest

from analysis import store
from analysis.env.adapters import CsvEnvSource, HttpEnvSource
from analysis.env.ingest import run_once

CSV_HEADER = "ts,zone,temp_c,rh_pct,co2_ppm,lamp_state\n"


@pytest.fixture
def csv_dir(tmp_path):
    d = tmp_path / "env_csv"
    d.mkdir()
    (d / "a.csv").write_text(
        CSV_HEADER
        + "2026-08-30T08:00:00,room1,23.5,85.0,800,120\n"
        + "2026-08-30T09:00:00,room1,24.0,88.0,900,150\n"
        + "2026-08-30T09:00:00,room2,22.0,80.0,700,\n",
        encoding="utf-8",
    )
    return d


def read_env(conn):
    """读回 `(ts, zone, temp_c)` 三元组（经存储层，不写裸 SQL）。"""
    return [(r["ts"], r["zone"], r["temp_c"]) for r in store.fetch_environment(conn)]


def test_csv_ingest_and_dedupe(csv_dir, pg):
    conn = pg
    source = CsvEnvSource(str(csv_dir))

    cursor = run_once(conn, source)
    assert cursor == "2026-08-30T09:00:00"
    assert len(read_env(conn)) == 3

    # 同一批数据重复拉取：主键去重，不产生重复行
    run_once(conn, source)
    assert len(read_env(conn)) == 3

    # 游标之后只拉新增
    rows = source.fetch_since("2026-08-30T08:00:00")
    assert [r.ts for r in rows] == ["2026-08-30T09:00:00", "2026-08-30T09:00:00"]


def test_csv_source_skips_empty_ts(csv_dir):
    (csv_dir / "bad.csv").write_text(CSV_HEADER + ",room1,20.0\n", encoding="utf-8")
    rows = CsvEnvSource(str(csv_dir)).fetch_since(None)
    assert rows
    assert all(r.ts for r in rows)


def test_http_source_parse():
    payload = {
        "rows": [
            {"ts": "2026-08-30T10:00:00", "zone": "room1", "temp_c": "25.5",
             "rh_pct": "", "co2_ppm": None, "lamp_state": 2},
        ]
    }
    rows = HttpEnvSource._parse(payload)
    assert len(rows) == 1
    assert rows[0].temp_c == 25.5
    assert rows[0].rh_pct is None
    assert rows[0].lamp_state == 2.0


def test_http_source_parse_bare_list():
    rows = HttpEnvSource._parse([{"ts": "t1", "zone": "z", "temp_c": 1}])
    assert rows[0].temp_c == 1.0


def test_http_source_requires_transport_until_site_confirmed():
    src = HttpEnvSource("http://env.example.com/api")
    with pytest.raises(NotImplementedError, match="现场确认"):
        src.fetch_since(None)


def test_http_source_with_injected_transport():
    captured = {}

    def transport(url, *, params=None, body=None):
        captured["url"] = url
        return [{"ts": "t1", "zone": "z", "temp_c": 19.5}]

    src = HttpEnvSource("http://env.example.com/api", transport=transport)
    rows = src.fetch_since("2026-08-30T00:00:00")
    assert rows[0].temp_c == 19.5
    assert captured["url"] == "http://env.example.com/api?since=2026-08-30T00:00:00"


def test_upsert_environment_overwrites(pg):
    conn = pg
    store.upsert_environment(conn, [
        {"ts": "t1", "zone": "z", "temp_c": 20.0, "rh_pct": None,
         "co2_ppm": None, "lamp_state": None},
    ])
    store.upsert_environment(conn, [
        {"ts": "t1", "zone": "z", "temp_c": 21.0, "rh_pct": None,
         "co2_ppm": None, "lamp_state": None},
    ])
    assert read_env(conn) == [("t1", "z", 21.0)]

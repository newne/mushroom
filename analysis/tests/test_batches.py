"""批次台账：解析、窗口切分与批次对比（2026-10-12）。"""

import pytest

from analysis.batches import BatchFetchError, fetch_batches, parse_batches, window_of

TSV = """\
101\t611\t2026-08-16 09:00:00
107\t611\t2026-09-30 08:00:00
108\t612\t2026-09-30 08:00:00
"""


def test_parse_batches_shapes_rows():
    rows = parse_batches(TSV)
    assert [r["room_id"] for r in rows] == ["611", "611", "612"]
    assert rows[1]["batch_no"] == "mogu-107"
    assert rows[1]["entry_date"] == "2026-09-30"


def test_parse_batches_rejects_wrong_field_count():
    with pytest.raises(BatchFetchError, match="字段数"):
        parse_batches("101\t611\n")


def test_fetch_batches_uses_docker_exec_with_container_password():
    seen = {}

    def runner(argv):
        seen["argv"] = argv
        return TSV

    rows = fetch_batches(runner=runner)
    assert len(rows) == 3
    joined = " ".join(seen["argv"])
    assert "docker exec tools_mysql" in joined
    assert "MYSQL_ROOT_PASSWORD" in joined      # 口令只在容器 env 里，不落我们的配置
    assert "mo_gu_batch" in joined


def test_window_of_uses_next_batch_as_upper_bound():
    rows = parse_batches(TSV)
    windows = window_of(rows, room="611", limit=2)
    assert windows[0]["batch_no"] == "mogu-107" and windows[0]["until"] is None
    assert windows[1]["batch_no"] == "mogu-101" and windows[1]["until"] == "2026-09-30"

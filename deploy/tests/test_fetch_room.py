"""``deploy.fetch_room``：把入库台账变成 room.yaml。

重点在**契约**：写出来的 YAML 必须能被 ``patrol.room`` 读回（消费端就是它），
所以每个落盘用例都拿 ``load_room_state`` 复读一遍。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest
from deploy.fetch_room import (
    BatchRow,
    FetchError,
    main,
    parse_tsv,
    read_tsv,
    render_room_yaml,
    select_batch,
    write_room_yaml,
)
from patrol.room import RoomStateError, load_room_state

TODAY = date(2026, 9, 13)

# 现场真实取数的样子（`mysql -N -B`，制表符分隔，无表头）——2026-09-13 实测值。
REAL = (
    "102\t612\t2026-04-05\t5\t9792\tTD1_Q2MDINFO01\t2026-04-09 18:00:00\n"
    "101\t7\t2026-03-22\t19\t9792\tTD1_Q3MDINFO01\t2026-04-09 18:00:00\n"
    "100\t611\t2026-03-16\t25\t9792\tTD1_Q1MDINFO01\t2026-04-09 18:00:00\n"
    "99\t612\t2026-03-09\t27\t9792\tTD1_Q2MDINFO01\t2026-04-06 19:00:00\n"
)


def test_parse_real_dump() -> None:
    rows = parse_tsv(REAL)
    assert len(rows) == 4
    first = rows[0]
    assert first.batch_id == "102"
    assert first.room_id == "612"
    assert first.entry_date == date(2026, 4, 5)
    assert first.system_day_num == 5
    assert first.packages == 9792
    assert first.info_code == "TD1_Q2MDINFO01"
    assert first.batch_no == "mogu-102"


def test_parse_ignores_blank_lines_and_crlf() -> None:
    rows = parse_tsv("\r\n" + REAL.replace("\n", "\r\n") + "\r\n")
    assert len(rows) == 4


def test_parse_accepts_datetime_in_time() -> None:
    rows = parse_tsv("1\t611\t2026-03-16 08:30:00\t1\t10\tTD1_Q1MDINFO01\t2026-03-16 08:30:00\n")
    assert rows[0].entry_date == date(2026, 3, 16)


def test_parse_treats_null_as_missing() -> None:
    rows = parse_tsv("1\t611\t2026-03-16\tNULL\tNULL\tNULL\tNULL\n")
    assert rows[0].system_day_num is None
    assert rows[0].packages is None
    assert rows[0].info_code == ""
    assert rows[0].updated_at == ""


def test_parse_rejects_wrong_field_count() -> None:
    # 少一列 ⇒ 后面所有列都会错位（in_num 被当成 info_code 之类）。必须报错，不能跳过。
    with pytest.raises(FetchError, match="字段数"):
        parse_tsv("1\t611\t2026-03-16\t5\t9792\n")


def test_parse_rejects_bad_date() -> None:
    with pytest.raises(FetchError, match="in_time"):
        parse_tsv("1\t611\t16/03/2026\t5\t9792\tX\t2026-03-16 00:00:00\n")


def test_parse_rejects_non_integer_day_num() -> None:
    with pytest.raises(FetchError, match="in_day_num"):
        parse_tsv("1\t611\t2026-03-16\t五天\t9792\tX\t2026-03-16 00:00:00\n")


def test_parse_rejects_missing_room() -> None:
    with pytest.raises(FetchError, match="code_num"):
        parse_tsv("1\t\t2026-03-16\t5\t9792\tX\t2026-03-16 00:00:00\n")


def test_select_picks_latest_batch_for_room() -> None:
    row = select_batch(parse_tsv(REAL), "612", today=TODAY)
    assert row.entry_date == date(2026, 4, 5)      # 不是 2026-03-09
    assert row.batch_id == "102"


def test_select_is_stable_on_same_date() -> None:
    rows = parse_tsv(
        "7\t611\t2026-03-16\t1\t1\tX\t2026-03-16 00:00:00\n"
        "9\t611\t2026-03-16\t1\t1\tX\t2026-03-16 00:00:00\n"
    )
    assert select_batch(rows, "611", today=TODAY).batch_id == "9"


def test_select_rejects_unknown_room() -> None:
    with pytest.raises(FetchError, match="没有库房"):
        select_batch(parse_tsv(REAL), "999", today=TODAY)


def test_select_rejects_future_entry_date() -> None:
    rows = parse_tsv("1\t611\t2026-09-20\t1\t1\tX\t2026-09-20 00:00:00\n")
    with pytest.raises(FetchError, match="之后"):
        select_batch(rows, "611", today=TODAY)


def test_render_round_trips_through_patrol_room(tmp_path: Path) -> None:
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    text = render_room_yaml(row, today=TODAY, now=datetime(2026, 9, 13, 21, 30, 0))
    p = tmp_path / "room.yaml"
    p.write_text(text, encoding="utf-8")
    state = load_room_state(p)                     # consumer 是 patrol.room
    assert state.room_id == "611"
    assert state.entry_date == date(2026, 3, 16)
    assert state.packages == 9792
    assert state.batch_no == "mogu-100"
    assert "mo_gu_batch id=100" in state.source


def test_render_carries_both_day_conventions() -> None:
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    text = render_room_yaml(row, today=TODAY)
    # 2026-03-16 → 2026-09-13 是第 181 天，生产系统口径第 182 天；两者都要能看见
    assert "第 181 天" in text
    assert "第 182 天" in text
    assert "不巡检" in text


def test_render_in_window_says_admitted() -> None:
    rows = parse_tsv("1\t611\t2026-09-11\t1\t9792\tTD1_Q1MDINFO01\t2026-09-11 00:00:00\n")
    row = select_batch(rows, "611", today=TODAY)
    text = render_room_yaml(row, today=TODAY)
    assert "第 2 天" in text
    assert "准入" in text


def test_write_creates_new_file(tmp_path: Path) -> None:
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    out = tmp_path / "room.yaml"
    note = write_room_yaml(out, render_room_yaml(row, today=TODAY))
    assert "新建" in note
    assert load_room_state(out).entry_date == date(2026, 3, 16)


def test_write_reports_unchanged_date(tmp_path: Path) -> None:
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    out = tmp_path / "room.yaml"
    text = render_room_yaml(row, today=TODAY)
    write_room_yaml(out, text)
    assert "未变" in write_room_yaml(out, text)


def test_write_reports_batch_switch(tmp_path: Path) -> None:
    out = tmp_path / "room.yaml"
    old = select_batch(parse_tsv(REAL), "612", today=TODAY)          # 2026-04-05
    write_room_yaml(out, render_room_yaml(old, today=TODAY))
    new = BatchRow(batch_id="200", room_id="612", entry_date=date(2026, 9, 10))
    note = write_room_yaml(out, render_room_yaml(new, today=TODAY))
    assert "2026-04-05 → 2026-09-10" in note
    assert load_room_state(out).entry_date == date(2026, 9, 10)


def test_write_leaves_no_temp_file(tmp_path: Path) -> None:
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    write_room_yaml(tmp_path / "room.yaml", render_room_yaml(row, today=TODAY))
    assert [p.name for p in tmp_path.iterdir()] == ["room.yaml"]


def test_write_refuses_broken_text(tmp_path: Path) -> None:
    # 手工造一份 entry_date 非法的文本：必须在落盘前被 patrol.room 的校验挡下
    with pytest.raises(FetchError, match="校验"):
        write_room_yaml(tmp_path / "room.yaml", "room_id: '611'\nentry_date: 20260913\n")
    assert not (tmp_path / "room.yaml").exists()


def test_write_replaces_test_standin(tmp_path: Path) -> None:
    """现场 room.yaml 曾是手填的测试替身——脚本必须能把它换掉，且说明变了什么。"""
    out = tmp_path / "room.yaml"
    out.write_text('room_id: "611"\nentry_date: "2026-09-11"\nbatch_no: "TEST"\n',
                   encoding="utf-8")
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    note = write_room_yaml(out, render_room_yaml(row, today=TODAY))
    assert "2026-09-11 → 2026-03-16" in note


def test_read_tsv_from_file_and_stdin(tmp_path: Path, monkeypatch) -> None:
    p = tmp_path / "dump.tsv"
    p.write_text(REAL, encoding="utf-8")
    assert len(parse_tsv(read_tsv(str(p)))) == 4

    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(REAL))
    assert len(parse_tsv(read_tsv("-"))) == 4


def test_read_tsv_reports_missing_file() -> None:
    with pytest.raises(FetchError, match="读不到数据源"):
        read_tsv("D:/definitely/not/here.tsv")


def test_main_prints_only_by_default(tmp_path: Path, capsys) -> None:
    dump = tmp_path / "dump.tsv"
    dump.write_text(REAL, encoding="utf-8")
    out = tmp_path / "room.yaml"
    rc = main(["--room", "611", "--from", str(dump), "--out", str(out),
               "--today", "2026-09-13", "--print"])
    assert rc == 0
    assert not out.exists()
    printed = capsys.readouterr().out
    assert "entry_date: 2026-03-16" in printed
    assert "本轮不巡检" in printed


def test_main_writes_file(tmp_path: Path, capsys) -> None:
    dump = tmp_path / "dump.tsv"
    dump.write_text("1\t611\t2026-09-11\t1\t9792\tTD1_Q1MDINFO01\t2026-09-11 00:00:00\n",
                    encoding="utf-8")
    out = tmp_path / "room.yaml"
    rc = main(["--room", "611", "--from", str(dump), "--out", str(out),
               "--today", "2026-09-13"])
    assert rc == 0
    assert load_room_state(out).entry_date == date(2026, 9, 11)
    assert "本轮会巡检" in capsys.readouterr().out


def test_main_fails_closed_on_unknown_room(tmp_path: Path, capsys) -> None:
    dump = tmp_path / "dump.tsv"
    dump.write_text(REAL, encoding="utf-8")
    out = tmp_path / "room.yaml"
    rc = main(["--room", "999", "--from", str(dump), "--out", str(out),
               "--today", "2026-09-13"])
    assert rc == 2
    assert not out.exists()                     # 取不到数 ⇒ 一个字节都不写
    assert "没有库房" in capsys.readouterr().err


def test_broken_old_file_is_replaced_not_fatal(tmp_path: Path) -> None:
    """旧文件是占位（entry_date 空）时必须能直接覆盖——现场第一次接入就是这种状态。"""
    out = tmp_path / "room.yaml"
    out.write_text('room_id: "unset"\nentry_date: ""\n', encoding="utf-8")
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    note = write_room_yaml(out, render_room_yaml(row, today=TODAY))
    assert "旧文件不可用" in note
    assert load_room_state(out).entry_date == date(2026, 3, 16)


def test_missing_file_is_not_replaced_silently(tmp_path: Path) -> None:
    """反向确认：文件缺失时报错而不是当作"可以随便写"。"""
    with pytest.raises(RoomStateError):
        load_room_state(tmp_path / "nope.yaml")

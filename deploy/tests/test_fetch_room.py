"""``deploy.fetch_room``：把批次台账变成 room.yaml。

两条主线：

1. **契约**：写出来的 YAML 必须能被 ``patrol.room`` 读回（消费端就是它），
   所以每个落盘用例都拿 ``load_room_state`` 复读一遍；
2. **取数源**（用户 2026-10-14 裁定 + Chief 裁定 A′）：走 pgsql，**复用权威源**
   ``utils.batch_yield_service.get_batch_ranges``（``(room_id, in_date)`` 口径唯一），
   **不自己写 SQL**；旧的"借业务库容器 + root 口令"那条路必须**在源码里都不存在**
   （见文件末尾的守门用例）。"无 mysql、无 docker"不是口号：注入 runner / 注入 session
   的用例连数据库进程都不需要。
"""

from __future__ import annotations

import sys
import types
from datetime import date, datetime
from pathlib import Path

import pytest
from deploy.fetch_room import (
    BatchRow,
    FetchError,
    fetch_ledger_rows,
    ledger_rows_via_service,
    load_room_state_text,
    main,
    parse_tsv,
    read_tsv,
    render_room_yaml,
    rows_to_batch_rows,
    select_batch,
    write_room_yaml,
)
from patrol.room import RoomStateError, load_room_state

TODAY = date(2026, 9, 13)

# 离线台账的样子（`--from`，两列；演练/无库环境用）。现场真值来自权威取数源。
REAL = (
    "612\t2026-04-05\n"
    "7\t2026-03-22\n"
    "611\t2026-03-16\n"
    "612\t2026-03-09\n"
)


def install_fake_service(monkeypatch, items, seen: list | None = None):
    """把权威取数源换成假的：证明**调用点**对、且下游不依赖任何真实数据库。"""
    module = types.ModuleType("utils.batch_yield_service")

    def get_batch_ranges(session, room_ids=None, **_kw):
        if seen is not None:
            seen.append((session, list(room_ids or [])))
        return items

    module.get_batch_ranges = get_batch_ranges
    monkeypatch.setitem(sys.modules, "utils.batch_yield_service", module)
    return module


# ---------- 离线 TSV（旁路，不是取数通道） ----------


def test_parse_offline_tsv() -> None:
    rows = parse_tsv(REAL)
    assert len(rows) == 4
    assert rows[0].room_id == "612"
    assert rows[0].entry_date == date(2026, 4, 5)
    assert rows[0].batch_id == "612|2026-04-05"
    assert rows[0].batch_no == "612_20260405"
    assert rows[0].packages is None                 # 权威源没有这个量 ⇒ 不编


def test_parse_ignores_blank_lines_and_crlf() -> None:
    rows = parse_tsv("\r\n" + REAL.replace("\n", "\r\n") + "\r\n")
    assert len(rows) == 4


def test_parse_accepts_datetime_in_date() -> None:
    rows = parse_tsv("611\t2026-03-16 08:30:00\n")
    assert rows[0].entry_date == date(2026, 3, 16)


def test_parse_rejects_wrong_field_count() -> None:
    # 列数不对 ⇒ 后面所有列都会错位。必须报错，不能跳过。
    with pytest.raises(FetchError, match="期望 2 列"):
        parse_tsv("611\t2026-03-16\t9792\n")
    with pytest.raises(FetchError, match="期望 2 列"):
        parse_tsv("611\n")


def test_parse_rejects_bad_date() -> None:
    with pytest.raises(FetchError, match="入库日期"):
        parse_tsv("611\t16/03/2026\n")


def test_parse_rejects_missing_room() -> None:
    with pytest.raises(FetchError, match="room_id"):
        parse_tsv("\t2026-03-16\n")


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


# ---------- 取数：复用权威源（无 mysql、无 docker、无自写 SQL） ----------


def test_ledger_rows_go_through_the_authoritative_service(monkeypatch) -> None:
    """默认取数**只**调权威源，并把库房号限定进去（口径唯一，不是第二套）。"""
    seen: list = []
    session = object()
    install_fake_service(
        monkeypatch,
        [{"room_id": "611", "in_date": date(2026, 9, 11), "min_date": date(2026, 9, 11),
          "max_date": date(2026, 9, 11)}],
        seen,
    )
    rows = ledger_rows_via_service("611", session=session)
    assert rows == [("611", date(2026, 9, 11))]
    assert seen == [(session, ["611"])]


def test_fetch_rows_from_injected_runner_without_any_database() -> None:
    """注入 runner：证明"能取到批次与房间"，且取数**不经过**任何外部客户端。"""
    rows = rows_to_batch_rows(fetch_ledger_rows("611", runner=lambda _room: [
        ("611", date(2026, 9, 11)), ("612", date(2026, 9, 11))]))
    assert {r.room_id for r in rows} == {"611", "612"}
    row = select_batch(rows, "611", today=TODAY)
    assert load_room_state_text(render_room_yaml(row, today=TODAY)).room_id == "611"


def test_ledger_rows_accept_datetime_and_string_dates() -> None:
    rows = rows_to_batch_rows([("611", datetime(2026, 9, 11, 8, 30)), ("612", "2026-09-11")])
    assert [r.entry_date for r in rows] == [date(2026, 9, 11)] * 2


def test_ledger_rows_reject_wrong_shape() -> None:
    with pytest.raises(FetchError, match="期望 2 列"):
        rows_to_batch_rows([("611", "2026-09-11", "9792")])
    with pytest.raises(FetchError, match="缺 room_id"):
        rows_to_batch_rows([(None, "2026-09-11")])
    with pytest.raises(FetchError, match="入库日期"):
        rows_to_batch_rows([("611", "not-a-date")])


def test_missing_authoritative_source_says_to_ship_src_package(monkeypatch) -> None:
    """镜像里没有 `src/**`（或没有 sqlalchemy）⇒ 指名道姓的错，**且不退回自写取数**。"""
    monkeypatch.setitem(sys.modules, "utils.batch_yield_service", None)   # import 会抛 ImportError
    with pytest.raises(FetchError, match="batch_yield_service"):
        ledger_rows_via_service("611", session=object())


def test_missing_engine_dependency_says_to_ship_src_package(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "global_const", None)
    monkeypatch.setitem(sys.modules, "sqlalchemy.orm", None)
    with pytest.raises(FetchError, match="权威取数依赖"):
        ledger_rows_via_service("611")          # 不注入 session ⇒ 走引擎 import


def test_service_failure_does_not_return_an_empty_ledger(monkeypatch) -> None:
    """权威源抛错 ⇒ 报错，绝不返回空表（空表会被读成"这个库房没有批次"）。"""
    module = types.ModuleType("utils.batch_yield_service")

    def boom(_session, room_ids=None, **_kw):
        raise RuntimeError("connection refused")

    module.get_batch_ranges = boom
    monkeypatch.setitem(sys.modules, "utils.batch_yield_service", module)
    with pytest.raises(FetchError, match="connection refused"):
        ledger_rows_via_service("611", session=object())


# ---------- 判定 ----------


def test_select_picks_latest_batch_for_room() -> None:
    row = select_batch(parse_tsv(REAL), "612", today=TODAY)
    assert row.entry_date == date(2026, 4, 5)      # 不是 2026-03-09
    assert row.batch_id == "612|2026-04-05"


def test_select_is_stable_on_same_date() -> None:
    rows = parse_tsv("611\t2026-03-16\n611\t2026-03-16\n")
    row = select_batch(rows, "611", today=TODAY)
    assert row.batch_no == "611_20260316"


def test_select_rejects_unknown_room() -> None:
    with pytest.raises(FetchError, match="没有库房"):
        select_batch(parse_tsv(REAL), "999", today=TODAY)


def test_select_rejects_future_entry_date() -> None:
    with pytest.raises(FetchError, match="之后"):
        select_batch(parse_tsv("611\t2026-09-20\n"), "611", today=TODAY)


# ---------- 渲染与落盘 ----------


def test_batch_no_is_derived_not_the_forbidden_primary_key() -> None:
    """显示名 = ``<room_id>_<YYYYMMDD>``（权威源自己在用的写法），不再是业务库主键。"""
    row = BatchRow(room_id="611", entry_date=date(2026, 9, 30))
    assert row.batch_no == "611_20260930"
    assert row.batch_id == "611|2026-09-30"
    assert "mogu-" not in render_room_yaml(row, today=date(2026, 10, 1))


def test_render_round_trips_through_patrol_room(tmp_path: Path) -> None:
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    text = render_room_yaml(row, today=TODAY, now=datetime(2026, 9, 13, 21, 30, 0))
    p = tmp_path / "room.yaml"
    p.write_text(text, encoding="utf-8")
    state = load_room_state(p)                     # consumer 是 patrol.room
    assert state.room_id == "611"
    assert state.entry_date == date(2026, 3, 16)
    # 包数没有来源 ⇒ null（未知），绝不许变成 0 或字符串 "None"
    assert state.packages is None and "packages: null" in text
    assert state.batch_no == "611_20260316"
    assert "611|2026-03-16" in state.source


def test_render_carries_both_day_conventions() -> None:
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    text = render_room_yaml(row, today=TODAY)
    # 2026-03-16 → 2026-09-13 是第 181 天，生产系统口径第 182 天；两者都要能看见
    assert "第 181 天" in text
    assert "第 182 天" in text
    assert "不巡检" in text


def test_render_in_window_says_admitted() -> None:
    row = select_batch(parse_tsv("611\t2026-09-11\n"), "611", today=TODAY)
    text = render_room_yaml(row, today=TODAY)
    assert "第 2 天" in text
    assert "准入" in text


def test_render_keeps_a_real_package_count_when_a_source_has_one() -> None:
    """字段形状不随取数源变：将来若有别的源能给包数，渲染不用改（且不再是 0/None 混用）。"""
    row = BatchRow(room_id="611", entry_date=date(2026, 9, 11), packages=9792)
    text = render_room_yaml(row, today=TODAY)
    assert "packages: 9792" in text
    assert load_room_state_text(text).packages == 9792


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
    new = BatchRow(room_id="612", entry_date=date(2026, 9, 10))
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


# ---------- CLI ----------


def test_main_offline_prints_only(tmp_path: Path, capsys) -> None:
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


def test_main_offline_writes_file(tmp_path: Path, capsys) -> None:
    dump = tmp_path / "dump.tsv"
    dump.write_text("611\t2026-09-11\n", encoding="utf-8")
    out = tmp_path / "room.yaml"
    rc = main(["--room", "611", "--from", str(dump), "--out", str(out),
               "--today", "2026-09-13"])
    assert rc == 0
    assert load_room_state(out).entry_date == date(2026, 9, 11)
    assert "本轮会巡检" in capsys.readouterr().out


def test_main_uses_the_ledger_by_default(tmp_path, capsys, monkeypatch) -> None:
    """默认通道 = 权威取数源；没有 `--from` 也不会去碰任何外部客户端。"""
    import deploy.fetch_room as mod

    monkeypatch.setattr(mod, "fetch_ledger_rows",
                        lambda _room, runner=None: [("611", "2026-09-11")])
    out = tmp_path / "room.yaml"
    rc = main(["--room", "611", "--out", str(out), "--today", "2026-09-13"])
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


def test_main_fails_closed_when_the_ledger_is_unreachable(tmp_path, capsys, monkeypatch) -> None:
    import deploy.fetch_room as mod

    def boom(_room, runner=None):
        raise FetchError("读 pgsql 台账失败：connection refused")

    monkeypatch.setattr(mod, "fetch_ledger_rows", boom)
    out = tmp_path / "room.yaml"
    rc = main(["--room", "611", "--out", str(out), "--today", "2026-09-13"])
    assert rc == 2 and not out.exists()
    assert "connection refused" in capsys.readouterr().err


# ---------- 守门：旧通路在源码里**不存在** ----------

FORBIDDEN = ("subprocess", "tools_mysql", "docker", "mysql", "MYSQL_ROOT_PASSWORD")


@pytest.mark.parametrize("token", FORBIDDEN)
def test_module_has_no_legacy_db_access(token: str) -> None:
    """用户 2026-10-14 裁定：业务库禁用、不得借他人容器取 root 口令。

    这条用**源码文本**守：只要有人把那条路写回来（哪怕只写一半），这里立刻红。
    """
    source = (Path(__file__).resolve().parents[1] / "src" / "deploy" / "fetch_room.py"
              ).read_text(encoding="utf-8")
    assert token.lower() not in source.lower()

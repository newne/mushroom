"""``deploy.fetch_room``：把批次台账变成 room.yaml。

三条主线：

1. **契约**：写出来的 YAML 必须能被 ``patrol.room`` 读回（消费端就是它），
   所以每个落盘用例都拿 ``load_room_state`` 复读一遍；
2. **取数源**（用户 2026-10-14 裁定 + Chief 同日裁定）：走**算法侧只读端点**
   ``GET /batches?room_id=…``，**不碰数据库、不写 SQL、不解析连接串**；
   旧的"借业务库容器 + root 口令"那条路必须**在源码里都不存在**（见文件末尾守门用例）；
3. **三个名字别混**：`batch_id`＝身份 `<room>|<date>`、`batch_no`＝端点给的派生名、
   `display_name`＝业务名 `mogu-*`（缺 ⇒ null，不编）。
"""

from __future__ import annotations

import sys
import types
from datetime import date, datetime
from pathlib import Path

import pytest
from deploy.fetch_room import (
    ANALYSIS_URL_ENV,
    DEFAULT_ANALYSIS_URL,
    BatchRow,
    FetchError,
    analysis_base_url,
    fetch_ledger_payload,
    fetch_ledger_rows,
    load_room_state_text,
    main,
    parse_tsv,
    read_tsv,
    refresh_room_file,
    render_room_yaml,
    rows_from_payload,
    select_batch,
    write_room_yaml,
)
from patrol.room import RoomStateError, load_room_state

TODAY = date(2026, 9, 13)

# 离线台账（`--from`，两列；演练/无库环境用）。现场真值来自只读端点。
REAL = (
    "612\t2026-04-05\n"
    "7\t2026-03-22\n"
    "611\t2026-03-16\n"
    "612\t2026-03-09\n"
)


def batch(room: str = "611", entry: str = "2026-09-30", batch_no: str = "611_20260930",
          display: str | None = "mogu-107", **extra):
    item = {"room_id": room, "room_id_raw": room, "entry_date": entry,
            "batch_id": f"{room}|{entry}", "batch_no": batch_no,
            "display_name": display, "until": None}
    item.update(extra)
    return item


def payload(items=None, *, stale: bool = False, ok: bool = True, **extra):
    rows = [batch()] if items is None else items
    out = {"ok": ok, "room_id": "611", "room_label": "611", "batches": rows,
           "n_batches": len(rows), "ledger_refreshed_at": "2026-10-14T13:59:55",
           "ledger_n_rows": 2, "ledger_n_pruned": 0, "stale": stale}
    out.update(extra)
    return out


def install_fake_httpx(monkeypatch, *, status: int = 200, body=None, seen: list | None = None):
    """假 httpx：证明 HTTP 层（URL/参数/状态码/JSON）也是被测过的。"""
    module = types.ModuleType("httpx")

    class _Resp:
        status_code = status

        def json(self):
            if body is None:
                raise ValueError("not json")
            return body

    def get(url, params=None, timeout=None):
        if seen is not None:
            seen.append((url, params, timeout))
        return _Resp()

    module.get = get
    monkeypatch.setitem(sys.modules, "httpx", module)
    return module


# ---------- 端点载荷 → BatchRow（三个名字） ----------


def test_rows_map_the_three_names_separately() -> None:
    rows = rows_from_payload(payload([batch()]))
    row = rows[0]
    assert row.room_id == "611" and row.entry_date == date(2026, 9, 30)
    assert row.batch_id == "611|2026-09-30"          # 身份，与端点同式
    assert row.batch_no == "611_20260930"            # 派生名（端点给的）
    assert row.display_name == "mogu-107"            # 业务名，单独一列
    assert row.packages is None                      # 台账没有这个量 ⇒ 未知，不是 0


def test_batch_no_is_taken_from_the_endpoint_not_derived_locally() -> None:
    """端点给什么就用什么——本模块**不自己拼**派生名。"""
    rows = rows_from_payload(payload([batch(batch_no="611_20260930_来自端点")]))
    assert rows[0].batch_no == "611_20260930_来自端点"


def test_display_name_may_be_missing_and_is_not_invented() -> None:
    rows = rows_from_payload(payload([batch(display=None)]))
    assert rows[0].display_name is None
    text = render_room_yaml(rows[0], today=TODAY)
    assert "display_name: null" in text


def test_batch_id_mismatch_is_refused() -> None:
    """身份字段与 (room_id, entry_date) 推出来的不一致 ⇒ 命名口径对不上，拒绝继续。"""
    with pytest.raises(FetchError, match="batch_id"):
        rows_from_payload(payload([batch(batch_id="612|2026-09-30")]))


def test_missing_batch_no_is_refused() -> None:
    with pytest.raises(FetchError, match="batch_no"):
        rows_from_payload(payload([batch(batch_no="")]))


def test_missing_room_or_bad_date_is_refused() -> None:
    with pytest.raises(FetchError, match="room_id"):
        rows_from_payload(payload([batch(room="")]))
    with pytest.raises(FetchError, match="入库日期"):
        rows_from_payload(payload([batch(entry="not-a-date")]))


# ---------- fail-closed：stale / ok / 结构 ----------


def test_stale_ledger_is_refused_with_a_clear_reason() -> None:
    with pytest.raises(FetchError, match="陈旧"):
        fetch_ledger_payload("611", runner=lambda _room: payload(stale=True))


def test_payload_without_stale_is_refused() -> None:
    """端点是唯一能判断"台账陈不陈旧"的地方：没给就拒绝，不许默认成"不陈旧"。"""
    bad = payload()
    del bad["stale"]
    with pytest.raises(FetchError, match="没给 stale"):
        fetch_ledger_payload("611", runner=lambda _room: bad)


def test_ok_false_and_bad_structure_are_refused() -> None:
    with pytest.raises(FetchError, match="ok≠true"):
        fetch_ledger_payload("611", runner=lambda _room: payload(ok=False, error="boom"))
    with pytest.raises(FetchError, match="batches 列表"):
        fetch_ledger_payload("611", runner=lambda _room: payload(batches="nope"))


def test_stale_refusal_keeps_the_old_file(tmp_path: Path, capsys) -> None:
    """陈旧 ⇒ 报错 + **保留旧 room.yaml**（一个字节都不改），绝不静默用旧值。"""
    import deploy.fetch_room as mod

    out = tmp_path / "room.yaml"
    old = 'room_id: "611"\nentry_date: "2026-09-30"\n'
    out.write_text(old, encoding="utf-8")
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(mod, "fetch_ledger_rows",
                       lambda *_a, **_kw: (_ for _ in ()).throw(FetchError("台账陈旧（stale=true）")))
        rc = main(["--room", "611", "--out", str(out), "--today", "2026-10-01"])
    finally:
        monkey.undo()
    assert rc == 2
    assert out.read_text(encoding="utf-8") == old
    assert "陈旧" in capsys.readouterr().err


# ---------- 台账元数据必须可见 ----------


def test_ledger_meta_is_logged() -> None:
    logs: list[str] = []
    fetch_ledger_payload("611", runner=lambda _room: payload(), log=logs.append)
    joined = "\n".join(logs)
    assert "台账：" in joined and "2026-10-14T13:59:55" in joined and "stale=False" in joined


def test_missing_ledger_meta_warns_instead_of_being_silent() -> None:
    logs: list[str] = []
    bare = {"ok": True, "stale": False, "batches": []}
    fetch_ledger_payload("611", runner=lambda _room: bare, log=logs.append)
    assert any("没给台账元数据" in line for line in logs)


# ---------- HTTP 层（URL / 参数 / 状态码） ----------


def test_analysis_base_url_priority(monkeypatch) -> None:
    monkeypatch.setenv(ANALYSIS_URL_ENV, "http://env-host:9000")
    assert analysis_base_url("http://arg-host:8000") == "http://arg-host:8000"
    assert analysis_base_url(None) == "http://env-host:9000"
    monkeypatch.delenv(ANALYSIS_URL_ENV, raising=False)
    assert analysis_base_url(None) == DEFAULT_ANALYSIS_URL


def test_http_layer_calls_batches_endpoint_with_room_id(monkeypatch) -> None:
    seen: list = []
    install_fake_httpx(monkeypatch, body=payload(), seen=seen)
    got = fetch_ledger_payload("611", url="http://analysis:8000")
    assert got["ok"] is True
    url, params, _timeout = seen[0]
    assert url == "http://analysis:8000/batches" and params == {"room_id": "611"}


def test_http_layer_refuses_non_200(monkeypatch) -> None:
    install_fake_httpx(monkeypatch, status=503)
    with pytest.raises(FetchError, match="HTTP 503"):
        fetch_ledger_payload("611")


def test_http_layer_refuses_non_json(monkeypatch) -> None:
    install_fake_httpx(monkeypatch, body=None)
    with pytest.raises(FetchError, match="不是 JSON"):
        fetch_ledger_payload("611")


def test_fetch_ledger_rows_end_to_end_with_a_fake_runner() -> None:
    rows = fetch_ledger_rows("611", runner=lambda _room: payload())
    row = select_batch(rows, "611", today=date(2026, 10, 1))
    assert row.batch_no == "611_20260930"
    assert load_room_state_text(render_room_yaml(row, today=date(2026, 10, 1))).room_id == "611"


# ---------- 离线 TSV（旁路，不是取数通道） ----------


def test_parse_offline_tsv() -> None:
    rows = parse_tsv(REAL)
    assert len(rows) == 4
    assert rows[0].room_id == "612"
    assert rows[0].entry_date == date(2026, 4, 5)
    assert rows[0].batch_id == "612|2026-04-05"
    assert rows[0].batch_no == "612_20260405"        # 离线旁路本机派生（生产用端点给的）
    assert rows[0].display_name is None              # 离线没有业务名 ⇒ null


def test_parse_ignores_blank_lines_and_crlf() -> None:
    rows = parse_tsv("\r\n" + REAL.replace("\n", "\r\n") + "\r\n")
    assert len(rows) == 4


def test_parse_accepts_datetime_in_date() -> None:
    rows = parse_tsv("611\t2026-03-16 08:30:00\n")
    assert rows[0].entry_date == date(2026, 3, 16)


def test_parse_rejects_wrong_field_count() -> None:
    with pytest.raises(FetchError, match="期望 2 列"):
        parse_tsv("611\t2026-03-16\t9792\n")
    with pytest.raises(FetchError, match="期望 2 列"):
        parse_tsv("611\n")


def test_parse_rejects_bad_date_and_missing_room() -> None:
    with pytest.raises(FetchError, match="入库日期"):
        parse_tsv("611\t16/03/2026\n")
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


def test_render_round_trips_through_patrol_room(tmp_path: Path) -> None:
    row = select_batch(rows_from_payload(payload()), "611", today=date(2026, 10, 1))
    text = render_room_yaml(row, today=date(2026, 10, 1),
                            now=datetime(2026, 10, 1, 21, 30, 0))
    p = tmp_path / "room.yaml"
    p.write_text(text, encoding="utf-8")
    state = load_room_state(p)                     # consumer 是 patrol.room
    assert state.room_id == "611"
    assert state.entry_date == date(2026, 9, 30)
    assert state.packages is None                  # 未知 ⇒ null（不许变 0 / "None"）
    assert state.batch_no == "611_20260930"        # 派生名
    assert "packages: null" in text and 'display_name: "mogu-107"' in text
    assert "611|2026-09-30" in state.source


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
    new = BatchRow(room_id="612", entry_date=date(2026, 9, 10), batch_no="612_20260910")
    note = write_room_yaml(out, render_room_yaml(new, today=TODAY))
    assert "2026-04-05 → 2026-09-10" in note
    assert load_room_state(out).entry_date == date(2026, 9, 10)


def test_write_leaves_no_temp_file(tmp_path: Path) -> None:
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    write_room_yaml(tmp_path / "room.yaml", render_room_yaml(row, today=TODAY))
    assert [p.name for p in tmp_path.iterdir()] == ["room.yaml"]


def test_write_refuses_broken_text(tmp_path: Path) -> None:
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
    out = tmp_path / "room.yaml"
    out.write_text('room_id: "unset"\nentry_date: ""\n', encoding="utf-8")
    row = select_batch(parse_tsv(REAL), "611", today=TODAY)
    note = write_room_yaml(out, render_room_yaml(row, today=TODAY))
    assert "旧文件不可用" in note
    assert load_room_state(out).entry_date == date(2026, 3, 16)


def test_missing_file_is_not_replaced_silently(tmp_path: Path) -> None:
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


def test_main_uses_the_endpoint_by_default(tmp_path, capsys, monkeypatch) -> None:
    """默认通道 = 只读端点（假 runner 替掉网络），并打印端点给的三个名字。"""
    import deploy.fetch_room as mod

    monkeypatch.setattr(mod, "fetch_ledger_rows",
                        lambda *_a, **_kw: rows_from_payload(payload()))
    out = tmp_path / "room.yaml"
    rc = main(["--room", "611", "--out", str(out), "--today", "2026-10-01"])
    assert rc == 0
    assert load_room_state(out).entry_date == date(2026, 9, 30)
    printed = capsys.readouterr().out
    assert "611_20260930" in printed and "mogu-107" in printed


def test_main_fails_closed_on_unknown_room(tmp_path: Path, capsys) -> None:
    dump = tmp_path / "dump.tsv"
    dump.write_text(REAL, encoding="utf-8")
    out = tmp_path / "room.yaml"
    rc = main(["--room", "999", "--from", str(dump), "--out", str(out),
               "--today", "2026-09-13"])
    assert rc == 2
    assert not out.exists()                     # 取不到数 ⇒ 一个字节都不写
    assert "没有库房" in capsys.readouterr().err


def test_main_fails_closed_when_the_endpoint_is_unreachable(tmp_path, capsys, monkeypatch) -> None:
    import deploy.fetch_room as mod

    def boom(*_a, **_kw):
        raise FetchError("读批次端点失败：connection refused")

    monkeypatch.setattr(mod, "fetch_ledger_rows", boom)
    out = tmp_path / "room.yaml"
    rc = main(["--room", "611", "--out", str(out), "--today", "2026-10-01"])
    assert rc == 2 and not out.exists()
    assert "connection refused" in capsys.readouterr().err


# ---------- 轮次前的就地刷新（票 18） ----------


def seed_room_file(tmp_path: Path, entry: date, *, room: str = "611") -> tuple[Path, bytes]:
    """先放一份"旧文件"，返回 (路径, 旧字节)——后面用它断言"有没有被写"。"""
    path = tmp_path / "room.yaml"
    text = render_room_yaml(BatchRow(room_id=room, entry_date=entry,
                                     batch_no=f"{room}_{entry.strftime('%Y%m%d')}"),
                            today=entry)
    path.write_text(text, encoding="utf-8")
    return path, path.read_bytes()


def test_refresh_updates_when_the_batch_changed(tmp_path: Path) -> None:
    path, _old = seed_room_file(tmp_path, date(2026, 9, 30))
    result = refresh_room_file("611", path, log=lambda _m: None,
                               runner=lambda _r: payload([batch(entry="2026-10-08")]))

    assert result["error"] is None and result["changed"] is True
    assert result["previous_entry_date"] == "2026-09-30"
    assert result["entry_date"] == "2026-10-08"
    assert "2026-09-30 → 2026-10-08" in result["note"]
    assert load_room_state(path).entry_date == date(2026, 10, 8)


def test_refresh_is_idempotent_when_the_batch_is_unchanged(tmp_path: Path) -> None:
    """未变 ⇒ **一个字节都不写**（不抖 mtime）。"""
    path, old = seed_room_file(tmp_path, date(2026, 9, 30))
    before = path.stat().st_mtime_ns

    result = refresh_room_file("611", path, log=lambda _m: None,
                               runner=lambda _r: payload())

    assert result["error"] is None and result["changed"] is False
    assert "未变" in result["note"]
    assert path.read_bytes() == old
    assert path.stat().st_mtime_ns == before


def test_refresh_keeps_the_old_file_when_the_endpoint_fails(tmp_path: Path) -> None:
    path, old = seed_room_file(tmp_path, date(2026, 9, 30))

    def boom(_room):
        raise FetchError("读批次端点失败：connection refused")

    result = refresh_room_file("611", path, log=lambda _m: None, runner=boom)

    assert result["changed"] is False and result["error"]
    assert result["entry_date"] == "2026-09-30"          # 沿用最后已知批次
    assert path.read_bytes() == old


def test_refresh_keeps_the_old_file_when_the_ledger_is_stale(tmp_path: Path) -> None:
    path, old = seed_room_file(tmp_path, date(2026, 9, 30))
    result = refresh_room_file("611", path, log=lambda _m: None,
                               runner=lambda _r: payload(stale=True))
    assert "陈旧" in result["error"] and path.read_bytes() == old


def test_refresh_keeps_the_old_file_on_a_future_entry_date(tmp_path: Path) -> None:
    path, old = seed_room_file(tmp_path, date(2026, 9, 30))
    result = refresh_room_file("611", path, log=lambda _m: None, today=date(2026, 9, 30),
                               runner=lambda _r: payload([batch(entry="2026-11-01")]))
    assert "之后" in result["error"] and path.read_bytes() == old


def test_refresh_writes_a_missing_file_when_the_endpoint_is_ok(tmp_path: Path) -> None:
    path = tmp_path / "room.yaml"
    result = refresh_room_file("611", path, log=lambda _m: None,
                               runner=lambda _r: payload())
    assert result["error"] is None and result["changed"] is True
    assert load_room_state(path).entry_date == date(2026, 9, 30)


def test_refresh_does_not_write_a_missing_file_when_the_endpoint_fails(tmp_path: Path) -> None:
    """文件缺失 + 端点不可用 ⇒ 不写（daemon 读不到 ⇒ 按既有契约不动机构）。"""
    path = tmp_path / "room.yaml"

    def boom(_room):
        raise FetchError("读批次端点失败：timeout")

    result = refresh_room_file("611", path, log=lambda _m: None, runner=boom)
    assert result["error"] and not path.exists()


# ---------- 守门：旧通路与"第二套取数"在源码里**不存在** ----------

FORBIDDEN = ("subprocess", "tools_mysql", "docker", "mysql", "MYSQL_ROOT_PASSWORD",
             "psycopg", "sqlalchemy", "PGSQL_DSN", "batch_yield_service", "SELECT ")


@pytest.mark.parametrize("token", FORBIDDEN)
def test_module_has_no_database_access_or_legacy_path(token: str) -> None:
    """裁定：业务库禁用、取数只能走只读端点；**不许自己写 SQL / 解析连接串 / 连库**。

    这条用**源码文本**守：只要有人把那条路写回来（哪怕只写一半），这里立刻红。
    """
    source = (Path(__file__).resolve().parents[1] / "src" / "deploy" / "fetch_room.py"
              ).read_text(encoding="utf-8")
    assert token.lower() not in source.lower()

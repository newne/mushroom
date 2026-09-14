"""巡检准入门禁：入库天数规则与 fail-closed 行为。

业务规则（用户 2026-09-13 定）：**第 0、1 天不动；第 26 天起不自动巡检**；
中间窗口可巡检。整库同一个入库时间 ⇒ 库房级判定。

这里守两类事，第二类比第一类重要得多：

1. **天数算得对**：入库当天 = 第 0 天，窗口端点含在内。
2. **读不到就不动**：文件缺失、entry_date 空、日期格式错、包数非数字、窗口倒置
   ——全部必须拒绝巡检。这台机器会自己走 4.5 米导轨，**没有准入依据时的默认必须是"不动"**，
   而不是"没有限制条件所以照跑"。误动一次产出 60 张错误照片并写进测量表；
   少拍一轮只是少一轮数据。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from patrol.daemon import PatrolDaemon
from patrol.room import (
    MAX_DAY,
    MIN_DAY,
    RoomState,
    RoomStateError,
    ensure_room_file,
    load_room_state,
    parse_room_state,
)
from patrol.store import JsonlStore
from patrol.sync import SyncClient

# ---------- 天数与窗口 ----------


def state(entry: date, **kw) -> RoomState:
    return RoomState(room_id="A", entry_date=entry, **kw)


def test_day_zero_is_the_entry_day_itself():
    assert state(date(2026, 9, 13)).day_on(date(2026, 9, 13)) == 0


@pytest.mark.parametrize(
    ("entry", "today", "day"),
    [
        (date(2026, 9, 13), date(2026, 9, 13), 0),
        (date(2026, 9, 13), date(2026, 9, 14), 1),
        (date(2026, 9, 13), date(2026, 9, 15), 2),
        (date(2026, 9, 13), date(2026, 10, 8), 25),
        (date(2026, 9, 13), date(2026, 10, 9), 26),
        (date(2026, 8, 1), date(2026, 9, 13), 43),
    ],
)
def test_day_counting(entry, today, day):
    assert state(entry).day_on(today) == day


def test_our_day_zero_is_the_systems_day_one():
    """两套天数口径必须钉住，否则会静默放行"第 1 天"。

    实测生产系统 `mushroom_operating_record.in_day_num`：611 库 in_time=2026-03-16，
    **当天**的记录 in_day_num=**01**（从 1 开始数）。我们内部从 0 开始数，
    所以「入库当天」= 我们第 0 天 = 系统第 1 天。差一位会让"前 2 天不动"
    悄悄变成"只挡第 1 天"，第 2 天起就能拍——正是规则要挡的范围。
    """
    entry = date(2026, 3, 16)
    s = state(entry)
    assert s.day_on(entry) == 0, "我们口径：入库当天 = 第 0 天"
    _, reason = s.verdict(entry)
    assert "第 0 天" in reason, "文案要给我们口径"
    assert "第 1 天" in reason, f"文案也要给系统口径（in_day_num 从 1 起），实际: {reason}"


def test_reason_mentions_both_conventions_on_every_path():
    """放行/拒绝两条路径都要给两套口径——现场照着系统看天数。"""
    entry = date(2026, 3, 16)
    allowed, why = state(entry).verdict(date(2026, 3, 18))       # 我们第 2 天 = 系统第 3 天
    assert allowed and "第 2 天" in why and "第 3 天" in why
    refused, why2 = state(entry).verdict(date(2026, 4, 12))      # 我们第 27 天 = 系统第 28 天
    assert not refused and "第 27 天" in why2 and "第 28 天" in why2


@pytest.mark.parametrize("day", [0, 1])
def test_first_two_days_must_not_move(day):
    entry = date(2026, 9, 13)
    allowed, reason = state(entry).verdict(date(2026, 9, 13 + day) if day else entry)
    assert allowed is False
    assert "不动" in reason


@pytest.mark.parametrize("day", list(range(MIN_DAY, MAX_DAY + 1)))
def test_whole_window_is_allowed(day):
    """窗口 2–25 天**含端点**全部放行——端点算错的代价是白等一天或早停一天。"""
    from datetime import timedelta

    entry = date(2026, 9, 1)
    allowed, reason = state(entry).verdict(entry + timedelta(days=day))
    assert allowed is True, reason
    assert str(day) in reason


@pytest.mark.parametrize("day", [MAX_DAY + 1, MAX_DAY + 2, 40, 120])
def test_day_after_the_window_must_not_patrol(day):
    from datetime import timedelta

    entry = date(2026, 9, 1)
    allowed, reason = state(entry).verdict(entry + timedelta(days=day))
    assert allowed is False
    assert "不再自动巡检" in reason


def test_future_entry_date_is_refused():
    """日期在未来 = 数据有误。不能因为"算出来是负数"就当成第 0 天放行。"""
    allowed, reason = state(date(2099, 1, 1)).verdict(date(2026, 9, 13))
    assert allowed is False
    assert "未来" in reason


def test_window_is_configurable():
    s = RoomState(room_id="A", entry_date=date(2026, 9, 1), min_day=5, max_day=10)
    assert s.verdict(date(2026, 9, 4))[0] is False     # 第 3 天 < min
    assert s.verdict(date(2026, 9, 6))[0] is True      # 第 5 天 = min（含）
    assert s.verdict(date(2026, 9, 11))[0] is True     # 第 10 天 = max（含）
    assert s.verdict(date(2026, 9, 12))[0] is False    # 第 11 天 > max


# ---------- 读不到就不动（fail-closed） ----------


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(RoomStateError, match="不存在"):
        load_room_state(tmp_path / "nope.yaml")


def test_blank_entry_date_is_refused(tmp_path):
    """占位文件就是留空的——它必须导致"不动"，而不是被当成配置好了。"""
    p = ensure_room_file(tmp_path / "room.yaml", room_id="A")
    text = p.read_text(encoding="utf-8")
    assert "entry_date" in text
    with pytest.raises(RoomStateError, match="缺少 entry_date"):
        load_room_state(p)


@pytest.mark.parametrize("bad", ["13/09/2026", "2026-13-01", "today", "20260913", "  "])
def test_bad_date_is_refused(bad):
    with pytest.raises(RoomStateError):
        parse_room_state({"entry_date": bad})


def test_non_integer_packages_is_refused():
    with pytest.raises(RoomStateError, match="packages"):
        parse_room_state({"entry_date": "2026-09-01", "packages": "很多包"})


def test_inverted_window_is_refused():
    with pytest.raises(RoomStateError, match="min_day"):
        parse_room_state({"entry_date": "2026-09-01", "min_day": 30, "max_day": 5})


def test_non_mapping_top_level_is_refused():
    with pytest.raises(RoomStateError):
        parse_room_state(["entry_date"])


def test_valid_file_roundtrip(tmp_path):
    p = tmp_path / "room.yaml"
    p.write_text(
        "room_id: 库房A\n"
        "entry_date: 2026-09-01\n"
        "packages: 1200\n"
        "batch_no: B-20260901-01\n"
        "source: 台账接口 2026-09-01 08:00\n",
        encoding="utf-8",
    )
    s = load_room_state(p)
    assert s.room_id == "库房A"
    assert s.entry_date == date(2026, 9, 1)
    assert s.packages == 1200
    assert s.batch_no == "B-20260901-01"
    assert "台账" in s.source


def test_yaml_date_object_is_accepted(tmp_path):
    """YAML 会把 `entry_date: 2026-09-01` 解析成 date 对象，不能只认字符串。"""
    p = tmp_path / "room.yaml"
    p.write_text("entry_date: 2026-09-01\n", encoding="utf-8")
    assert load_room_state(p).entry_date == date(2026, 9, 1)


def test_ensure_room_file_does_not_clobber_existing(tmp_path):
    p = tmp_path / "room.yaml"
    p.write_text("entry_date: 2026-09-01\n", encoding="utf-8")
    ensure_room_file(p)
    assert load_room_state(p).entry_date == date(2026, 9, 1)


# ---------- 接进 daemon：不动 = 不连控制器、不建取证 ----------


def make_daemon(tmp_path, room_state, *, today: date):
    opened = []

    def open_fmc():
        opened.append(1)
        raise AssertionError("准入没过就不该连接控制器")

    daemon = PatrolDaemon(
        open_fmc=open_fmc,
        stations=[],
        capture_client=object(),
        store=JsonlStore(str(tmp_path / "outbox.jsonl")),
        sync=SyncClient(transport=lambda url, body=None: None),
        room_state=room_state,
        now=lambda: datetime(today.year, today.month, today.day, 9, 0, 0),
        log=lambda _m: None,
    )
    return daemon, opened


@pytest.mark.parametrize("day", [0, 1, 26, 40])
def test_daemon_skips_round_without_touching_controller(tmp_path, day):
    from datetime import timedelta

    entry = date(2026, 9, 1)
    daemon, opened = make_daemon(tmp_path, state(entry), today=entry + timedelta(days=day))
    assert daemon.run_cycle() == "skipped"
    assert opened == [], "没准入就不该有任何连接动作"
    assert not (tmp_path / "runs").exists(), "没跑就不该留取证文件"
    assert len(JsonlStore(str(tmp_path / "outbox.jsonl"))) == 0


def test_daemon_without_room_state_skips(tmp_path):
    """未接入库状态 ⇒ 不动。这是默认路径，所以必须显式测。"""
    daemon, opened = make_daemon(tmp_path, None, today=date(2026, 9, 13))
    assert daemon.run_cycle() == "skipped"
    assert opened == []


def test_daemon_enters_patrol_on_the_first_allowed_day(tmp_path):
    """第 2 天必须放行——否则窗口起点算错会白等到第 3 天。

    放行的判据是"它去连控制器了"：`open_fmc` 每次都失败 ⇒ 重试耗尽 ⇒ run_cycle 返回
    "failed"。若准入把它拦下，返回值会是 "skipped" 且一次连接都不会发生。
    """
    entry = date(2026, 9, 1)
    today = date(2026, 9, 3)          # 第 2 天 = 窗口第一天
    attempts = []

    def open_fmc():
        attempts.append(1)
        raise RuntimeError("假装连不上")

    daemon = PatrolDaemon(
        open_fmc=open_fmc,
        stations=[],
        capture_client=object(),
        store=JsonlStore(str(tmp_path / "outbox.jsonl")),
        sync=SyncClient(transport=lambda url, body=None: None),
        room_state=state(entry),
        now=lambda: datetime(today.year, today.month, today.day, 9, 0, 0),
        reconnect_attempts=1,
        backoff_s=0.0,
        log=lambda _m: None,
    )
    assert daemon.run_cycle() == "failed", "放行后应一路走到连接（连不上才是 failed）"
    assert attempts, "第 2 天必须真的去连控制器"


def test_admission_reason_mentions_the_day_number(tmp_path):
    """日志里要能直接看到第几天——现场靠这句话判断"为什么今天没动"。"""
    entry = date(2026, 9, 1)
    daemon, _ = make_daemon(tmp_path, state(entry), today=entry)
    allowed, reason = daemon._admission()
    assert allowed is False
    assert "第 0 天" in reason


# ---------- 每轮现读 room.yaml（换批次不用重启进程） ----------


def room_file(tmp_path, entry: date, *, name: str = "room.yaml"):
    p = tmp_path / name
    p.write_text(
        f'room_id: "611"\nentry_date: "{entry.isoformat()}"\nbatch_no: "B1"\n',
        encoding="utf-8",
    )
    return p


def test_daemon_rereads_room_file_every_cycle(tmp_path):
    """准入依据是**文件**，不是启动那一刻的快照。

    常驻进程里 `room.yaml` 会被 `deploy-fetch-room` 换掉（新批次入库），进程必须自己
    跟上：把日期冻在启动那一刻，天数是错的，而它不会报错——只会一直按旧结论办事。
    """
    today = date(2026, 9, 13)
    path = room_file(tmp_path, date(2026, 3, 16))          # 第 181 天 ⇒ 不巡检
    attempts = []

    def open_fmc():
        attempts.append(1)
        raise RuntimeError("假装连不上")

    daemon = PatrolDaemon(
        open_fmc=open_fmc,
        stations=[],
        capture_client=object(),
        store=JsonlStore(str(tmp_path / "outbox.jsonl")),
        sync=SyncClient(transport=lambda url, body=None: None),
        room_state_path=path,
        now=lambda: datetime(today.year, today.month, today.day, 9, 0, 0),
        reconnect_attempts=1,
        backoff_s=0.0,
        log=lambda _m: None,
    )

    assert daemon.run_cycle() == "skipped"
    assert attempts == []

    room_file(tmp_path, date(2026, 9, 11))                 # 换批次：第 2 天 ⇒ 放行
    assert daemon.run_cycle() == "failed", "换过文件后应走到连接（连不上才 failed）"
    assert attempts, "新入库日期进窗口后，不重启进程也该开始巡检"


def test_daemon_reports_why_room_file_is_unusable(tmp_path):
    """文件坏了要说出坏在哪，而不是笼统一句"未接入"——现场就靠这句话定位。"""
    path = tmp_path / "room.yaml"
    path.write_text('room_id: "611"\nentry_date: "13/09/2026"\n', encoding="utf-8")
    daemon, opened = make_daemon(tmp_path, None, today=date(2026, 9, 13))
    daemon.room_state_path = path

    assert daemon.run_cycle() == "skipped"
    allowed, reason = daemon._admission()
    assert allowed is False
    assert "YYYY-MM-DD" in reason
    assert opened == []


def test_daemon_with_missing_room_file_says_missing(tmp_path):
    daemon, _ = make_daemon(tmp_path, None, today=date(2026, 9, 13))
    daemon.room_state_path = tmp_path / "nope.yaml"
    allowed, reason = daemon._admission()
    assert allowed is False
    assert "不存在" in reason

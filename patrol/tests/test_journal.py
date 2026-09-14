"""轮次取证：把"进程没了好歹留下它走到哪"变成可回归的性质。

起因是 2026-09-13 那次整轮上机——进程跑了 2 分 46 秒、抓到第 14 站被打断，
而 outbox 里**一条记录都没有**：现场只知道它死了，查不出走到哪、为什么死。
本文件的断言就是针对那一幕：

1. 开始就有痕迹（不是等地跑完才写）；
2. 逐站心跳**当场落盘并 fsync**（进程被 kill 也留得住）；
3. 被中断的那一轮，痕迹里能读出**最后走到的站位**；
4. 异常路径留下类型/文本/堆栈；
5. 取证写不进去（磁盘满/只读）**不得**影响巡检本身。

另守一条分工：取证**不进** outbox——否则会给 prod 灌它不关心的行，而且同步成功
会清空 outbox，取证反而跟着没了。
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime

from fmc_fakes import COMMISSIONED_SOFT_LIMITS, FakeFmcLib
from patrol.daemon import JOURNAL_DISABLED, PatrolDaemon, journal_dir_for
from patrol.fmc import Fmc4030, FmcError
from patrol.journal import RoundJournal
from patrol.room import RoomState
from patrol.stations import Station
from patrol.store import JsonlStore
from patrol.sync import SyncClient

# 这些用例测的是 daemon 自身行为（重连/急停/取证/落库），不是准入判定；
# 准入有它自己的测试文件（test_room.py）。这里显式给一份"已过准入"的库房状态，
# 而不是让门禁默认放行——默认必须是"不动"（fail-closed）。
_ENTRY = date(2026, 1, 1)
ALLOWED_ROOM = RoomState(room_id="test", entry_date=_ENTRY)


def _allowed_now() -> datetime:
    """固定"今天"= 入库第 10 天，落在准入窗口内。"""
    return datetime(2026, 1, 11, 9, 0, 0)


def read_lines(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------- RoundJournal 本体 ----------


def test_journal_starts_on_disk_immediately(tmp_path):
    """第一行必须**立刻**在盘上：进程随后的死亡不能抹掉"这一轮开始过"。"""
    j = RoundJournal(tmp_path, now=datetime(2026, 9, 13, 9, 14, 40), pid=4242)
    j.start(stations=60)
    assert j.path.name == "20260913-091440-4242.jsonl"
    assert j.path.exists(), "start() 之后文件就该存在，而不是等 close"
    assert read_lines(j.path)[0]["event"] == "start"
    assert read_lines(j.path)[0]["stations"] == 60
    j.close()


def test_filename_carries_pid_so_two_processes_do_not_collide(tmp_path):
    """同秒启动的两个进程（并发驱动同一控制器）不该互相覆盖取证。"""
    a = RoundJournal(tmp_path, now=datetime(2026, 9, 13, 9, 0, 0), pid=1)
    b = RoundJournal(tmp_path, now=datetime(2026, 9, 13, 9, 0, 0), pid=2)
    assert a.path != b.path


def test_station_heartbeats_are_readable_after_abrupt_death(tmp_path):
    """模拟"写完第 3 站就被 kill"：盘上必须能读出走到哪一站。"""
    j = RoundJournal(tmp_path)
    j.start(stations=60)
    for i in (1, 2, 3):
        j.station(i, 60, station_id=f"S10{i}", ok=True, elapsed_s=8.8)
    path = j.path
    # 模拟 kill -9：文件句柄没关、end 没写、也不调 close
    events = read_lines(path)
    assert [e["event"] for e in events] == ["start", "station", "station", "station"]
    assert events[-1]["station_id"] == "S103", "最后一行就是它走到的那一站"
    assert not any(e["event"] == "end" for e in events), "没写完就没有 end，这本身就是证据"
    j.close()


def test_lines_are_fsynced_not_just_buffered(tmp_path):
    """只 flush 不 fsync 的话，机器级断电仍会丢——本模块存在的意义就在这一步。"""
    calls = {"n": 0}
    real_fsync = os.fsync

    def counting_fsync(fd):
        calls["n"] += 1
        return real_fsync(fd)

    j = RoundJournal(tmp_path)
    try:
        import patrol.journal as mod

        orig = mod.os.fsync
        mod.os.fsync = counting_fsync
        j.start(stations=1)
        j.station(1, 1, station_id="S101", ok=True)
        j.end(status="ok")
    finally:
        import patrol.journal as mod

        mod.os.fsync = orig
        j.close()
    assert calls["n"] == 3, "每一行都要 fsync"


def test_exception_records_type_text_and_traceback(tmp_path):
    j = RoundJournal(tmp_path)
    try:
        raise FmcError(-7, "goto")
    except FmcError as e:
        j.exception(e, where="round")
    j.close()

    end = read_lines(j.path)[-1]
    assert end["event"] == "end" and end["status"] == "exception"
    assert end["where"] == "round"
    assert "FmcError" in end["error"]
    assert "Traceback" in end["traceback"], "没有堆栈就还要靠猜"


def test_write_failure_does_not_raise(tmp_path):
    """磁盘满/只读挂载时，取证写不进去也不能把巡检拖垮。

    只让**写**失败（不碰 mkdir）：现场更可能的形态是磁盘写满，而不是目录建不出来。
    """
    j = RoundJournal(tmp_path)
    j.start(stations=1)  # 正常开出文件
    real_write = j._fh.write

    def boom(_text):
        raise OSError(28, "No space left on device")

    j._fh.write = boom
    try:
        j.station(1, 1, ok=True)              # 不抛
        j.append({"event": "whatever"})       # 不抛
    finally:
        j._fh.write = real_write
        j.close()


def test_journal_dir_sits_next_to_the_outbox(tmp_path):
    """取证目录跟着 outbox 走：运维知道 outbox 在哪就知道取证在哪。"""
    assert journal_dir_for(tmp_path / "outbox.jsonl") == tmp_path / "runs"


# ---------- 接进 daemon ----------


class OkCapture:
    def capture(self, *, ip, user="admin", pwd="", filename, storage="cloud"):
        from patrol.capture_client import CaptureResult

        return CaptureResult(object_name=filename + ".jpg", file_path=None,
                             cloud_url="http://minio/x.jpg", raw={})


class SucceedingFmc(Fmc4030):
    def __init__(self):
        super().__init__(FakeFmcLib(soft_limits=COMMISSIONED_SOFT_LIMITS))
        self.pos = (0.0, 10.0, -21.2)

    def home_all(self, **_kw):
        return None

    def goto(self, y, z, **_kw):
        self._lib.pos = (0.0, y, z)

    def current_yz(self):
        return (self._lib.pos[1], self._lib.pos[2])

    def lamp(self, on, *, io=0):
        return None

    def stop_everything(self):
        return []


def make_daemon(tmp_path, *, fmc_factory, stations=2, **kw):
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=fmc_factory,
        stations=[Station(id=f"S10{i}", box_id=f"B10{i}", y=10.0 * i, z=-21.2,
                          camera_ip="192.168.1.238") for i in range(1, stations + 1)],
        capture_client=OkCapture(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: {"ok": True}),
        log=lambda _m: None,
        **kw,
    )
    return daemon, store


def test_daemon_writes_a_journal_that_records_every_station(tmp_path):
    daemon, _store = make_daemon(tmp_path, fmc_factory=SucceedingFmc)
    assert daemon.run_cycle() == "ok"

    runs = list((tmp_path / "runs").glob("*.jsonl"))
    assert len(runs) == 1, "一轮一个取证文件"
    events = read_lines(runs[0])
    assert events[0]["event"] == "start"
    assert [e["station_id"] for e in events if e["event"] == "station"] == ["S101", "S102"]
    assert events[-1]["event"] == "end" and events[-1]["status"] == "ok"
    assert events[-1]["n_results"] == 2


def test_interrupted_round_leaves_journal_but_no_outbox_rows(tmp_path):
    """**就是 2026-09-13 那一幕**：outbox 零记录，但取证能读出走到哪、为什么死。

    运动失败 ⇒ 本轮作废（不再把后续 58 个站位也开过去再一个个失败）。
    """

    class DiesMidRound(SucceedingFmc):
        def goto(self, y, z, **_kw):
            # 第 2 站（y=20）的运动失败 ⇒ round 抛 FmcRoundAbort ⇒ daemon 急停 + failed
            if y > 10.0:
                raise FmcError(-5, "goto 发送失败")
            super().goto(y, z)

    daemon, store = make_daemon(tmp_path, fmc_factory=DiesMidRound, stations=3)
    assert daemon.run_cycle() == "failed"

    # outbox：一条业务记录都没有（事务性：半截的轮次不留数据）
    assert len(store.pending()) == 0

    # 取证：走到哪、怎么死的，全在
    journal = next((tmp_path / "runs").glob("*.jsonl"))
    events = read_lines(journal)
    assert events[0]["event"] == "start"
    stations_done = [e["station_id"] for e in events if e["event"] == "station"]
    assert stations_done == ["S101", "S102"], "第 1 站成功、第 2 站炸——最后心跳就是断点"
    failed = events[2]
    assert failed["ok"] is False and failed["abort"] == "motion"
    assert "发送失败" in failed["error"]
    end = events[-1]
    assert end["event"] == "end" and end["status"] == "exception"
    assert "FmcRoundAbort" in end["error"]
    assert any(e["event"] == "emergency_stop" for e in events), "急停也要留痕"


def test_unexpected_exception_is_recorded_and_not_swallowed(tmp_path):
    """未预期异常过去会 `raise` 出去（进程死、啥也没留）；现在要记下来并转 failed。"""

    class Surprise(SucceedingFmc):
        def home_all(self, **_kw):
            raise RuntimeError("第三方库内部炸了")

    daemon, _store = make_daemon(tmp_path, fmc_factory=Surprise)
    assert daemon.run_cycle() == "failed"
    end = read_lines(next((tmp_path / "runs").glob("*.jsonl")))[-1]
    assert end["status"] == "exception"
    assert "RuntimeError: 第三方库内部炸了" in end["error"]


def test_heartbeat_reports_failures_too(tmp_path):
    """站位级采图失败不抛异常，所以心跳是它唯一的取证渠道。

    只有 1 个站位、1 次失败 ⇒ 达不到"连续 3 次"的中止门槛 ⇒ 本轮跑完、结果 partial。
    """
    calls = {"n": 0}

    class FlakyCapture:
        def capture(self, *, ip, user="admin", pwd="", filename, storage="cloud"):
            calls["n"] += 1
            from patrol.capture_client import CaptureError

            raise CaptureError("采图失败: 无头模式截图失败")

    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=SucceedingFmc,
        stations=[Station(id="S101", box_id="B101", y=10.0, z=-21.2,
                          camera_ip="192.168.1.238")],
        capture_client=FlakyCapture(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: {"ok": True}),
        log=lambda _m: None,
    )
    assert daemon.run_cycle() == "partial"
    events = read_lines(next((tmp_path / "runs").glob("*.jsonl")))
    failed = [e for e in events if e["event"] == "station"]
    assert failed and failed[0]["ok"] is False
    assert "无头模式截图失败" in failed[0]["error"]
    assert failed[0]["abort"] is None, "只失败 1 次，没到中止门槛"
    assert calls["n"] == 2, "默认重试一次（retries=1）"


def test_journal_can_be_disabled_explicitly(tmp_path):
    """关闭必须显式（哨兵），不能靠"忘了传"——失败取证是默认行为。"""
    daemon, _store = make_daemon(tmp_path, fmc_factory=SucceedingFmc,
                                 journal_dir=JOURNAL_DISABLED)
    daemon.run_cycle()
    assert not (tmp_path / "runs").exists()


def test_journal_defaults_to_next_to_the_outbox(tmp_path):
    daemon, _store = make_daemon(tmp_path, fmc_factory=SucceedingFmc)
    assert daemon.journal_dir == tmp_path / "runs", "默认就该开，且落在 outbox 旁边"


# ---------- 回溯用的元数据（时间 / 库房 / 站位 / 位置） ----------


def test_image_index_rows_carry_room_time_and_position(tmp_path):
    """每帧索引必须自带**时间 + 库房 + 站位 + 坐标**——这是之后回溯照片的钥匙。

    少了库房这一维，多库房/多批次部署时只能靠文件名或时间猜照片属于谁。
    """
    sent: list[dict] = []
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    daemon = PatrolDaemon(
        open_fmc=SucceedingFmc,
        stations=[Station(id="S101", box_id="B101", y=187.17, z=-21.2,
                          camera_ip="192.168.1.238")],
        capture_client=OkCapture(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: sent.extend(body["rows"])),
        room_state=RoomState(room_id="库房A", entry_date=date(2026, 1, 1),
                             batch_no="B-20260101-01"),
        now=_allowed_now,
        log=lambda _m: None,
    )
    assert daemon.run_cycle() == "ok"

    rows = [r for r in sent if r["kind"] == "image_index"]
    assert len(rows) == 1
    row = rows[0]
    # 时间
    assert row["ts"], "必须有采集时刻"
    # 库房维度
    assert row["room_id"] == "库房A"
    assert row["entry_date"] == "2026-01-01"
    assert row["batch_no"] == "B-20260101-01"
    # 站位与拍照位置
    assert row["station_id"] == "S101" and row["box_id"] == "B101"
    assert row["yz"] == [187.17, -21.2]
    assert row["angle_profile"] == "top45"
    # 照片去向（MinIO）
    assert row["object_name"].endswith(".jpg")

    # round 汇总行同样带库房维度
    rnd = next(r for r in sent if r["kind"] == "round")
    assert rnd["room_id"] == "库房A" and rnd["entry_date"] == "2026-01-01"


def test_journal_start_line_carries_room_identity(tmp_path):
    """取证首行也要有库房身份——排障时"这是哪间库房哪一批"是第一个要回答的问题。"""
    daemon, _store = make_daemon(tmp_path, fmc_factory=SucceedingFmc)
    daemon.run_cycle()
    start = read_lines(next((tmp_path / "runs").glob("*.jsonl")))[0]
    assert start["event"] == "start"
    assert "room_id" in start and "entry_date" in start


def test_connect_failure_produces_no_journal(tmp_path):
    """连不上控制器时本轮**没开始**，不该留下一个空的取证文件误导人。"""
    def boom():
        raise RuntimeError("连接失败")

    daemon, _store = make_daemon(tmp_path, fmc_factory=boom)
    assert daemon.run_cycle() == "failed"
    assert not (tmp_path / "runs").exists()


def test_journal_end_status_matches_the_returned_result(tmp_path):
    """取证里的结束状态必须与 run_cycle() 的返回值一致——否则现场会拿着两份说法。"""
    class AlwaysFailsCapture:
        def capture(self, *, ip, user="admin", pwd="", filename, storage="cloud"):
            from patrol.capture_client import CaptureError

            raise CaptureError("采图失败")

    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=SucceedingFmc,
        stations=[Station(id="S101", box_id="B101", y=10.0, z=-21.2,
                          camera_ip="192.168.1.238")],
        capture_client=AlwaysFailsCapture(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: {"ok": True}),
        log=lambda _m: None,
    )
    result = daemon.run_cycle()
    assert result == "partial"
    end = read_lines(next((tmp_path / "runs").glob("*.jsonl")))[-1]
    assert end["event"] == "end" and end["status"] == result


def test_consecutive_capture_failures_abort_the_round_but_still_report(tmp_path):
    """连续 3 次采图失败 ⇒ 本轮中止，但这是**跑完的一轮**（有结果），不是异常。"""
    class AlwaysFailsCapture:
        def capture(self, *, ip, user="admin", pwd="", filename, storage="cloud"):
            from patrol.capture_client import CaptureError

            raise CaptureError("采图失败")

    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    sent: list[dict] = []
    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=SucceedingFmc,
        stations=[Station(id=f"S10{i}", box_id=f"B10{i}", y=10.0 * i, z=-21.2,
                          camera_ip="192.168.1.238") for i in range(1, 6)],
        capture_client=AlwaysFailsCapture(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: sent.extend(body["rows"])),
        log=lambda _m: None,
    )
    assert daemon.run_cycle() == "partial"

    events = read_lines(next((tmp_path / "runs").glob("*.jsonl")))
    heartbeat = [e for e in events if e["event"] == "station"]
    assert len(heartbeat) == 3, "连败 3 次即停，不该把剩下 2 站也跑掉"
    assert heartbeat[-1]["abort"] == "capture"
    end = events[-1]
    assert end["status"] == "partial" and end["aborted"] is True
    # 中止也算"跑完的一轮"：round 汇总 + 3 条失败索引都进了 outbox（同步成功故已清空）
    kinds = [r["kind"] for r in sent]
    assert kinds.count("round") == 1
    assert kinds.count("image_index") == 3
    assert len(store.pending()) == 0, "同步成功后 outbox 清空（at-least-once 契约）"

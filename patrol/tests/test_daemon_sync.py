
from datetime import date, datetime

import pytest
from fmc_fakes import COMMISSIONED_SOFT_LIMITS, FakeFmcLib, commissioned_client
from patrol.daemon import PatrolDaemon
from patrol.fmc import Fmc4030, FmcError
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



class SyncClientAndCaptureStub:
    """带 transport 的 CaptureClient 替身，避免真实采图分支。"""

    def __init__(self):
        from patrol.capture_client import CaptureClient

        self._client = CaptureClient(transport=lambda url, *, params=None, body=None: {
            "success": True, "filename": params["filename"] + ".jpg",
            "cloud_url": "http://minio/x.jpg"})

    def __getattr__(self, name):
        return getattr(self._client, name)


def test_store_append_and_clear(tmp_path):
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    assert len(store) == 0
    store.append({"kind": "round", "ts": "t1"})
    store.append({"kind": "round", "ts": "t2"})
    assert len(store) == 2
    records = store.pending()
    assert records[0]["ts"] == "t1"
    store.replace([])
    assert len(store) == 0


def test_store_survives_torn_line(tmp_path):
    """评审 #5：崩溃撕裂的尾行被隔离，好数据照常同步，outbox 不再卡死。"""
    path = tmp_path / "outbox.jsonl"
    store = JsonlStore(str(path))
    store.append({"kind": "round", "ts": "t1"})
    store.append({"kind": "round", "ts": "t2"})
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "round", "ts": "t3", "ok": ')  # 模拟断电撕裂

    assert store.corrupt_lines == 0
    records = store.pending()
    assert [r["ts"] for r in records] == ["t1", "t2"]
    assert store.corrupt_lines == 1
    # 自愈后文件只剩好行，len 不再受坏行影响
    assert len(store) == 2
    assert store.corrupt_lines == 0


def test_sync_flush_success_clears_outbox(tmp_path):
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    store.append({"kind": "measurement", "ts": "t1", "box_id": "B01"})
    sent = []
    sync = SyncClient(transport=lambda url, body=None: sent.append(body["rows"]))
    assert sync.flush(store) == 1
    assert sent == [[{"kind": "measurement", "ts": "t1", "box_id": "B01"}]]
    assert len(store) == 0


def test_sync_flush_failure_keeps_records(tmp_path):
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    store.append({"kind": "measurement", "ts": "t1", "box_id": "B01"})

    def boom(url, *, params=None, body=None):
        raise RuntimeError("网络断开")

    sync = SyncClient(transport=boom)
    with pytest.raises(RuntimeError):
        sync.flush(store)
    assert len(store) == 1  # 失败保留，待补传


def test_sync_requires_transport(tmp_path):
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    store.append({"kind": "round", "ts": "t1"})
    with pytest.raises(NotImplementedError):
        SyncClient().flush(store)


def test_daemon_cycle_ok_and_sync(tmp_path):
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    sent = []
    sync = SyncClient(transport=lambda url, body=None: sent.append(body["rows"]))
    opens = []

    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=lambda: (opens.append(1), commissioned_client())[1],
        stations=[Station(id="S01", box_id="B01", y=10.0, z=0.0)],
        capture_client=SyncClientAndCaptureStub(),
        store=store,
        sync=sync,
        log=lambda msg: None,
    )
    assert daemon.run_cycle() == "ok"
    # 同步成功后 outbox 清空，记录应出现在 transport 收到的批次里
    flat = [r for batch in sent for r in batch]
    assert "round" in [r["kind"] for r in flat]
    assert opens == [1]
    assert len(store) == 0


def test_daemon_refuses_to_move_when_soft_limits_are_not_commissioned(tmp_path):
    """ADR-0008 的闸门要在**每一轮**生效，不是只在启动预检里。

    控制器自带软限位出厂 ±200mm，未整定时它会把 4.4 米的 Y 目标**截断到 200mm 并返回
    成功**——业务层完全看不出来。所以连上之后第一件事就是校验，不过就断开、本轮判失败，
    一个运动指令都不许发。
    """
    lib = FakeFmcLib()                      # 出厂默认 (200, 200)：没整定
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    logs: list[str] = []

    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=lambda: Fmc4030(lib),
        stations=[Station(id="S01", box_id="B01", y=10.0, z=0.0)],
        capture_client=SyncClientAndCaptureStub(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: None),
        reconnect_attempts=1,
        backoff_s=0.0,
        log=logs.append,
    )

    assert daemon.run_cycle() == "failed"
    assert any("软限位" in m for m in logs), "必须说清是软限位的问题"
    assert lib.calls_of("line2") == [], "不许发任何运动指令"
    assert lib.calls_of("home") == []
    assert lib.calls_of("jog") == []
    assert len(store) == 0, "没跑成就不该写结果"
    assert lib.opened is False, "校验不过要断开连接"


def test_daemon_runs_when_soft_limits_are_commissioned(tmp_path):
    """反向确认：整定过就必须放行（别把闸门做成永远关着的）。"""
    lib = FakeFmcLib(soft_limits=COMMISSIONED_SOFT_LIMITS)
    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=lambda: Fmc4030(lib),
        stations=[Station(id="S01", box_id="B01", y=10.0, z=0.0)],
        capture_client=SyncClientAndCaptureStub(),
        store=JsonlStore(str(tmp_path / "outbox.jsonl")),
        sync=SyncClient(transport=lambda url, body=None: None),
        log=lambda _m: None,
    )
    assert daemon.run_cycle() == "ok"
    assert lib.calls_of("line2"), "整定过就该真的动"


def test_daemon_reconnect_then_success(tmp_path):
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    attempts = {"n": 0}
    sleeps: list[float] = []

    def flaky_open():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("连接失败")
        return commissioned_client()

    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=flaky_open,
        stations=[],
        capture_client=SyncClientAndCaptureStub(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: None),
        backoff_s=0.0,
        sleep=sleeps.append,
        log=lambda msg: None,
    )
    assert daemon.run_cycle() == "ok"
    assert attempts["n"] == 3


def test_daemon_gives_up_after_max_attempts(tmp_path):
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))

    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=lambda: (_ for _ in ()).throw(RuntimeError("连接失败")),
        stations=[],
        capture_client=SyncClientAndCaptureStub(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: None),
        reconnect_attempts=2,
        backoff_s=0.0,
        log=lambda msg: None,
    )
    assert daemon.run_cycle() == "failed"


def _boom_fmc_daemon(tmp_path, exc):
    """造一个"回零就炸"的 daemon，返回 (daemon, [被创建的 fmc 实例])。"""
    made: list = []

    class BoomFmc(Fmc4030):
        def __init__(self):
            super().__init__(FakeFmcLib(soft_limits=COMMISSIONED_SOFT_LIMITS))
            self.stopped = False

        def home_all(self, **_kw):
            raise exc

        def stop_everything(self):
            self.stopped = True
            return []

    def open_fmc():
        fmc = BoomFmc()
        made.append(fmc)
        return fmc

    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=open_fmc,
        stations=[],  # 回零就炸，轮不到站位
        capture_client=SyncClientAndCaptureStub(),
        store=JsonlStore(str(tmp_path / "outbox.jsonl")),
        sync=SyncClient(transport=lambda url, body=None: None),
        log=lambda msg: None,
    )
    return daemon, made


def test_daemon_emergency_stops_on_motion_failure(tmp_path):
    """运动失败必须急停——任何"把轴丢在运动中"的失败路径都不可接受。"""
    daemon, made = _boom_fmc_daemon(tmp_path, FmcError("回零失败"))
    assert daemon.run_cycle() == "failed"
    assert made and made[0].stopped is True


def test_daemon_emergency_stops_on_interrupt(tmp_path):
    """Ctrl+C 同样要急停，而不是让轴继续跑完整轮。"""
    daemon, made = _boom_fmc_daemon(tmp_path, KeyboardInterrupt())
    assert daemon.run_cycle() == "failed"
    assert made and made[0].stopped is True


def test_daemon_survives_emergency_stop_itself_failing(tmp_path):
    """急停自身失败不能盖掉原始异常，也不能让 run_cycle 抛出去。"""

    class BoomFmc(Fmc4030):
        def __init__(self):
            super().__init__(FakeFmcLib(soft_limits=COMMISSIONED_SOFT_LIMITS))

        def home_all(self, **_kw):
            raise FmcError("回零失败")

        def stop_everything(self):
            raise RuntimeError("急停也炸了")

    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=BoomFmc,
        stations=[],
        capture_client=SyncClientAndCaptureStub(),
        store=JsonlStore(str(tmp_path / "outbox.jsonl")),
        sync=SyncClient(transport=lambda url, body=None: None),
        log=lambda msg: None,
    )
    assert daemon.run_cycle() == "failed"


def test_daemon_writes_image_index_rows(tmp_path):
    """ADR-0005：采图结果必须落图像索引。

    此前 `StationCapture.run` 返回的 object_name / cloud_url 在 daemon 里被**直接丢弃**，
    于是"图落在 MinIO 里，却没有任何记录能把它关联到站位与时间"——历史模式无从查起。
    """
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    sent: list = []
    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=lambda: commissioned_client(),
        stations=[Station(id="S01", box_id="B01", y=10.0, z=0.0)],
        capture_client=SyncClientAndCaptureStub(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: sent.append(body["rows"])),
        log=lambda msg: None,
    )
    assert daemon.run_cycle() == "ok"

    rows = [r for batch in sent for r in batch]  # 同步成功后 outbox 已清空，看发出去的批次
    assert {"round", "image_index"} <= {r["kind"] for r in rows}
    idx = next(r for r in rows if r["kind"] == "image_index")
    assert idx["station_id"] == "S01"
    assert idx["box_id"] == "B01"
    assert idx["ok"] is True
    assert idx["object_name"].endswith(".jpg")
    assert idx["yz"] == [10.0, 0.0]


def test_daemon_writes_failed_station_index_row(tmp_path):
    """失败的站位同样留一行索引（object_name 为空）——"这一帧没拍成"也是事实。"""
    from patrol.capture_client import CaptureError

    class AlwaysFailingCapture:
        def capture(self, **_kwargs):
            raise CaptureError("相机不在线")

    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    sent: list = []
    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=lambda: commissioned_client(),
        stations=[Station(id="S07", box_id="B07", y=10.0, z=0.0)],
        capture_client=AlwaysFailingCapture(),
        store=store,
        sync=SyncClient(transport=lambda url, body=None: sent.append(body["rows"])),
        log=lambda msg: None,
    )
    assert daemon.run_cycle() == "partial"

    rows = [r for batch in sent for r in batch]
    idx = next(r for r in rows if r["kind"] == "image_index")
    assert idx["station_id"] == "S07"
    assert idx["ok"] is False
    assert "相机不在线" in idx["error"]


def test_daemon_measure_fn_rows_synced(tmp_path):
    store = JsonlStore(str(tmp_path / "outbox.jsonl"))
    sent = []
    sync = SyncClient(transport=lambda url, body=None: sent.append(body["rows"]))

    class StubRecord:
        """模拟 measure.record.MeasurementRecord（鸭子类型满足 ToRow）。"""

        def to_row(self) -> dict:
            return {"ts": "t1", "box_id": "B01", "mean_len_mm": 50.0,
                    "mean_cap_mm": 30.0, "n": 2}

    daemon = PatrolDaemon(
        room_state=ALLOWED_ROOM,
        now=_allowed_now,
        open_fmc=lambda: commissioned_client(),
        stations=[],
        capture_client=SyncClientAndCaptureStub(),
        store=store,
        sync=sync,
        measure_fn=lambda report: [StubRecord()],
        log=lambda msg: None,
    )
    daemon.run_cycle()
    flat = [r for batch in sent for r in batch]
    kinds = sorted(r["kind"] for r in flat)
    assert kinds == ["measurement", "round"]
    row = next(r for r in flat if r["kind"] == "measurement")
    assert row["box_id"] == "B01" and row["mean_len_mm"] == 50.0

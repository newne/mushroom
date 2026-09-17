"""手动通道：会话、单飞、急停不受限。

这里钉的都是"现场会疼"的性质，尤其最后几条关于急停的——急停是唯一一条
"无论如何都要能生效"的路径，它的例外必须有测试，否则迟早被"统一处理"掉。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from deploy.manual import SESSION_TTL_S, ManualChannel

T0 = datetime(2026, 9, 14, 10, 0, 0)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def tick(self, seconds):
        self.t += timedelta(seconds=seconds)


def chan(tmp_path, clock=None) -> ManualChannel:
    return ManualChannel(str(tmp_path / "cmd"), now=clock or Clock())


# ---------- 会话 ----------


def test_motion_needs_a_session(tmp_path):
    c = chan(tmp_path)
    with pytest.raises(PermissionError, match="会话"):
        c.submit("goto", args={"y": 100.0, "z": -20.0})


def test_non_motion_does_not_need_a_session(tmp_path):
    """开灯/抓拍不动机构，不该被会话挡住。"""
    c = chan(tmp_path)
    cmd, why = c.submit("lamp", args={"on": True})
    assert cmd.kind == "lamp" and why == "已提交"


def test_session_expires_and_renewal_extends(tmp_path):
    clock = Clock()
    c = chan(tmp_path, clock)
    c.open_session("tok-1")
    assert c.active_session() is not None

    clock.tick(SESSION_TTL_S + 1)
    assert c.active_session() is None, "过了有效期就不该还是有效会话"

    c.open_session("tok-1")
    clock.tick(SESSION_TTL_S - 10)
    assert c.renew_session() is not None, "续期应当把有效期往后推"
    clock.tick(60)
    assert c.active_session() is not None, "续期后又活了"


def test_close_session_revokes(tmp_path):
    c = chan(tmp_path)
    c.open_session("tok-1")
    c.close_session()
    assert c.active_session() is None
    with pytest.raises(PermissionError):
        c.submit("jog", args={"axis": "Y", "dist": 5.0})


# ---------- 单飞 ----------


def test_only_one_command_in_flight(tmp_path):
    c = chan(tmp_path)
    c.open_session("t")
    first, _ = c.submit("goto", args={"y": 1.0, "z": 0.0})
    second, why = c.submit("jog", args={"axis": "Y", "dist": 5.0})
    assert second.id == first.id, "被拒时返回的是正在跑的那条"
    assert "还没结束" in why


def test_result_frees_the_slot(tmp_path):
    c = chan(tmp_path)
    c.open_session("t")
    cmd, _ = c.submit("goto", args={"y": 1.0, "z": 0.0})
    assert c.claim() is not None
    assert c.claim() is None, "同一条指令不能被领两次"
    c.complete(cmd, ok=True, detail="到位")
    assert c.inflight() is None
    nxt, why = c.submit("jog", args={"axis": "Y", "dist": 5.0})
    assert nxt.kind == "jog" and why == "已提交"


def test_result_is_paired_with_the_command(tmp_path):
    c = chan(tmp_path)
    c.open_session("t")
    cmd, _ = c.submit("capture", args={"station_id": "S101"})
    c.complete(cmd, ok=False, detail="相机不可用", data={"reason": "camera"})
    got = c.result()
    assert got.id == cmd.id and got.ok is False and "相机" in got.detail
    assert got.kind == "capture"
    assert got.args == {"station_id": "S101"}
    assert got.data == {"reason": "camera"}


def test_legacy_result_without_structured_fields_is_readable(tmp_path):
    c = chan(tmp_path)
    c.result_path.parent.mkdir(parents=True, exist_ok=True)
    c.result_path.write_text(
        json.dumps(
            {
                "id": "legacy-1",
                "ok": True,
                "detail": "旧结果",
                "ended_at": "2026-09-14T10:00:00",
            }
        ),
        encoding="utf-8",
    )

    got = c.result()

    assert got is not None
    assert got.id == "legacy-1" and got.ok is True
    assert got.kind == "" and got.args == {} and got.data == {}


def test_unknown_kind_is_rejected(tmp_path):
    c = chan(tmp_path)
    with pytest.raises(ValueError, match="未知指令"):
        c.submit("self_destruct")


# ---------- 急停：唯一的例外 ----------


def test_estop_works_without_a_session(tmp_path):
    """急停不要求会话——"先接管再能停"是不可接受的。"""
    c = chan(tmp_path)
    c.raise_estop(by="operator", reason="看到异响")
    assert c.raised() is True
    assert "operator" in (c.estop() or {}).get("by", "")


def test_estop_bypasses_the_single_flight_rule(tmp_path):
    """队列里还压着一条 goto 时，急停照样要能置上。"""
    c = chan(tmp_path)
    c.open_session("t")
    c.submit("goto", args={"y": 100.0, "z": -20.0})      # 占着位
    c.raise_estop(reason="急")
    assert c.raised() is True
    assert c.inflight() is not None, "急停不该把在跑的指令从队列里抹掉（执行方要看它）"


def test_estop_can_be_cleared_only_explicitly(tmp_path):
    c = chan(tmp_path)
    c.raise_estop()
    assert c.raised() is True
    c.close_session()          # 无关操作不该顺带清急停
    assert c.raised() is True
    c.clear_estop()
    assert c.raised() is False


def test_estop_allows_only_stopping_and_turning_the_lamp_off():
    """急停闩锁放行什么，只有这一处定义（console 与执行方共用）。"""
    from deploy.manual import estop_allows

    assert estop_allows("stop") is True                    # 再停一次永远可以
    assert estop_allows("lamp", {"on": False}) is True     # 关灯：要能安全靠近设备
    assert estop_allows("lamp", {"on": True}) is False
    assert estop_allows("lamp", {}) is False               # 参数缺失 = 不当作关灯
    for kind in ("goto", "jog", "home", "capture"):
        assert estop_allows(kind) is False
    assert estop_allows("lamp", None) is False


def test_broken_command_file_reads_as_nothing_in_flight(tmp_path):
    """正在写一半的 JSON（或盘写坏了）不能把执行方卡死。"""
    c = chan(tmp_path)
    c.cmd_path.parent.mkdir(parents=True, exist_ok=True)
    c.cmd_path.write_text('{"id": "x", "kind":', encoding="utf-8")
    assert c.inflight() is None
    assert c.claim() is None


# ---------- 运动中的实时位置/速度（观测通道） ----------


def test_progress_roundtrip_and_clear(tmp_path):
    """执行方写、console 读、收场清：同一份文件，坏文件同样按"没有"处理。"""
    c = chan(tmp_path)
    assert c.read_progress() is None
    c.write_progress({"id": "c-1", "kind": "goto", "ts": "2026-09-14T10:00:00",
                      "position_yz": [1105.172, 55.587], "speed_yz": [40.0, 0.0], "moving": True})
    got = c.read_progress()
    assert got is not None and got["id"] == "c-1" and got["position_yz"] == [1105.172, 55.587]
    c.clear_progress()
    assert c.read_progress() is None
    c.clear_progress()          # 幂等：已经没了也不该炸


def test_broken_progress_file_reads_as_nothing(tmp_path):
    c = chan(tmp_path)
    c.progress_path.parent.mkdir(parents=True, exist_ok=True)
    c.progress_path.write_text('{"id": "x",', encoding="utf-8")
    assert c.read_progress() is None

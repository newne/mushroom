"""票 29：操作会话与「相邻巡检轮次三联」（ADR-0028）。

三条硬约束在这里钉住，它们决定了这个视图有没有意义：

1. **±10 分钟合并**：一次调节往往连改几个点位，拆成几条就答不了"这次决定是什么"；
2. **强制跨越**：三联里必须有调前、有调后，否则"调完怎么变"无从谈起；
3. **同框**：不同框的绝对高度不可比，跨框比较会把层间差异读成长势变化。

外加一条纪律：凑不齐就**说缺**，不拿另一侧的帧顶替。
"""

from __future__ import annotations

from analysis.store import insert_images, insert_rounds, upsert_operations

from analysis import ops_session


def _op(ts: str, *, point: str = "TemSet", value: str = "21", operator: str = "张三") -> dict:
    return {
        "ts": ts, "zone": "611", "device_code": "CF01", "device_name": "冷风机",
        "point_code": point, "point_name": point, "value": value,
        "operator": operator, "source": "现场", "content": f"{point}={value}",
    }


def _round(ts: str, round_id: str, room: str = "611") -> dict:
    return {"ts": ts, "round_id": round_id, "room_id": room, "ok": 1}


def _image(ts: str, round_id: str, *, box: str = "B206", ok: int = 1,
           name: str | None = None, room: str = "611") -> dict:
    return {
        "ts": ts, "round_id": round_id, "station_id": box.replace("B", "S"),
        "box_id": box, "angle_profile": "level0", "object_name": name or f"{box}-{ts}.jpg",
        "ok": ok, "room_id": room,
    }


# ---------- 会话切分 ----------


def test_sessions_merge_chain_within_window():
    """相邻两条间隔 ≤10 分钟就并入；链式传递（10:00 → 10:05 → 10:11）算一次调节。"""
    out = ops_session.sessions([
        _op("2026-08-01T10:00:00"),
        _op("2026-08-01T10:05:00", point="HumSet", value="5"),
        _op("2026-08-01T10:11:00", point="CycOnTime", value="300"),
    ], window_min=10)
    assert len(out) == 1
    assert out[0]["n_changes"] == 3
    assert out[0]["ts_start"] == "2026-08-01T10:00:00"
    assert out[0]["ts_end"] == "2026-08-01T10:11:00"
    # 会话内部**逐条保留**（ADR-0023 的"逐条留"不变）
    assert [c["point_name"] for c in out[0]["changes"]] == ["TemSet", "HumSet", "CycOnTime"]


def test_sessions_boundary_ten_minutes_merges_eleven_splits():
    """边界值：正好 10 分钟算同一会话；11 分钟切开。"""
    same = ops_session.sessions(
        [_op("2026-08-01T10:00:00"), _op("2026-08-01T10:10:00")], window_min=10)
    assert len(same) == 1

    split = ops_session.sessions(
        [_op("2026-08-01T10:00:00"), _op("2026-08-01T10:11:00")], window_min=10)
    assert len(split) == 2


def test_sessions_keep_both_operators():
    """同一次调节里换人接着点，仍然是同一次调节，两个人名都留着。"""
    out = ops_session.sessions([
        _op("2026-08-01T10:00:00", operator="张三"),
        _op("2026-08-01T10:03:00", point="HumSet", operator="李四"),
    ], window_min=10)
    assert out[0]["operators"] == ["张三", "李四"]
    assert out[0]["operator"] == "张三、李四"


def test_sessions_ignore_unparsable_timestamps():
    """时刻解不出来的行**不入会话**（不猜它是哪一次调节）。"""
    out = ops_session.sessions([_op("not-a-time"), _op("2026-08-01T10:00:00")])
    assert len(out) == 1
    assert out[0]["n_changes"] == 1


# ---------- 相邻轮次 ----------


def test_pick_rounds_forces_crossing_and_prefers_after():
    rounds = [_round(f"2026-08-01T{h:02d}:00:00", f"r{h}") for h in (3, 6, 9, 12, 15)]
    out = ops_session.pick_rounds("2026-08-01T10:30:00", rounds, frames=3)
    assert out["crossed"] is True
    assert out["missing"] is None
    assert len(out["picked"]) == 3
    sides = [p["side"] for p in out["picked"]]
    # 1 张调前 + 2 张调后：调节之后才看得出变化
    assert sides.count("before") == 1
    assert sides.count("after") == 2
    # 升序返回，gap_h 带符号
    assert out["picked"][0]["gap_h"] < 0 < out["picked"][-1]["gap_h"]


def test_pick_rounds_drops_rounds_inside_the_adjustment():
    """调节进行中拍到的那一轮**两边都不算**。

    拿它充当"调后"，等于把调节过程本身当成结果——一次 10 分钟的连改正好撞上一轮
    巡检，概率并不低（一轮 48 站要跑十几分钟）。
    """
    rounds = [
        _round("2026-08-01T09:00:00", "r9"),
        _round("2026-08-01T10:05:00", "r10"),   # 落在 10:00–10:10 的会话窗口里
        _round("2026-08-01T12:00:00", "r12"),
    ]
    out = ops_session.pick_rounds(
        "2026-08-01T10:00:00", rounds, frames=3, end_ts="2026-08-01T10:10:00")
    assert out["during"] == 1
    assert [p["round_id"] for p in out["picked"]] == ["r9", "r12"]
    assert out["crossed"] is True


def test_pick_rounds_reports_missing_after_instead_of_substituting():
    """批次末尾没有后续轮次 ⇒ 如实标缺，**不拿调节前的帧充数**。"""
    rounds = [_round(f"2026-08-01T{h:02d}:00:00", f"r{h}") for h in (3, 6)]
    out = ops_session.pick_rounds("2026-08-02T10:00:00", rounds, frames=3)
    assert out["crossed"] is False
    assert out["missing"] == "after"
    assert all(p["side"] == "before" for p in out["picked"])
    assert len(out["picked"]) == 2      # 名额用不满就是不满，不编


def test_pick_rounds_missing_before_when_batch_just_started():
    rounds = [_round("2026-08-01T12:00:00", "r12"), _round("2026-08-01T15:00:00", "r15")]
    out = ops_session.pick_rounds("2026-08-01T09:00:00", rounds, frames=3)
    assert out["missing"] == "before"
    assert all(p["side"] == "after" for p in out["picked"])


# ---------- 帧选取 ----------


def test_pick_frame_skips_failed_frames():
    """失败帧不是"长势不变"，是"这次没拍到"——不入选。"""
    frames = [
        {"ts": "2026-08-01T09:00:00", "ok": 0, "object_name": "bad.jpg"},
        {"ts": "2026-08-01T09:30:00", "ok": 1, "object_name": "good.jpg"},
    ]
    assert ops_session.pick_frame(frames, "2026-08-01T09:05:00")["object_name"] == "good.jpg"


def test_pick_frame_prefers_non_probe_even_if_farther():
    """同轮里正常帧优先于重试帧（probe），即便 probe 更靠近目标时刻。"""
    frames = [
        {"ts": "2026-08-01T09:36:00", "ok": 1, "object_name": "B206-probe1.jpg"},
        {"ts": "2026-08-01T09:40:00", "ok": 1, "object_name": "B206.jpg"},
    ]
    assert ops_session.pick_frame(frames, "2026-08-01T09:35:00")["object_name"] == "B206.jpg"


def test_pick_frame_returns_none_when_all_failed():
    assert ops_session.pick_frame(
        [{"ts": "2026-08-01T09:00:00", "ok": 0, "object_name": "x.jpg"}], "2026-08-01T09:00:00"
    ) is None


def test_attach_frames_keeps_round_without_frame():
    """轮次在、帧不在 ⇒ 保留并标 `has_frame=False`：那是缺口，不是"这一轮不存在"。"""
    picked = [
        {"ts": "2026-08-01T09:00:00", "round_id": "r9", "side": "before", "gap_h": -1.5},
        {"ts": "2026-08-01T12:00:00", "round_id": "r12", "side": "after", "gap_h": 1.5},
    ]
    out = ops_session.attach_frames(
        picked, {"r12": [_image("2026-08-01T12:01:00", "r12")]},
        target_ts="2026-08-01T10:30:00",
    )
    assert out[0]["has_frame"] is False
    assert out[1]["has_frame"] is True
    assert out[1]["image"]["box_id"] == "B206"


# ---------- 接口 ----------


def test_endpoint_requires_zone(client):
    assert client.get("/operations/sessions").status_code == 400


def test_endpoint_no_operations_is_its_own_state(client):
    """"没人动过"与"取不到"必须分得开：前者本身就是一个有用的结论。"""
    body = client.get("/operations/sessions?zone=611").json()
    assert body["state"] == "no_operations"
    assert body["sessions"] == []


def test_endpoint_builds_triptych_on_one_box(client, pg):
    insert_rounds(pg, [
        _round("2026-08-01T09:00:00", "r9"),
        _round("2026-08-01T12:00:00", "r12"),
        _round("2026-08-01T15:00:00", "r15"),
    ])
    insert_images(pg, [
        _image("2026-08-01T09:01:00", "r9"),
        _image("2026-08-01T12:01:00", "r12"),
        _image("2026-08-01T15:01:00", "r15"),
    ])
    upsert_operations(pg, [_op("2026-08-01T10:30:00")])

    body = client.get(
        "/operations/sessions?zone=611&box_id=B206&entry_date=2026-07-20"
    ).json()
    assert body["state"] == "ok"
    session = body["sessions"][0]
    assert session["n_changes"] == 1
    assert session["growth_day"] == 12
    assert session["crossed"] is True
    assert len(session["frames"]) == 3
    # 同框是硬约束
    assert {f["image"]["box_id"] for f in session["frames"] if f["has_frame"]} == {"B206"}
    # 调前 1 + 调后 2
    assert [f["side"] for f in session["frames"]] == ["before", "after", "after"]
    assert all(f["image"]["object_name"] for f in session["frames"])


def test_endpoint_notes_when_no_rounds(client, pg):
    """没有巡检轮次 ⇒ 会话照样列出来，但三联是空的并说明原因（不是"没人调过"）。"""
    upsert_operations(pg, [_op("2026-08-01T10:30:00")])
    body = client.get("/operations/sessions?zone=611").json()
    assert body["state"] == "no_rounds"
    session = body["sessions"][0]
    assert session["frames"] == []
    assert session["note"] == "没有可用的巡检轮次"

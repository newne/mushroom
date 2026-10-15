"""库房维度必须活着穿过 `/ingest`。

背景（2026-09-13 审计发现）：`patrol.daemon` 给每条 `round` / `image_index` 行都盖了
`room_id`/`entry_date`/`batch_no`，但 `images`/`rounds` 表里没有这三列，
`insert_images` 又只按 `IMAGE_FIELDS` 取值 —— 于是这三个字段在 `/ingest` 这一跳
**被静默丢掉**，没人报错。照片在 MinIO 里、索引在库里，却说不清属于哪间库房。

这类"写入方多给、存储方少要"的字段丢失不会自己暴露，所以每一条都要有用例钉住。
"""

from __future__ import annotations

import pytest

ROOM = {"room_id": "611", "entry_date": "2026-03-16", "batch_no": "mogu-100"}


def image_row(**kw) -> dict:
    row = {"kind": "image_index", "ts": "2026-09-13T20:25:42", "ok": True,
           "box_id": "B101", "station_id": "S101", "angle_profile": "top45",
           "imaging_profile": "img-2026-10-13a",
           "object_name_source": "service",
           "camera_ip": "192.168.1.238", "yz": [187.167, -21.2],
           "object_name": "20260913/B101_S101_top45_202542",
           "cloud_url": "http://192.168.1.250:9000/mogu/20260913/B101_S101_top45_202542.jpg",
           "elapsed_s": 13.1, **ROOM}
    row.update(kw)
    return row


def round_row(**kw) -> dict:
    row = {"kind": "round", "ts": "2026-09-13T20:35:15", "ok": False, "n_results": 59,
           "n_failures": 1, "aborted": False, **ROOM}
    row.update(kw)
    return row


# ---------- 库房维度端到端 ----------


def test_image_rows_keep_the_room_stamp(client):
    with client as c:
        assert c.post("/ingest", json={"rows": [image_row()]}).status_code == 200
        rows = c.get("/images").json()
    assert len(rows) == 1
    assert rows[0]["room_id"] == "611"
    assert rows[0]["entry_date"] == "2026-03-16"
    assert rows[0]["batch_no"] == "mogu-100"
    assert rows[0]["object_name"] == "20260913/B101_S101_top45_202542"
    # 成像参数快照 id（巡检手票 11）：每帧带自己的取证归属，不靠"时间×站位"反推
    assert rows[0]["imaging_profile"] == "img-2026-10-13a"
    # object_name 的来源（service=服务返回 / request=请求名兜底）：兜底要留痕，别静默
    assert rows[0]["object_name_source"] == "service"


def test_round_rows_keep_the_room_stamp(client):
    with client as c:
        assert c.post("/ingest", json={"rows": [round_row()]}).status_code == 200
        rows = c.get("/rounds").json()
    assert rows[0]["room_id"] == "611"
    assert rows[0]["entry_date"] == "2026-03-16"
    assert rows[0]["n_failures"] == 1


def test_mixed_batch_ingests_every_kind(client):
    """真实 outbox 就是混着三种 kind 的一批（sync.py 全批一个请求）。"""
    rows = [round_row(), image_row(),
            image_row(station_id="S102", box_id="B102", ts="2026-09-13T20:26:02",
                      object_name="20260913/B102_S102_top45_202602"),
            {"kind": "measurement", "ts": "2026-09-13T20:35:15", "room_id": "611", "box_id": "B101",
             "n": 6, "mean_len_mm": 42.5, "mean_cap_mm": 31.0, "p10_len": 38.0,
             "p90_len": 47.0, "quality": ""}]
    with client as c:
        got = c.post("/ingest", json={"rows": rows}).json()
        assert got == {"inserted_measurements": 1, "skipped_measurements": 0,
                       "inserted_rounds": 1, "inserted_images": 2, "warnings": []}
        assert c.get("/images").json()[0]["room_id"] == "611"
        assert c.get("/rounds").json()[0]["room_id"] == "611"
        assert c.get("/measurements").json()[0]["mean_len_mm"] == 42.5


def test_failed_frame_keeps_room_and_position(client):
    """失败帧没有 object_name，但库房与"停在哪"这两件事必须留下。"""
    bad = image_row(ok=False, object_name=None, cloud_url=None,
                    error="controller did not finish the move")
    bad.pop("kind")
    with client as c:
        c.post("/ingest", json={"rows": [{"kind": "image_index", **bad}]})
        row = c.get("/images").json()[0]
    assert row["ok"] == 0
    assert row["object_name"] is None
    assert row["room_id"] == "611"
    assert row["y"] == pytest.approx(187.167)


def test_images_can_be_filtered_by_room_and_box(client):
    rows = [image_row(),
            image_row(room_id="612", station_id="S201", box_id="B201",
                      ts="2026-09-13T20:27:02")]
    with client as c:
        c.post("/ingest", json={"rows": rows})
        assert len(c.get("/images?room_id=611").json()) == 1
        assert len(c.get("/images?room_id=612").json()) == 1
        assert len(c.get("/images?box_id=B201").json()) == 1
        assert len(c.get("/images?limit=1").json()) == 1
        assert len(c.get("/images").json()) == 2


# ---------- 票 13 §3.2：新字段与"静默忽略"护栏 ----------


def test_dup_groups_round_trips_as_objects_not_strings(client):
    """`dup_groups` 的元素是**对象**（生产者 `patrol/daemon.py:344`），不是字符串。

    夹具用真形状（含 `n` 与 `stations`）：这样生产者/消费者一旦漂移，这条测试会红 ——
    早先的夹具用 `["a1b2c3d4"]`，会把"元素是字符串"这个错误印象固化成契约。
    """
    groups = [{"sha256_12": "a1b2c3d4e5f6", "n": 35,
               "stations": ["S211", "S212", "S301"]}]
    row = round_row(room_id="611", suspect_duplicate=True, dup_groups=groups,
                    n_duplicate=35, n_frames_recorded=48, content_check_error=None)
    with client as c:
        assert c.post("/ingest", json={"rows": [row]}).status_code == 200
        got = c.get("/rounds").json()[0]
    assert isinstance(got["dup_groups"], list), "读回来必须是列表（不是 JSON 字符串）"
    assert got["dup_groups"] == groups, "元素必须还是那三个键的对象"
    assert set(got["dup_groups"][0]) == {"sha256_12", "n", "stations"}
    assert got["suspect_duplicate"] in (1, True)
    assert got["n_duplicate"] == 35
    assert got["n_frames_recorded"] == 48


def test_no_duplicates_round_has_null_groups(client):
    """无重复时生产者不发这两个键 ⇒ 库里 NULL（不是 `[]`、更不是 0）。"""
    with client as c:
        c.post("/ingest", json={"rows": [round_row(room_id="611")]})
        got = c.get("/rounds").json()[0]
    assert got["dup_groups"] is None
    assert got["suspect_duplicate"] is None


def test_frame_level_dup_columns_land(client):
    rows = [image_row(frame_dup=1, dup_group="a1b2c3d4", content_sha256="a" * 64,
                      same_as_previous=1, duplicate_streak=35, elapsed_s=13.1)]
    with client as c:
        c.post("/ingest", json={"rows": rows})
        got = c.get("/images").json()[0]
    assert got["frame_dup"] == 1
    assert got["dup_group"] == "a1b2c3d4"
    assert got["same_as_previous"] == 1
    assert got["duplicate_streak"] == 35
    assert got["elapsed_s"] == pytest.approx(13.1)


def test_unknown_key_is_reported_not_swallowed(client):
    """发出去没有列的键**必须**在响应里点名：静默忽略是这类错误唯一的藏身处。"""
    row = image_row()
    row["brand_new_field"] = "2026"
    with client as c:
        got = c.post("/ingest", json={"rows": [row]}).json()
    assert got["warnings"] == ["image_index.brand_new_field 没有对应列，**会被忽略**（先加列再发字段）"]
    assert got["inserted_images"] == 1, "警告归警告，别的字段照常落库"


def test_clean_batch_has_no_warnings(client):
    with client as c:
        got = c.post("/ingest", json={"rows": [image_row(), round_row()]}).json()
    assert got["warnings"] == []


def test_rounds_and_images_endpoints_expose_the_integrity_fields(client):
    """出口也要有（只增不改）：库里能查、**接口能出**、页面才能标。

    `dup_groups` 必须是 **list**（不是 JSON 字符串）——否则界面拿到一坨文本没法用。
    """
    rnd = round_row(room_id="611", suspect_duplicate=True,
                    dup_groups=[{"sha256_12": "a1b2c3d4e5f6", "n": 35,
                                 "stations": ["S211"]}],
                    n_duplicate=35, n_frames_recorded=48,
                    content_check_error=None)
    img = image_row(frame_dup=True, dup_group="a1b2c3d4e5f6", content_sha256="b" * 64,
                    same_as_previous=True, duplicate_streak=35)
    with client as c:
        c.post("/ingest", json={"rows": [rnd, img]})
        round_out = c.get("/rounds").json()[0]
        image_out = c.get("/images").json()[0]

    assert round_out["suspect_duplicate"] in (1, True)
    assert round_out["dup_groups"] == [{"sha256_12": "a1b2c3d4e5f6", "n": 35,
                                       "stations": ["S211"]}]
    assert set(round_out["dup_groups"][0]) == {"sha256_12", "n", "stations"}
    assert round_out["n_duplicate"] == 35
    assert round_out["n_frames_recorded"] == 48
    assert round_out["content_check_error"] is None

    assert image_out["frame_dup"] in (1, True)
    assert image_out["same_as_previous"] in (1, True)
    assert image_out["duplicate_streak"] == 35
    assert image_out["dup_group"] == "a1b2c3d4e5f6"
    assert image_out["content_sha256"] == "b" * 64


# ---------- 重复上报：唯一键挡插重（2026-10-15 真数据彩排抓到的那条） ----------


def test_reposting_the_same_failed_frame_updates_instead_of_inserting(client):
    """patrol 重投同一「采图失败」帧（`angle_profile` 缺测 ⇒ NULL）⇒ 覆盖写，不是插新行。

    实时链路（`POST /ingest` → `store.insert_images`）与一次性搬迁走的是**同一套 upsert**。
    唯一键若是普通 `UNIQUE`，NULL 互不相等 ⇒ `ON CONFLICT (ts, station_id, angle_profile)`
    永远匹配不上 ⇒ 每重投一次多一行（真数据彩排第二遍多出来的 10 行就是这么来的）。
    预期轴：重投 N 次，`/images` 里还是一行、内容是最新那次。
    """
    failed = image_row(angle_profile=None, object_name=None, ok=False,
                       error="采图链路失败: refused")
    with client as c:
        assert c.post("/ingest", json={"rows": [failed]}).json()["inserted_images"] == 1
        again = c.post("/ingest", json={"rows": [failed | {"error": "refused again"}]})
        assert again.json()["inserted_images"] == 1
        imgs = c.get("/images", params={"station_id": "S101"}).json()
    assert len(imgs) == 1, "同一失败帧重投两次只许留一行"
    assert imgs[0]["angle_profile"] is None, "失败帧照样入库（唯一键不许变成 NOT NULL）"
    assert imgs[0]["error"] == "refused again", "第二次是覆盖写，不是插重"

"""`GET /timeline`（票 03）：**本批全部站位**的逐轮覆盖矩阵，一次拿回。

票 02 只给一个站位。现场要的是"这一批 48 个节点各自怎么走、哪片区域断了"，
所以端点扩成矩阵：**一次请求**返回「每站位 × 每轮」的格子，页面不再发 48 次请求。

三条硬规则（spec）在矩阵形态下同样成立：

1. **x 永远是真实时间戳**：矩阵带一条**共享时间轴** `rounds[]`（本批逐轮，带 `ts`），
   节点的 `points[i]` 与 `rounds[i]` 一一对应。逐点重复 6144 次 `ts` / `round_id`
   会把单次响应抬到约 690 KB —— 那正是票 03 验收 4（≤500 KB）不允许的。
2. **`null ≠ 0`**：这一轮这一站没有帧 ⇒ `has_frame=False` 且 `n_frames=None`。
3. **零前端依赖**：端点只给数据形状（本条在页面上）。

布局：`nodes[]` 带 `layer` / `col` / `box_id`，页面按**与平面图同一套** col/layer 顺序
排成小倍数矩阵；格数由 `grid.cols × grid.layers` 决定，**不是死写 48**。
"""

from __future__ import annotations

import datetime as dt
import json
import uuid

from analysis import store


def rnd(ts, round_id, *, room="611", entry="2026-09-30", batch_no="611_20260930", **kw):
    """一轮巡检（矩阵用例只用得到这几列，其余按 NULL 入库）。"""
    return {"ts": ts, "round_id": round_id, "room_id": room, "entry_date": entry,
            "batch_no": batch_no, "ok": True, "n_results": 48, "n_failures": 0, **kw}


def frame(ts, round_id, station_id, *, room="611", box=None, entry="2026-09-30",
          batch_no="611_20260930", **kw):
    """一帧（图像索引一帧一行，ADR-0005）。框号默认按站位 id 推（B{层}{框:02d}）。"""
    return {"ts": ts, "round_id": round_id, "station_id": station_id,
            "box_id": box or ("B" + station_id[1:]), "room_id": room, "entry_date": entry,
            "batch_no": batch_no, "angle_profile": "level0", "ok": True,
            "object_name": f"{round_id}/{station_id}_{ts}", **kw}


#: 本批三轮：两个站位两条轨迹，其中 S102 在 10-02 那一轮**一张都没有**（缺口）。
ROUNDS = [
    rnd("2026-09-30T10:00:00", "r-0930"),
    rnd("2026-10-01T10:00:00", "r-1001"),
    rnd("2026-10-02T10:00:00", "r-1002"),
]
FRAMES = [
    frame("2026-09-30T10:00:05", "r-0930", "S102"),
    frame("2026-09-30T10:00:09", "r-0930", "S102"),
    frame("2026-09-30T10:00:06", "r-0930", "S201"),
    frame("2026-10-01T10:00:05", "r-1001", "S102"),
    # 2026-10-02：这一轮巡检到了，S102 却一张都没有 —— 缺即缺
    frame("2026-10-02T10:00:07", "r-1002", "S201"),
    frame("2026-10-02T10:00:08", "r-1002", "S201"),
]


def seed(conn) -> None:
    store.insert_rounds(conn, ROUNDS)
    store.insert_images(conn, FRAMES)


def matrix(client, **params):
    """矩阵形态 = **不给 station_id**（给了就是票 02 的单站位轨迹）。"""
    base = {"zone": "611", "metric": "coverage", "batch_id": "611|2026-09-30"}
    return client.get("/timeline", params={**base, **params})


def test_matrix_gives_every_station_the_whole_batch_round_by_round(client, pg):
    seed(pg)
    got = matrix(client).json()

    assert got["ok"] is True and got["metric"] == "coverage"
    assert got["station_id"] is None, "矩阵形态没有'某一个站位'"
    assert got["batch"] == {"room_id": "611", "entry_date": "2026-09-30",
                            "batch_no": "611_20260930"}

    assert got["axis"] == {"t0": "2026-09-30T10:00:00", "t1": "2026-10-02T10:00:00",
                           "n_rounds": 3}
    rounds = got["rounds"]
    assert [r["ts"] for r in rounds] == [
        "2026-09-30T10:00:00", "2026-10-01T10:00:00", "2026-10-02T10:00:00"]
    assert [r["round_id"] for r in rounds] == ["r-0930", "r-1001", "r-1002"]
    assert [r["day"] for r in rounds] == [0, 1, 2], "生长天数由 entry_date 派生（入库当天 = 0）"

    nodes = {n["node_id"]: n for n in got["nodes"]}
    assert set(nodes) == {"S102", "S201"}, "本批有帧的站位各一行"
    s102 = nodes["S102"]
    assert (s102["layer"], s102["col"], s102["box_id"]) == (1, 2, "B102")
    assert (nodes["S201"]["layer"], nodes["S201"]["col"]) == (2, 1)
    assert [p["has_frame"] for p in s102["points"]] == [True, True, False]
    assert [p["n_frames"] for p in s102["points"]] == [2, 1, None], \
        "缺帧是 None —— **不是 0**（spec 硬规则 2）"
    assert [p["n_frames"] for p in nodes["S201"]["points"]] == [1, None, 2]
    assert all(len(n["points"]) == len(rounds) for n in got["nodes"]), \
        "每站的 points 与共享时间轴 rounds 一一对应"
    # 逐点不再重复 ts/round_id/day：那是 6144 次重复，正是体积预算不允许的
    assert json.dumps(got, ensure_ascii=False).count('"ts"') == len(rounds)


def test_matrix_orders_nodes_like_the_floor_plan(client, pg):
    """布局与平面图**同一套 col/layer 顺序**：第 1 层在最上、层内按 col 从左到右。

    入库顺序故意打乱（S412 → S102 → S201）：顺序必须是**排出来的**，不是"库表碰巧"。
    """
    store.insert_rounds(pg, ROUNDS)
    store.insert_images(pg, [
        frame("2026-09-30T10:00:05", "r-0930", "S412"),
        frame("2026-09-30T10:00:06", "r-0930", "S102"),
        frame("2026-09-30T10:00:07", "r-0930", "S201"),
    ])
    got = matrix(client).json()
    assert [(n["node_id"], n["layer"], n["col"]) for n in got["nodes"]] == [
        ("S102", 1, 2), ("S201", 2, 1), ("S412", 4, 12)]


def test_matrix_layer_and_col_come_from_the_station_id_and_are_never_guessed(client, pg):
    """层/框来自站位 id 的编码（patrol.stations.build_grid：`S{层}{框:02d}`）。

    解析不出来的 id ⇒ `layer`/`col` 是 **null**：宁可让页面画成"不知道在哪一格"，
    也不塞进一个**猜出来的**格子里（那会让人照着错的格子去找）。
    """
    store.insert_rounds(pg, ROUNDS[:1])
    store.insert_images(pg, [
        frame("2026-09-30T10:00:05", "r-0930", "S102", box="B102"),
        frame("2026-09-30T10:00:06", "r-0930", "old-7", box="bench"),
    ])
    nodes = {n["node_id"]: n for n in matrix(client).json()["nodes"]}
    assert nodes["old-7"]["layer"] is None and nodes["old-7"]["col"] is None
    assert nodes["old-7"]["box_id"] == "bench"


def test_matrix_is_scoped_to_one_batch_and_one_room(client, pg):
    """别批、别间的站位/帧不许混进来（矩阵是"这一批这一间"的）。"""
    store.insert_rounds(pg, ROUNDS + [
        rnd("2026-09-20T10:00:00", "r-prev", entry="2026-09-12", batch_no="611_20260912"),
        rnd("2026-10-02T11:00:00", "r-607", room="607", batch_no="607_20260930"),
    ])
    store.insert_images(pg, FRAMES + [
        frame("2026-09-20T10:00:05", "r-prev", "S310", box="B310", entry="2026-09-12"),
        frame("2026-10-02T11:00:05", "r-607", "S102", box="B607", room="607"),
    ])
    got = matrix(client).json()
    assert [n["node_id"] for n in got["nodes"]] == ["S102", "S201"]
    assert got["nodes"][0]["box_id"] == "B102", "别间的同号站位不算到本间头上"


def test_matrix_says_so_when_no_station_has_a_frame(client, pg):
    """批次对、有轮次、一张帧都没有 ⇒ 空矩阵 + 一句人话；时间轴仍在（**不拿 0 凑站位**）。"""
    store.insert_rounds(pg, ROUNDS)
    got = matrix(client).json()
    assert got["nodes"] == []
    assert got["axis"] == {"t0": "2026-09-30T10:00:00", "t1": "2026-10-02T10:00:00",
                           "n_rounds": 3}
    assert got["notes"] == [
        "本批（611 2026-09-30）一轮都没有帧：没有站位能排进矩阵——**不拿空站位凑数**"]


def test_matrix_says_there_are_no_rounds_instead_of_faking_a_window(client, pg):
    """批次对但一轮都没有 ⇒ 空网格 + 一句人话；`t0/t1` 是 null（**不拿今天凑窗口**）。"""
    got = matrix(client).json()
    assert got["ok"] is True and got["nodes"] == [] and got["rounds"] == []
    assert got["axis"] == {"t0": None, "t1": None, "n_rounds": 0}
    assert got["notes"] == ["本批（611 2026-09-30）还没有巡检轮次：轨迹要等这一批有轮次之后才有内容"]


def test_matrix_excludes_rounds_without_a_round_id_and_counts_them(client, pg):
    """没有 round_id 的旧轮次**不进网格**，但在 `notes` 里点名+计数（不许悄悄消失）。"""
    store.insert_rounds(pg, ROUNDS + [
        {"ts": "2026-10-03T10:00:00", "round_id": None, "room_id": "611",
         "entry_date": "2026-09-30", "batch_no": "611_20260930", "ok": True},
    ])
    store.insert_images(pg, FRAMES)
    got = matrix(client).json()
    assert [r["round_id"] for r in got["rounds"]] == ["r-0930", "r-1001", "r-1002"]
    assert got["axis"]["t1"] == "2026-10-02T10:00:00"
    assert got["notes"] == [
        "1 轮没有 round_id（旧轮次）：帧挂不到它头上，未进网格——**不猜**"]


def test_matrix_stays_under_the_size_budget(client, pg):
    """验收 4：48 站 × 本批天数（16）× 每天 8 轮 ⇒ 单次响应 ≤ 500 KB。

    `round_id` 用**生产同款**（`uuid4().hex`，32 位）：拿短 id 去测体积就等于没测
    —— 真机上每个格子都要多带 27 个字节 × 6144 格。
    """
    cols, layers, days, per_day = 12, 4, 16, 8
    rounds, frames = [], []
    for day in range(days):
        date = (dt.date(2026, 9, 30) + dt.timedelta(days=day)).isoformat()
        for hour in range(per_day):
            ts = f"{date}T{8 + hour:02d}:00:00"
            round_id = uuid.uuid4().hex
            rounds.append(rnd(ts, round_id))
            for layer in range(1, layers + 1):
                for col in range(1, cols + 1):
                    frames.append(frame(ts, round_id, f"S{layer}{col:02d}"))
    store.insert_rounds(pg, rounds)
    store.insert_images(pg, frames)

    resp = matrix(client)
    assert resp.status_code == 200
    got = resp.json()
    assert len(got["nodes"]) == cols * layers
    assert all(len(n["points"]) == days * per_day for n in got["nodes"])
    assert len(resp.content) < 500 * 1024, f"{len(resp.content)} 字节 > 500 KB"


def _counts(catalog, schema: str) -> dict:
    out = {}
    for table in ("rounds", "images", "batches", "batches_state", "measurements",
                  "environment", "operations"):
        catalog.execute(f'SELECT count(*) FROM "{schema}".{table}')
        out[table] = catalog.fetchone()[0]
    return out


def test_matrix_is_pure_read_and_never_touches_tdengine(client, pg, pg_schema, catalog,
                                                        monkeypatch):
    """验收：矩阵也是**纯读**端点（表行数一行不变），且**不碰 TDengine**（ADR-0026）。"""
    import analysis.env.tdengine as tdengine

    class Boom:
        def __init__(self, *args, **kwargs):
            raise AssertionError("/timeline 不许碰 TDengine（ADR-0026）")

    monkeypatch.setattr(tdengine, "TdengineEnvSource", Boom)
    seed(pg)
    before = _counts(catalog, pg_schema)
    assert matrix(client).status_code == 200
    assert _counts(catalog, pg_schema) == before, "读端点不许写库"


def test_matrix_still_refuses_growth_and_unknown_rooms(client, pg):
    """口径面不变：metric 只认 coverage；库房号要规范；batch_id 的库房要与 zone 同间。"""
    seed(pg)
    assert matrix(client, metric="growth").status_code == 400
    bad = matrix(client, zone="999")
    assert bad.status_code == 400 and "不是规范库房号" in bad.json()["detail"]
    mismatch = matrix(client, batch_id="607|2026-09-30")
    assert mismatch.status_code == 400 and "不是同一间" in mismatch.json()["detail"]

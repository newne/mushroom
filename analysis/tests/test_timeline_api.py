"""`GET /timeline?metric=coverage`（票 02）：单站位 × 本批逐轮的巡检覆盖轨迹。

与端点有关的两条硬规则（spec「三条硬规则」）：

① 格点带**真实时间戳** `ts`——页面按它定位 x（禁止数组下标当 x）；
② `null ≠ 0`：这一轮这个站位没有帧 ⇒ `has_frame=false` 且 `n_frames` 是 **None**，
   **不是 0**。"没拍到"不许长得像"拍到 0 张"。

取数只有两条（**纯读**）：`rounds` 给逐轮、`images` 按 `round_id`+`station_id` 数帧；
批次过滤直接用 `entry_date`（ADR-0020 的身份），不用"今天减入库日"这类推算。
"""

from __future__ import annotations

import datetime as dt
import json

from analysis import store


def rnd(ts, round_id, *, room="611", entry="2026-09-30", batch_no="611_20260930", **kw):
    """一轮巡检（票 02 只用得到这几列，其余按 NULL 入库）。"""
    return {"ts": ts, "round_id": round_id, "room_id": room, "entry_date": entry,
            "batch_no": batch_no, "ok": True, "n_results": 48, "n_failures": 0, **kw}


def frame(ts, round_id, station_id, *, room="611", box="B102", entry="2026-09-30",
          batch_no="611_20260930", **kw):
    """一帧（图像索引一帧一行，ADR-0005）。"""
    return {"ts": ts, "round_id": round_id, "station_id": station_id, "box_id": box,
            "room_id": room, "entry_date": entry, "batch_no": batch_no,
            "angle_profile": "top45", "ok": True,
            "object_name": f"{round_id}/{station_id}_{ts}", **kw}


#: 本批四轮：三天有帧、中间 10-02 那一轮**这个站位一张都没有**（缺口）。
ROUNDS = [
    rnd("2026-09-30T10:00:00", "r-0930"),
    rnd("2026-10-01T10:00:00", "r-1001"),
    rnd("2026-10-02T10:00:00", "r-1002"),
    rnd("2026-10-03T10:00:00", "r-1003"),
]
FRAMES = [
    frame("2026-09-30T10:00:05", "r-0930", "S102"),
    frame("2026-09-30T10:00:09", "r-0930", "S102"),
    # 同一个轮次里的**别的站位**：不是这一站的帧，不许算进来
    frame("2026-09-30T10:00:07", "r-0930", "S101", box="B101"),
    frame("2026-10-01T10:00:05", "r-1001", "S102"),
    # 2026-10-02：这一轮巡检到了，S102 却一张都没有 —— 缺即缺
    frame("2026-10-03T10:00:05", "r-1003", "S102"),
    frame("2026-10-03T10:00:06", "r-1003", "S102"),
    frame("2026-10-03T10:00:07", "r-1003", "S102"),
]


def seed(conn) -> None:
    store.insert_rounds(conn, ROUNDS)
    store.insert_images(conn, FRAMES)


def timeline(client, **params):
    base = {"zone": "611", "station_id": "S102", "metric": "coverage",
            "batch_id": "611|2026-09-30"}
    return client.get("/timeline", params={**base, **params})


def test_timeline_gives_one_station_the_whole_batch_round_by_round(client, pg):
    seed(pg)
    got = timeline(client).json()

    assert got["ok"] is True
    assert got["metric"] == "coverage"
    assert got["zone"] == "611" and got["station_id"] == "S102"
    assert got["batch"]["entry_date"] == "2026-09-30"

    assert len(got["nodes"]) == 1, "本票只支持单站位查询"
    node = got["nodes"][0]
    assert node["node_id"] == "S102"
    assert node["box_id"] == "B102", "框号从这一站自己的帧上带回来（对接既有生长曲线卡）"

    pts = node["points"]
    assert [p["ts"] for p in pts] == [
        "2026-09-30T10:00:00", "2026-10-01T10:00:00",
        "2026-10-02T10:00:00", "2026-10-03T10:00:00",
    ], "逐轮一格，按真实时间升序"
    assert [p["round_id"] for p in pts] == ["r-0930", "r-1001", "r-1002", "r-1003"]
    assert [p["day"] for p in pts] == [0, 1, 2, 3], "生长天数由 entry_date 派生（入库当天 = 0）"
    assert [p["has_frame"] for p in pts] == [True, True, False, True]
    assert [p["n_frames"] for p in pts] == [2, 1, None, 3], \
        "缺帧是 None —— **不是 0**（spec 硬规则 2）"
    assert got["axis"]["t0"] == "2026-09-30T10:00:00"
    assert got["axis"]["t1"] == "2026-10-03T10:00:00"
    assert got["axis"]["n_rounds"] == 4
    assert json.dumps(got, ensure_ascii=False).count('"ts"') == 4, "端点不产出任何下标序号"

def test_timeline_is_scoped_to_one_batch_and_one_room(client, pg):
    """批次过滤用 `"entry_date`（ADR-0020 的身份）：别批、别间的轮次不许混进来。
    另一间/另一批的**帧**也不许算进来（哪怕 `"round_id` 撞了）。"""
    # `rounds.ts` 是主键（一轮一个时刻）：别批/别间的轮次要各占一个不同的时刻，
    # 同刻写入是**覆盖**，不是新增（这里踩过一次：三条同刻的轮次最后只剩一条）。
    store.insert_rounds(pg, ROUNDS + [
        rnd("2026-09-20T10:00:00", "r-prev", entry="2026-09-12", batch_no="611_20260912"),
        rnd("2026-10-02T11:00:00", "r-607", room="607", entry="2026-09-30",
            batch_no="607_20260930"),
    ])
    store.insert_images(pg, FRAMES + [
        frame("2026-10-02T11:00:05", "r-607", "S102", room="607", box="B607"),
    ])

    got = timeline(client).json()
    pts = got["nodes"][0]["points"]
    assert [p["round_id"] for p in pts] == ["r-0930", "r-1001", "r-1002", "r-1003"]
    assert pts[2]["n_frames"] is None, "别间的同号站位有帧，也不能算到本间头上"
    assert got["nodes"][0]["box_id"] == "B102"


def test_timeline_defaults_to_the_current_batch_from_the_ledger(client, pg):
    """不给批次时取**台账里最新的一批**（`"/batches` 的当前批"），不用"今天减入库日"推算。"""
    store.upsert_batches(pg, [
        {"room_id": "611", "entry_date": "2026-09-12", "batch_no": "611_20260912",
         "batch_id": "611|2026-09-12"},
        {"room_id": "611", "entry_date": "2026-09-30", "batch_no": "611_20260930",
         "batch_id": "611|2026-09-30"},
    ])
    seed(pg)

    got = timeline(client, batch_id="").json()
    assert got["batch"] == {"room_id": "611", "entry_date": "2026-09-30",
                            "batch_no": "611_20260930"}, "当前批 = 台账最新一批"
    assert [p["round_id"] for p in got["nodes"][0]["points"]] == [
        "r-0930", "r-1001", "r-1002", "r-1003"]


def test_timeline_says_so_when_the_ledger_has_no_batch_for_this_room(client, pg):
    """台账里没有这间库房的批次 ⇒ 400 说清原因（点名下一步），**不**回空轨迹冒充"没有数据"。"""
    store.upsert_batches(pg, [{"room_id": "607", "entry_date": "2026-09-30",
                               "batch_no": "607_20260930", "batch_id": "607|2026-09-30"}])
    got = timeline(client, batch_id="")
    assert got.status_code == 400
    assert "台账里没有库房 611 的批次" in got.json()["detail"]
    assert "batch_ingest" in got.json()["detail"]


def test_rounds_without_a_round_id_are_not_guessed_into_the_grid(client, pg):
    """旧轮次没有 `"round_id` ⇒ 帧挂不到它头上：**不进网格**，但在 `"notes` 里点名+计数
    （不许悄悄消失：消失的轮次看起来就像"那几轮没巡检"）。"""
    store.insert_rounds(pg, ROUNDS + [
        {"ts": "2026-10-04T10:00:00", "round_id": None, "room_id": "611",
         "entry_date": "2026-09-30", "batch_no": "611_20260930", "ok": True},
    ])
    store.insert_images(pg, FRAMES)

    got = timeline(client).json()
    assert [p["ts"] for p in got["nodes"][0]["points"]][-1] == "2026-10-03T10:00:00"
    assert got["axis"] == {"t0": "2026-09-30T10:00:00", "t1": "2026-10-03T10:00:00",
                           "n_rounds": 4}
    assert got["notes"] == [
        "1 轮没有 round_id（旧轮次）：帧挂不到它头上，未进网格——**不猜**"]


def test_timeline_only_knows_coverage_and_one_station(client, pg):
    seed(pg)
    growth = timeline(client, metric="growth")
    assert growth.status_code == 400
    assert "只认 coverage" in growth.json()["detail"]

    none = timeline(client, station_id="")
    assert none.status_code == 400 and "station_id" in none.json()["detail"]

    bad_room = timeline(client, zone="999")
    assert bad_room.status_code == 400 and "不是规范库房号" in bad_room.json()["detail"]

    mismatch = timeline(client, batch_id="607|2026-09-30")
    assert mismatch.status_code == 400 and "不是同一间" in mismatch.json()["detail"]


def test_timeline_says_there_are_no_rounds_instead_of_faking_a_window(client, pg):
    """批次对但一轮都没有 ⇒ 空网格 + 一句人话；`"t0/t1` 是 null（**不拿今天凑窗口**）。"""
    got = timeline(client).json()
    assert got["ok"] is True and got["nodes"][0]["points"] == []
    assert got["axis"] == {"t0": None, "t1": None, "n_rounds": 0}
    assert got["notes"] == ["本批（611 2026-09-30）还没有巡检轮次：轨迹要等这一批有轮次之后才有内容"]

def test_one_station_whole_batch_stays_under_the_size_budget(client, pg):
    """验收 4：一个站位 × 本批天数（16）× 每天 8 轮 ⇒ 单次响应 ≤ 200 KB。"""
    rounds, frames = [], []
    for day in range(16):
        date = (dt.date(2026, 9, 30) + dt.timedelta(days=day)).isoformat()
        for hour in range(8):
            ts = f"{date}T{8 + hour:02d}:00:00"
            rounds.append(rnd(ts, f"r-{day}-{hour}"))
            frames.append(frame(ts, f"r-{day}-{hour}", "S102"))
    store.insert_rounds(pg, rounds)
    store.insert_images(pg, frames)

    resp = timeline(client)
    assert resp.status_code == 200
    assert len(resp.json()["nodes"][0]["points"]) == 16 * 8
    assert len(resp.content) < 200 * 1024, f"{len(resp.content)} 字节 > 200 KB"


def _counts(catalog, schema: str) -> dict:
    out = {}
    for table in ("rounds", "images", "batches", "batches_state", "measurements",
                  "environment", "operations"):
        catalog.execute(f'SELECT count(*) FROM "{schema}".{table}')
        out[table] = catalog.fetchone()[0]
    return out


def test_timeline_is_pure_read_and_never_touches_tdengine(client, pg, pg_schema, catalog,
                                                          monkeypatch):
    """验收 5：端点**纯读**（表行数一行不变）、**不碰 TDengine**（ADR-0026：只读源）。"""
    import analysis.env.tdengine as tdengine

    class Boom:
        def __init__(self, *args, **kwargs):
            raise AssertionError("/timeline 不许碰 TDengine（ADR-0026）")

    monkeypatch.setattr(tdengine, "TdengineEnvSource", Boom)
    seed(pg)
    before = _counts(catalog, pg_schema)

    assert timeline(client).status_code == 200
    assert _counts(catalog, pg_schema) == before, "读端点不许写库"

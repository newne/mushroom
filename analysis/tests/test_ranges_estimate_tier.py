"""票 26：区间表接纳「估算」档（ADR-0025，有意修正 ADR-0024 的「区间表只吃 verified」）。

为什么要有这些测试：`measurements` 长期 0 行，区间表长期只能出 `no_measurements` ——
"为了不放假数，连真数的位置也一起空着"。ADR-0025 放开的**只有区间表这一个消费者**，
代价是四条硬约束，缺一条就退回到"看着像真的假数"：

1. 只有估算行时给**独立状态**（「仅估算」），不是 `ok`、也不是任何空态；
2. 估算行与已验证行**不得混算**，同一分箱里两类计数分别可见；
3. **建议层永不消费**估算行（有断言，改坏了要能红）；
4. 整维标注「未验证」（信封 `flags` + 行上 `unverified`），页面才有一眼可辨的依据。

用**一条种子估算行**就能端到端验：不依赖真实数据到位（算法侧的临时序列还没落库）。
"""

from __future__ import annotations

import pytest

from analysis.align import (
    STATE_ESTIMATE_ONLY,
    STATE_OK,
    EstimateTierLeak,
    advice_input,
    assert_verified_only,
    compute_ranges,
    refresh_ranges,
)
from analysis.db import (
    connect,
    fetch_advice_ranges,
    fetch_ranges,
    fetch_ranges_state,
    insert_measurements,
    upsert_environment,
)

TMP = "2026-08-01T{i:02d}:00:00"


def _env(conn, hours: int, zone: str = "611") -> None:
    """24 小时环境：温度 22/26 交替，够两箱。"""
    upsert_environment(conn, [
        {"ts": TMP.format(i=i), "zone": zone, "temp_c": 22.0 + (i % 2) * 4.0,
         "rh_pct": 85.0, "co2_ppm": 800.0, "lamp_state": 2.0}
        for i in range(hours)
    ])


def _estimate_rows(hours: int, *, quality: str = "estimate", room: str = "611",
                   box: str = "B01", start: float = 10.0) -> list[dict]:
    """估算档的测量行：值非 NULL + `quality` 含 `estimate`（票 20 的行级标记）。"""
    return [{"ts": TMP.format(i=i), "room_id": room, "box_id": box,
             "mean_len_mm": start + i, "quality": quality}
            for i in range(hours)]


# ---------- 验收 ①：只有估算行时状态是独立的「仅估算」 ----------


def test_a_single_seed_estimate_row_is_already_its_own_state(tmp_path):
    """**一条种子估算行**就够：状态 = 仅估算，既不是 ok 也不是"没数据"。

    一条行算不出速率（速率的**两侧都必须存在**），所以这里同时钉住：
    状态说"这是估算档"，detail 说"为什么一行区间都没出"——两者不许互相盖掉。
    """
    path = str(tmp_path / "one.db")
    conn = connect(path)
    _env(conn, 24)
    insert_measurements(conn, _estimate_rows(1))

    run = compute_ranges(path)

    assert run.state == STATE_ESTIMATE_ONLY
    assert run.state != STATE_OK
    assert run.state != "no_measurements", "有测量行（只是估算档），不许说成没有数据"
    assert run.detail["n_estimate_measurements"] == 1
    assert run.detail["n_verified_measurements"] == 0
    # 一行算不出速率 ⇒ 没有区间行；但**为什么空**要留在 detail 里（票 05 的规矩）
    assert run.rows == []
    assert run.detail["tiers"]["estimate"]["n_rate_rows"] == 0
    assert run.detail["min_samples_per_bin"] == 3
    assert "估算" in run.state_text and "未验证" in run.state_text
    assert "样本不足" in run.state_text, "空要说得出口，别只留一句「仅估算」"

# ---------- 验收 ②③：不得混算 + 同一分箱里两类计数分别可见 ----------


def _verified_rows(hours: int, *, room: str = "611", box: str = "B01",
                   start: float = 10.0) -> list[dict]:
    """已验证档：每小时 +1mm ⇒ 每小时 1.0 mm/h。"""
    return [{"ts": TMP.format(i=i), "room_id": room, "box_id": box,
             "mean_len_mm": start + i, "quality": "verified"}
            for i in range(hours)]


def test_two_tiers_in_the_same_bin_are_counted_separately(tmp_path):
    """同一分箱里两类计数分别可见，且**统计只来自本档**（ADR-0025 §2）。

    造法：两档用**不同的框**（同库房、同时刻、同环境），落进同一个分箱 ——

    - 已验证档：24 小时每小时 +1mm ⇒ 1.0 mm/h；
    - 估算档：3 小时每小时 +11mm ⇒ 11.0 mm/h（层级 21 与 32 都落在 20–40mm 这一档）。

    「混算了没有」有**独立算得出来的判据**：主箱里 7 条已验证 + 1 条估算，
    合并后均值会从 1.0 变成 (7×1.0 + 1×11.0)/8 = 2.25、计数会从 7 变成 8。
    """
    path = str(tmp_path / "mix.db")
    conn = connect(path)
    _env(conn, 24)
    insert_measurements(conn, _verified_rows(24))
    insert_measurements(conn, [
        {"ts": TMP.format(i=i), "room_id": "611", "box_id": "B02",
         "mean_len_mm": level, "quality": "estimate+height_uncorrected"}
        for i, level in ((0, 10.0), (1, 21.0), (2, 32.0))
    ])

    run = compute_ranges(path)

    assert run.state == STATE_OK, "有已验证行 ⇒ 正常态（估算行只作计数）"
    assert run.flags == [], "这份表的主体是已验证档，不该整维标未验证"
    assert run.rows, "已验证行足够出区间"

    row = next(r for r in run.rows if r["stage"] == "20-40mm" and r["param"] == "temp_c")

    # ① 统计只来自本档：样本数 = 本档计数，均值 = 本档均值（合并后的 8 / 2.25 都是错的）
    assert row["tier"] == "verified"
    assert row["n"] == row["n_verified"] == 7
    assert row["rate_per_h"] == pytest.approx(1.0)
    assert row["rate_per_h"] != pytest.approx(2.25), "把估算行混进均值了"

    # ② 同一分箱里两类计数分别可见（不是只有一个含糊的 n）
    assert row["n_estimate"] == 1
    assert run.detail["n_verified_measurements"] == 24
    assert run.detail["n_estimate_measurements"] == 3
    assert run.detail["tiers"]["estimate"]["n_rate_rows"] == 2
    assert run.detail["tiers"]["estimate"]["metric"] == "mean_len_mm"

# ---------- 验收 ④：建议层永不消费估算行（有断言） ----------


def test_advice_input_never_carries_estimate_rows(tmp_path):
    """估算行**永不进建议层**（ADR-0025 §3），而且这条要能"改坏就红"。

    三层分别钉住：

    1. 端到端：只有估算行时，区间表有行、**建议层的输入是空的**；
    2. 混合时：建议层的输入只含已验证档那一行，估算行既不进也不"洗白"；
    3. 保险丝：真把一行估算行递到建议层入口（`assert_verified_only`），**直接抛**，
       不是静默丢掉 —— 静默丢掉的下一步就是有人"顺手"放宽筛选。
    """
    path = str(tmp_path / "advice.db")
    conn = connect(path)
    _env(conn, 24)
    insert_measurements(conn, _estimate_rows(24))

    refresh_ranges(path, computed_at="2026-08-02T00:00:00")
    assert fetch_ranges(conn), "估算档该出区间行（整张表就是它）"

    # ① 表里有行，建议层输入仍然是空 —— "有数"不等于"能用"
    assert fetch_advice_ranges(conn) == []
    assert fetch_ranges_state(conn)["state"] == STATE_ESTIMATE_ONLY
    assert fetch_ranges_state(conn)["flags"] == ["unverified"]

    # ② 混合时只给已验证档
    verified = {"stage": "20-40mm", "param": "temp_c", "tier": "verified", "unverified": False,
                "n": 3, "n_verified": 3, "n_estimate": 0}
    estimate = {"stage": "20-40mm", "param": "temp_c", "tier": "estimate", "unverified": True,
                "n": 9, "n_verified": 0, "n_estimate": 9}
    assert advice_input([verified, estimate]) == [verified]
    assert advice_input([]) == []

    # ③ 保险丝：估算行真的递进来时直接红（`EstimateTierLeak`），不许静默过滤
    with pytest.raises(EstimateTierLeak):
        assert_verified_only([estimate])
    assert_verified_only([verified])  # 正常路径不误伤

# ---------- 验收 ①（端到端，主 seam）：/ranges 信封把「估算（未验证）」给到页面 ----------


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "api.db")


@pytest.fixture
def client(db_path):
    from analysis.api import create_app
    from fastapi.testclient import TestClient

    return TestClient(create_app(db_path))


def _seed(path: str, rows: list[dict], *, hours: int = 24) -> None:
    conn = connect(path)
    _env(conn, hours)
    insert_measurements(conn, rows)


def test_ranges_envelope_from_a_single_seed_estimate_row(db_path, client):
    """验收原话：**一条种子估算行**就能端到端验（不依赖真实数据到位）。

    一条行算不出区间（速率的**两侧**都要在），所以这条同时钉住"没行也不能丢掉档"：
    状态仍是 `estimate_only`、`flags` 仍有 `unverified`、`state_text` 里"为什么没行"
    照样说得出口 —— 独立态与空态**两条信息都不许被对方盖掉**。
    """
    _seed(db_path, _estimate_rows(1))
    refresh_ranges(db_path, computed_at="2026-08-02T00:00:00")

    body = client.get("/ranges").json()

    assert body["state"] == STATE_ESTIMATE_ONLY, "有估算行 ⇒ 仅估算，不是 ok"
    assert body["state"] not in ("no_measurements", "never_computed"), "有数据，不许说成空"
    assert body["flags"] == ["unverified", "insufficient_samples"], "档 + 为什么没行，两条都在"
    assert body["rows"] == []
    assert body["detail"]["tiers"]["estimate"]["n_measurements"] == 1
    assert "估算" in body["state_text"] and "未验证" in body["state_text"]
    assert "样本不足" in body["state_text"]


def test_ranges_envelope_labels_the_estimate_tier(db_path, client):
    """同一套 seam（临时库 + 测试客户端）验端到端：估算档在信封里**一眼可辨**。

    页面上的「估算（未验证）」标注是**前端票**的事，但后端要把三样东西都给到：
    `state`（独立态）/ `flags`（整维标注）/ 行上的 `tier`+`unverified`（逐行兜底）。
    少任何一样，前端都只能靠猜 —— 那正是 ADR-0025 要避免的"看着像真的假数"。
    """
    _seed(db_path, _estimate_rows(24))
    refresh_ranges(db_path, computed_at="2026-08-02T00:00:00")

    body = client.get("/ranges").json()

    assert body["state"] == STATE_ESTIMATE_ONLY
    assert body["state"] != STATE_OK
    assert body["flags"] == ["unverified"], "整维标注：页面靠它显示「估算（未验证）」"
    assert "估算" in body["state_text"] and "未验证" in body["state_text"]
    assert body["count"] >= 1 and body["rows"], "估算档要有行（不是空表）"
    assert all(r["tier"] == "estimate" and r["unverified"] is True for r in body["rows"])
    assert all(r["n"] == r["n_estimate"] for r in body["rows"]), "本箱计数就是估算档自己的"
    assert all(r["n_verified"] == 0 for r in body["rows"])
    assert body["detail"]["tier"] == "estimate"
    assert body["detail"]["n_estimate_measurements"] == 24


def test_ranges_envelope_keeps_verified_tables_unlabeled(db_path, client):
    """反向钉子：验证档的表**不许**被顺手标成未验证（标注不能到处撒）。"""
    _seed(db_path, _verified_rows(24))
    refresh_ranges(db_path, computed_at="2026-08-02T00:00:00")

    body = client.get("/ranges").json()

    assert body["state"] == STATE_OK
    assert body["flags"] == []
    assert body["rows"] and all(
        r["tier"] == "verified" and r["unverified"] is False for r in body["rows"]
    )


# ---------- 老库迁移：新列必须能**就地**补上 ----------


def test_old_ranges_tables_get_the_tier_columns_in_place(tmp_path):
    """现场库是长期存在的：老 `ranges` / `ranges_state` 要能就地补列。

    `CREATE TABLE IF NOT EXISTS` **不补列** —— 漏一个就是每日重算抛
    `no such column: tier`、页面收到一个 500，比空表更坏（空表至少说得出口）。
    """
    import sqlite3

    from analysis.db import (
        migrate,
        replace_ranges,
        write_ranges_state,
    )

    raw = sqlite3.connect(str(tmp_path / "old.db"))
    raw.executescript("""
        CREATE TABLE ranges (
            stage TEXT NOT NULL, param TEXT NOT NULL, lo REAL, hi REAL, metric TEXT,
            unit TEXT, rate_per_h REAL, rate_mm_per_h REAL, n INTEGER,
            computed_at TEXT NOT NULL, PRIMARY KEY (stage, param));
        CREATE TABLE ranges_state (
            id INTEGER PRIMARY KEY CHECK (id = 1), computed_at TEXT, state TEXT NOT NULL,
            state_text TEXT NOT NULL, detail TEXT, n_rows INTEGER NOT NULL DEFAULT 0,
            metric TEXT, unit TEXT, updated_at TEXT NOT NULL);
        INSERT INTO ranges VALUES ('0-20mm', 'temp_c', 21.0, 22.0, 'mean_len_mm', 'mm',
                                   1.0, 1.0, 7, '2026-10-14T00:00:00');
    """)
    raw.commit()

    added = migrate(raw)
    assert {"ranges.tier", "ranges.n_verified", "ranges.n_estimate",
            "ranges_state.flags"} <= set(added), f"补列清单少了：{added}"
    assert migrate(raw) == [], "迁移幂等"

    # 老行没标过档 ⇒ 读侧算**未验证**（fail-closed），不许当验证值
    old = fetch_ranges(raw)[0]
    assert old["tier"] is None and old["unverified"] is True
    assert fetch_ranges_state(raw)["flags"] == []

    # 新列能写能读
    replace_ranges(raw, [{"stage": "0-20mm", "param": "temp_c", "lo": 21.0, "hi": 22.0,
                          "metric": "mean_len_mm", "unit": "mm", "rate_per_h": 1.0, "n": 7,
                          "tier": "estimate", "n_verified": 0, "n_estimate": 7}],
                   computed_at="2026-10-15T00:00:00")
    write_ranges_state(raw, state=STATE_ESTIMATE_ONLY, state_text="仅估算",
                       flags=["unverified"], n_rows=1)
    stored = fetch_ranges(raw)[0]
    assert (stored["tier"], stored["n_verified"], stored["n_estimate"],
            stored["unverified"]) == ("estimate", 0, 7, True)
    assert fetch_ranges_state(raw)["flags"] == ["unverified"]


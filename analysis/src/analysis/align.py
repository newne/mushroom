"""生长 × 环境对齐分析（票 08）：分阶段生长速率 × 环境分箱 → 最佳参数区间表。

输入为 measurements / environment 两表的小时级数据（spec §6）；输出区间行::

    {"stage": "20-40mm", "param": "temp_c", "lo": 23.5, "hi": 25.1,
     "rate_mm_per_h": 0.42, "n": 36}
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from analysis import store
from analysis.evidence import TIER_ESTIMATE, TIER_VERIFIED, tier_of
from analysis.growth import GROWTH_METRICS

MIN_SAMPLES_PER_BIN = 3  # 样本不足的区间不输出（避免误导）


def _pick_metric(m: pd.DataFrame, metric: str | None = None) -> tuple[str, str]:
    """选一个**有值**的长势量（列名, 单位）。

    默认按 `GROWTH_METRICS` 的优先级找第一个有值的：平视单档下 `mean_len_mm` 恒 NULL，
    实际会落到 `mean_projected_d_mm`（mm）或 `mean_area_mm2`（mm²）。
    一个都没有 ⇒ 报错（让上层说"样本不足"，不要硬算）。
    """
    for name, unit in GROWTH_METRICS:
        if metric and name != metric:
            continue
        if name in m.columns and m[name].notna().any():
            return name, unit
    raise ValueError(
        f"没有任何可用的长势量（{metric or '按优先级找'}）："
        f"平视下杆长恒 NULL，需要菌簇量（{', '.join(n for n, _ in GROWTH_METRICS)}）"
    )


def hourly_rates(
    measurements: pd.DataFrame,
    *,
    verified_only: bool = True,
    metric: str | None = None,
    tier: str | None = None,
) -> pd.DataFrame:
    """按 (库房, 框) 重采样为小时级生长曲线，差分得速率。

    输入列: ts, room_id, box_id[, quality] + 长势量（至少一个）
    输出列: room_id, box_id, hour, level_mm(该量的均值), `rate_per_h`、`rate_mm_per_h`、
    `metric`、`unit`

    - **量纲（票 05 / Chief 硬要求）**：帧里带 `metric`/`unit`；`rate_mm_per_h`
      **只在 `unit == 'mm'` 时非空**，面积口径下为 NaN —— 绝不让 `_mm_` 字段承载 mm²。
    - **只吃 `verified`**（票 11 §3）；`metric` 缺省按 `GROWTH_METRICS` 优先级取第一个有值的量。
    - `tier` 可以**显式点名**吃哪一档（票 26）：区间表的估算档传 `tier="estimate"`。
      **建议层永远不许传 `estimate`**（ADR-0025 §3）—— 它的入口是 `advice_input`，那里有断言。
      `verified_only=False` 保留旧语义：不过滤（老用例在用）。
    - **只在相邻整点间差分**：任一侧缺测（含被过滤掉的）⇒ 该段无速率，**不凑数**。
    """
    m = measurements.copy()
    if "room_id" not in m.columns:
        raise ValueError(
            "measurements 缺 room_id（票 11）：缺库房维度会把不同库房串成一条曲线"
        )
    wanted = tier if tier is not None else (TIER_VERIFIED if verified_only else None)
    if wanted is not None and "quality" in m.columns:
        # 档的判定**只有一处**（evidence.tier_of）：这里再来一份 contains 就会与它漂开。
        m = m[m["quality"].map(tier_of) == wanted]
    m["ts"] = pd.to_datetime(m["ts"])
    m["hour"] = m["ts"].dt.floor("h")
    if m.empty:
        # 空表也要带上正确的 dtype：否则 `hour` 是 object，和环境的 datetime64 合并时
        # pandas 直接抛 "trying to merge on object and datetime64 columns"。
        return pd.DataFrame({
            "room_id": pd.Series(dtype="object"),
            "box_id": pd.Series(dtype="object"),
            "hour": pd.Series(dtype="datetime64[ns]"),
            "level_mm": pd.Series(dtype="float64"),
            "rate_per_h": pd.Series(dtype="float64"),
            "rate_mm_per_h": pd.Series(dtype="float64"),
            "metric": pd.Series(dtype="object"),
            "unit": pd.Series(dtype="object"),
        })

    metric_name, unit = _pick_metric(m, metric)
    hourly = (m.groupby(["room_id", "box_id", "hour"])[metric_name]
              .mean().reset_index(name="level_mm"))
    hourly = hourly.sort_values(["room_id", "box_id", "hour"])
    grouped = hourly.groupby(["room_id", "box_id"])
    prev_hour = grouped["hour"].shift(1)
    prev_level = grouped["level_mm"].shift(1)
    one_hour = pd.Timedelta(hours=1)
    rate = (hourly["level_mm"] - prev_level).where((hourly["hour"] - prev_hour) == one_hour)
    hourly["rate_per_h"] = rate
    hourly["rate_mm_per_h"] = rate if unit == "mm" else float("nan")
    hourly["metric"] = metric_name
    hourly["unit"] = unit
    return hourly.dropna(subset=["rate_per_h"])[
        ["room_id", "box_id", "hour", "level_mm", "rate_per_h", "rate_mm_per_h",
         "metric", "unit"]
    ]


def align(rates: pd.DataFrame, environment: pd.DataFrame) -> pd.DataFrame:
    """生长速率与环境小时均值按 **(库房, 小时)** 对齐。

    environment 列: ts, zone, temp_c, rh_pct, co2_ppm[, lamp_on_min]

    两条硬约束（票 11/票 05）：环境必须按 `zone` 分组，否则四个库房混进同一个分箱；
    `zone` 与测量的 `room_id` 是同一个东西，只做一次改名，不造第三个名字。
    """
    if "room_id" not in rates.columns:
        raise ValueError("rates 缺 room_id（票 11）：对不齐库房的速率不能进分箱")
    env = environment.copy()
    if "zone" not in env.columns:
        raise ValueError(
            "environment 缺 zone（票 05）：不按库房聚合会把四个库房混进同一个分箱"
        )
    env["ts"] = pd.to_datetime(env["ts"])
    env["hour"] = env["ts"].dt.floor("h")
    cols = [c for c in ("temp_c", "rh_pct", "co2_ppm", "lamp_on_min") if c in env.columns]
    if rates.empty or env.empty:
        # 空输入直接给空结果，**不要进 merge**：空 DataFrame 的 dtype 随 pandas 版本
        # 摇摆（object vs datetime64），混跑换解释器时会抛
        # "You are trying to merge on object and datetime64 columns" —— 测试门禁
        # 必须与版本/顺序无关。
        return pd.DataFrame(columns=[
            "room_id", "box_id", "hour", "level_mm", "rate_mm_per_h", *cols, "stage_mm", "stage",
        ])
    env_hourly = (
        env.groupby(["zone", "hour"])[cols].mean().reset_index()
        .rename(columns={"zone": "room_id"})
    )
    merged = rates.merge(env_hourly, on=["room_id", "hour"], how="inner")
    # 分箱口径：暂留 20mm 长度分档；spec §2.7 的 D 分档要生长天数，
    # 等票 04 的批次窗口 join 落地后再换（票 05）。
    merged["stage_mm"] = (merged["level_mm"] // 20 * 20).astype(int)
    merged["stage"] = merged["stage_mm"].astype(str) + "-" + (merged["stage_mm"] + 20).astype(str) + "mm"
    return merged


#: 可分箱的连续环境量。补光的设备状态（0=自动…3=关闭）是分类量，不进分箱；
#: 进分箱的是由循环参数（OnSSet/OffSSet）折算出来的**点亮分钟**（ADR-0021）。
PARAM_COLS = ("temp_c", "rh_pct", "co2_ppm", "lamp_on_min")


def _count_in_bin(peer_stage: pd.DataFrame | None, param: str, lo: float, hi: float) -> int:
    """另一档有几行落在这个箱里（**只数，不算**）。

    箱边界来自 `pd.qcut`（右闭 `(lo, hi]`，与分箱同口径），所以判据是 `lo < x <= hi`；
    改成 `[lo, hi)` 会把边界上的行数漏掉，计数就跟分箱对不上了 —— 那正是本票要防的"数对不上"。
    """
    if peer_stage is None or peer_stage.empty or param not in peer_stage.columns:
        return 0
    col = peer_stage[param]
    return int(((col > lo) & (col <= hi)).sum())


def best_ranges(merged: pd.DataFrame, *, n_bins: int = 4,
                tier: str = TIER_VERIFIED,
                peers: pd.DataFrame | None = None) -> list[dict]:
    """对齐数据 → 最佳参数区间表（分位数分箱，取平均速率最高且样本充足的箱）。

    输出的每一行都带 **`metric` / `unit` / `rate_per_h`**（量纲随行走，票 05）与 **`tier`**
    （票 26：**档随行走** —— 下游靠它决定能不能进建议层、要不要标「未验证」，不许回头猜）；
    `rate_mm_per_h` 不再由这里给 —— 由门面 `store.replace_ranges` 按 `unit=='mm/h'` 决定写不写。

    `merged` 必须**已经是单一档**的数据：这个函数不做任何档筛选（混算不在这里发生，
    也不会在这里被挡住），筛选与分档由 `compute_ranges` 负责。

    `peers`（另一档的对齐数据）**只用来数每箱有几行**（`n_verified` / `n_estimate`）——
    它不参与分箱、不参与均值，所以"同一个分箱里两类计数分别可见"与"不得混算"同时成立。
    """
    out: list[dict] = []
    if merged.empty:
        return out
    metric = str(merged["metric"].iloc[0]) if "metric" in merged.columns else None
    unit = str(merged["unit"].iloc[0]) if "unit" in merged.columns else None
    peer_tier = TIER_ESTIMATE if tier == TIER_VERIFIED else TIER_VERIFIED
    peers_by_stage: dict[str, pd.DataFrame] = {}
    if peers is not None and not peers.empty and "stage" in peers.columns:
        peers_by_stage = {str(s): g for s, g in peers.groupby("stage")}
    for stage, group in merged.groupby("stage"):
        for param in PARAM_COLS:
            if param not in group or group[param].isna().all():
                continue
            try:
                bins = pd.qcut(group[param], q=n_bins, duplicates="drop")
            except ValueError:
                continue
            stats = group.groupby(bins, observed=True)["rate_per_h"].agg(["mean", "count"])
            stats = stats[stats["count"] >= MIN_SAMPLES_PER_BIN]
            if stats.empty:
                continue
            best = stats["mean"].idxmax()
            lo, hi = best.left, best.right
            n_self = int(stats.loc[best, "count"])
            # 两类计数：本档的数来自本档的箱（= n），另一档只**数**不**算**
            counts = {
                tier: n_self,
                peer_tier: _count_in_bin(peers_by_stage.get(str(stage)), param, lo, hi),
            }
            out.append({
                "stage": stage,
                "param": param,
                "lo": round(float(lo), 2),
                "hi": round(float(hi), 2),
                "metric": metric,
                "unit": unit,
                "rate_per_h": round(float(stats.loc[best, "mean"]), 4),
                "n": n_self,
                "tier": tier,
                "n_verified": counts[TIER_VERIFIED],
                "n_estimate": counts[TIER_ESTIMATE],
            })
    return out


def run_from_db(db_path: str | None = None, *, conn=None) -> list[dict]:
    """从库里读两表跑全流程（只读式计算，不落表）。返回区间行，**不返回为什么空**。

    要"为什么空"请用 `compute_ranges`（票 05 的空态标注）——它给同一批行 + 状态。
    连接来源同 `compute_ranges`（票 28 第 2 步）：有 `conn` 就用，没有才按路径建。
    """
    return compute_ranges(db_path, conn=conn).rows


#: 区间表重算的状态机（票 05）。**空表必须说得出为什么空**：
#: "从没跑过" / "没有测量" / "全被排除" / "没有环境数据" / "平视下没有可用长势量" /
#: "样本不足" 是六件不同的事，前五件靠再跑一次是好不了的，页面不能都显示成"暂无数据"。
#: 票 26 另加第七件**不是空态**的事：**「仅估算」**（这份表只有估算档，见 ADR-0025）。
STATE_OK = "ok"
STATE_NO_MEASUREMENTS = "no_measurements"
STATE_ALL_EXCLUDED = "all_excluded"
STATE_NO_ENVIRONMENT = "no_environment"
STATE_NO_GROWTH_METRIC = "no_growth_metric"
STATE_INSUFFICIENT_SAMPLES = "insufficient_samples"

#: **「仅估算」**（票 26 / ADR-0025）：这份区间表**只有估算档**，整维「未验证」。
#: 它是**独立状态**，不是 `ok` 的变体、也不是任何空态：一条估算行都要有明确表达 ——
#: 空态说的是"为什么没有数据"，本状态说的是"这份表是什么档次"，混成一个就会重演
#: ADR-0024 当初要防的事（看着像真的假数）。
STATE_ESTIMATE_ONLY = "estimate_only"

#: 信封 `flags` 里的标注（spec「状态与标注」：标注进 flags，不另开状态词）。
FLAG_UNVERIFIED = "unverified"
FLAG_INSUFFICIENT_SAMPLES = "insufficient_samples"

#: 每个状态一句人话（页面直接显示，不用自己拼，也别让四个页面各写一份）。
STATE_TEXT = {
    STATE_OK: "区间表已算好（{n} 行）",
    STATE_NO_MEASUREMENTS: "样本不足：库房里还没有任何测量行（测量管线未接上）",
    STATE_ALL_EXCLUDED: (
        "样本不足：测量行全部落在被排除的可疑轮/复制帧窗口内（见 detail.excluded_by_reason）"
    ),
    STATE_NO_ENVIRONMENT: "样本不足：还没有环境小时聚合，长势速率无法与环境对齐",
    STATE_NO_GROWTH_METRIC: (
        "样本不足：没有可用的长势量（平视单档下杆长恒缺测，需要菌簇量）"
    ),
    STATE_INSUFFICIENT_SAMPLES: (
        "样本不足：每个参数分箱的有效样本 < {min}（相邻整点的速率段不够），暂不输出区间"
    ),
    STATE_ESTIMATE_ONLY: (
        "区间表只有估算档（{n} 行，**未验证**）：估算值不能当验证值用，也永不进建议层"
    ),
}

#: 「仅估算」但一行区间都没出时的后半句：状态说的是**档**，不是"没有数据" ——
#: "为什么一行都没出"照样要说清楚（票 05 的规矩不因为换了个状态词就失效）。
STATE_TEXT_ESTIMATE_EMPTY = (
    "；样本不足：每个参数分箱的有效样本 < {min}（相邻整点的速率段不够），暂不输出区间"
)


@dataclass
class RangesRun:
    """一次重算的**全部结果**：区间行 + 状态 + 明细计数。

    `detail` 里的计数是"排除必须可见"的落点（票 17）：被 10-09 那条显式排除清单排掉的行
    要能在这里数出来，不许静默消失。`flags` 是**整维标注**的落点（票 26）：估算档的
    `unverified` 走这里，页面不用自己去猜。
    """

    rows: list[dict]
    state: str
    state_text: str
    detail: dict
    flags: list[str] = field(default_factory=list)


def compute_ranges(
    db_path: str | None = None, *, conn=None, metric: str | None = None
) -> RangesRun:
    """从库里读两表跑全流程，返回 **区间行 + 为什么空**（票 05）。

    **连接从哪来**（票 28 第 2 步）：给了 `conn` 就用它 —— API / jobs / 批处理手里本来就有
    一份连接，**不许再按路径重连**（PG 下没有"路径"这个概念）；没给就经门面按 `db_path`
    建一条，用完在本函数里关掉（CLI 与老测试还按老形状传路径）。
    """
    if conn is not None:
        return _compute_ranges(conn, metric=metric)
    own = store.connect(db_path)
    try:
        return _compute_ranges(own, metric=metric)
    finally:
        own.close()


def _compute_ranges(conn, *, metric: str | None = None) -> RangesRun:
    """`compute_ranges` 的实体：只认连接，数据访问全走门面。

    - **先过滤**（票 13/17）：可疑轮、复制帧、以及显式排除清单命中的测量行不进统计 ——
      复制帧会让长势曲线看起来正常，那种错误比缺数据更难发现。
    - 每一步失败都映射成一个**具名状态**，绝不返回一个无解释的空列表。
    """
    from analysis.integrity import partition

    measurements = pd.DataFrame(store.fetch_measurements(conn))
    # 环境读经门面再拼 DataFrame：`pd.read_sql` 只吃 DBAPI 连接（pandas 不认识 psycopg）
    # —— 票 28 第 2 步的挡路点 3。列名与原来那条 SELECT 一致（多出来的列没人读）。
    environment = pd.DataFrame(store.fetch_environment(conn))
    detail: dict = {
        "n_measurements": int(len(measurements)),
        "n_environment": int(len(environment)),
        "n_kept": 0,
        "n_excluded": 0,
        "excluded_by_reason": {},
        "min_samples_per_bin": MIN_SAMPLES_PER_BIN,
        # 档（票 26）：这份表是哪一档、两档各有几行测量 —— 「两类计数分别可见」的第一层。
        "tier": None,
        "n_verified_measurements": 0,
        "n_estimate_measurements": 0,
        # 两档各一份明细，**缺的一档也带键**：调用方不用判断键在不在
        "tiers": {t: {"n_measurements": 0, "n_rate_rows": None, "n_aligned_hours": None,
                      "n_rows": 0, "metric": None, "unit": None, "error": None}
                  for t in (TIER_VERIFIED, TIER_ESTIMATE)},
    }

    def state_text_for(state: str, n_rows: int) -> str:
        if state == STATE_ESTIMATE_ONLY and not n_rows:
            return (STATE_TEXT[state].format(n=n_rows)
                    + STATE_TEXT_ESTIMATE_EMPTY.format(min=MIN_SAMPLES_PER_BIN))
        return STATE_TEXT[state].format(n=n_rows, min=MIN_SAMPLES_PER_BIN)

    def flags_for(tier: str | None, *, empty_reason: str | None = None) -> list[str]:
        """估算档的**整维标注**（ADR-0025 §4）：`unverified` 必有；一行都出不了时补上原因标注。"""
        if tier != TIER_ESTIMATE:
            return []
        return [FLAG_UNVERIFIED] + ([empty_reason] if empty_reason else [])

    def done(state: str, extra: dict | None = None,
             *, flags: list[str] | None = None) -> RangesRun:
        detail.update(extra or {})
        return RangesRun(rows=[], state=state, state_text=state_text_for(state, 0),
                         detail=dict(detail), flags=list(flags or []))

    if measurements.empty:
        return done(STATE_NO_MEASUREMENTS)

    kept, dropped = partition(measurements.to_dict("records"), store.fetch_rounds(conn))
    detail["n_kept"] = len(kept)
    detail["n_excluded"] = len(dropped)
    by_reason: dict[str, int] = {}
    for item in dropped:
        by_reason[item.reason] = by_reason.get(item.reason, 0) + 1
    detail["excluded_by_reason"] = by_reason
    # 前 20 条原文（带人话原因）——"排除了多少"之外还要能回答"排掉了哪一段、凭什么"
    detail["excluded_samples"] = [
        {"ts": d.ts, "zone": d.zone, "reason": d.reason, "window": d.window, "detail": d.detail}
        for d in dropped[:20]
    ]
    detail["excluded_samples_truncated"] = len(dropped) > 20
    if dropped:
        print(f"[ranges] 排除 {len(dropped)} 条落在可疑轮窗口内的测量行（票 13/17）")

    if not kept:
        return done(STATE_ALL_EXCLUDED)
    if environment.empty:
        return done(STATE_NO_ENVIRONMENT)

    kept_df = pd.DataFrame(kept)
    if "quality" in kept_df.columns:
        by_tier = {t: kept_df[kept_df["quality"].map(tier_of) == t]
                   for t in (TIER_VERIFIED, TIER_ESTIMATE)}
    else:
        # 没有 quality 列（手工构造的帧/老库）：按旧口径整列当 verified 用 —— **不静默丢数据**
        by_tier = {TIER_VERIFIED: kept_df, TIER_ESTIMATE: kept_df.iloc[0:0]}
    detail["n_verified_measurements"] = int(len(by_tier[TIER_VERIFIED]))
    detail["n_estimate_measurements"] = int(len(by_tier[TIER_ESTIMATE]))
    for t, df in by_tier.items():
        detail["tiers"][t]["n_measurements"] = int(len(df))
    # 这份表是哪一档：有 verified 行就是 verified 档（估算行只进计数），否则才轮到估算档
    tier = (TIER_VERIFIED if len(by_tier[TIER_VERIFIED])
            else TIER_ESTIMATE if len(by_tier[TIER_ESTIMATE]) else None)
    detail["tier"] = tier

    # 两档**各跑一遍**流水线（ADR-0025 §2：估算行与已验证行不得混算）——
    # 混算出来的平均值既不是估算值也不是验证值，而且没人看得出它是哪一种。
    merged_by_tier: dict[str, pd.DataFrame] = {}
    for t, df in by_tier.items():
        if df.empty:
            continue
        info = detail["tiers"][t]
        try:
            metric_name, unit = _pick_metric(df, metric)
        except ValueError as e:
            info["error"] = str(e)
            continue
        info["metric"] = metric_name
        info["unit"] = unit
        rates = hourly_rates(df, tier=t, metric=metric_name)
        info["n_rate_rows"] = int(len(rates))
        merged = align(rates, environment)
        info["n_aligned_hours"] = int(len(merged))
        merged_by_tier[t] = merged

    primary = (TIER_VERIFIED if TIER_VERIFIED in merged_by_tier
               else TIER_ESTIMATE if TIER_ESTIMATE in merged_by_tier else None)
    if primary is None:
        # 一档都没跑起来：票 05 的两种"为什么空"照旧（没有可用长势量 / 样本不足）
        try:
            _pick_metric(kept_df, metric)
        except ValueError as e:
            # 平视单档下这条现在是**常态**（mean_len_mm 恒 NULL，菌簇四列还没有生产者）：
            # 必须说清是"没有长势量"，而不是笼统的"样本不足"。
            detail["error"] = str(e)
            return done(STATE_NO_GROWTH_METRIC, flags=flags_for(tier))
        return done(STATE_INSUFFICIENT_SAMPLES, flags=flags_for(tier))

    info = detail["tiers"][primary]
    detail["metric"] = info["metric"]
    detail["unit"] = info["unit"]
    detail["n_rate_rows"] = info["n_rate_rows"]
    detail["n_aligned_hours"] = info["n_aligned_hours"]
    # 另一档只作**每箱计数**用（peer），绝不参与分箱与均值
    other = TIER_ESTIMATE if primary == TIER_VERIFIED else TIER_VERIFIED
    rows = best_ranges(merged_by_tier[primary], tier=primary,
                       peers=merged_by_tier.get(other))
    info["n_rows"] = len(rows)
    detail["n_rows"] = len(rows)

    if not rows:
        if primary == TIER_ESTIMATE:
            # 估算档出不了行也仍是「仅估算」：**档是表的属性**，"为什么没行"由 flags 与
            # detail/state_text 说 —— 两者不许互相盖掉（票 05 的规矩照旧生效）。
            return done(STATE_ESTIMATE_ONLY,
                        flags=flags_for(tier, empty_reason=FLAG_INSUFFICIENT_SAMPLES))
        return done(STATE_INSUFFICIENT_SAMPLES)

    state = STATE_OK if primary == TIER_VERIFIED else STATE_ESTIMATE_ONLY
    return RangesRun(
        rows=rows,
        state=state,
        state_text=state_text_for(state, len(rows)),
        detail=dict(detail),
        flags=flags_for(tier),
    )


def refresh_ranges(
    db_path: str | None = None, *, conn=None,
    computed_at: str | None = None, metric: str | None = None,
) -> int:
    """每日批处理入口（票 05）：计算最佳参数区间并整体替换 ranges 表（评审 #6）。

    API 的 GET /ranges 只读本表——重算与读取分离，请求代价为 O(表大小)。

    **连接来源同 `compute_ranges`**（票 28 第 2 步）：jobs 与 API 手里有连接就直接传进来，
    不再按路径重连（PG 下没有"路径"）；没给就经门面按 `db_path` 建一条、用完关掉。
    """
    if conn is not None:
        return _refresh_ranges(conn, computed_at=computed_at, metric=metric)
    own = store.connect(db_path)
    try:
        return _refresh_ranges(own, computed_at=computed_at, metric=metric)
    finally:
        own.close()


def _refresh_ranges(conn, *, computed_at: str | None, metric: str | None) -> int:
    """`refresh_ranges` 的实体：**同一个连接里读 + 写**（同一事务里写区间行与状态）：

    行与状态不许对不上（"有 12 行"却写着 `样本不足`）。
    """
    from datetime import datetime

    stamp = computed_at or datetime.now().isoformat(timespec="seconds")
    run = _compute_ranges(conn, metric=metric)
    store.replace_ranges(conn, run.rows, stamp)
    store.write_ranges_state(
        conn,
        state=run.state,
        state_text=run.state_text,
        computed_at=stamp,
        detail=run.detail,
        flags=run.flags,
        n_rows=len(run.rows),
        metric=run.detail.get("metric"),
        unit=run.detail.get("unit"),
    )
    print(f"[ranges] state={run.state} rows={len(run.rows)} flags={run.flags} {run.state_text}")
    return len(run.rows)


# ---------- 建议层：估算行永不进（ADR-0025 §3） ----------


class EstimateTierLeak(RuntimeError):
    """估算行漏进了**只能吃已验证档**的入口（建议层 / 决策层）。

    fail-closed：**不许**被下游 catch 成"过滤掉再继续" —— 它意味着"筛选规则被改宽了"
    或"上游把估算行标成了 verified"，两种都得先修规则再跑，而不是带着它出建议。
    """


def is_verified_row(row) -> bool:
    """这一行能不能进建议层：**必须显式标着** `tier == "verified"` 且未被标未验证。

    没标档（老行 / 手工拼的行）**算不合格** —— fail-closed：宁可少一行，也不许把
    "没人标过"当"已验证"用（ADR-0025 §4：`verified` 是唯一能进建议层的标记）。
    """
    return (isinstance(row, dict) and row.get("tier") == TIER_VERIFIED
            and not row.get("unverified"))


def assert_verified_only(rows, *, what: str = "建议层的输入") -> None:
    """断言：集合里**不含估算行**（ADR-0025 §3 的保险丝 —— 改坏了要能红）。

    这是"建议层不消费估算行"从口头约定变成**可失败**的那一处：筛选逻辑被谁改宽、或者
    上游把估算行标成 verified，都会在这里炸出来，而不是安静地拿估算值去调控库房。
    """
    bad = [r for r in rows if not is_verified_row(r)]
    if bad:
        tiers = sorted({str(r.get("tier")) if isinstance(r, dict) else type(r).__name__
                        for r in bad})
        raise EstimateTierLeak(
            f"{what}里有 {len(bad)} 行不是已验证档（{', '.join(tiers)}）——"
            "估算行永不进建议层（ADR-0025 §3）"
        )


def advice_input(rows) -> list[dict]:
    """**建议层读区间行只许走这里**：只取已验证档，并对结果做断言（ADR-0025 §3）。

    为什么不一"过滤完就算了"：静默过滤是**看不见的降级** —— 没人知道那份建议是在少了
    N 行估算数据的情况下给出的。断言把"筛选被改宽"变成一次响亮的失败。
    """
    kept = [r for r in rows if is_verified_row(r)]
    assert_verified_only(kept)
    return kept

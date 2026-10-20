"""测量质量标记词汇表（票 11 §3 的代码侧钉子）。

`measurements.quality` 是**枚举**，不是自由文本。多标记用 `+` 连接，例如
`cap_level_approx+low_confidence`；**只要不含 `verified` 就不进区间表与决策统计**
（行照旧入库，缺失要可审计）。

| 值 | 含义 | 进区间表？ |
| --- | --- | --- |
| `verified` | 检出可信，数值可用于分析 | 是 |
| `no_detection` | 本行无有效检出，长度列全 NULL | 否 |
| `baseline` | 基线/定标帧，不是生长测量（⚠️ **历史值：2026-10-14 起停发**，枚举保留供读兼容） | 否 |
| `cap_level_approx` | 菌盖为平视近似值 | 否 |
| `stem_absent(level0)` | 菌柄缺失（level 0），长度无意义 | 否 |
| `low_confidence` | 置信度不足 | 否 |

本模块只多登记一个值：`no_angle_profile`——票 11 §9 要求"缺测原因登记为
无对应角度档"，而这个原因在单档平视下**每行都有**，落在 `quality` 里最不容易被
下游忽略（比埋在 `notes` 里强）。已在票 11 Comments 里登记待环境官确认。

铁律：无值一律 **NULL**（`n` 可为 0），**绝不写 0 冒充测量**——
`align.hourly_rates` 会在 0 与真值之间差分出一个陡降的假速率。
"""

from __future__ import annotations

#: 字段分级（spec §4.7）：`legacy` 退出活跃口径，`auxiliary` 仍产出但不作交付口径。
#: 键一律用**线格式/JSONL 的 snake_case**，与 `record.RECORD_FIELDS`、`BagResult` 一致；
#: spec 里的 CamelCase（`H_profile`）只是公式记号，由 `_FIELD_ALIASES` 归一。
LEGACY_FIELDS = ("mean_len_mm", "p10_len", "p90_len", "StipeEstimate")
AUXILIARY_FIELDS = ("mean_cap_mm", "mean_cluster_w_mm", "mean_cluster_h_mm",
                    "mean_area_mm2", "mean_projected_d_mm")
#: 像素口径的辅助量；**不是 mm**，别和上面那组混在一个清单里。
AUXILIARY_PX_FIELDS = ("cluster_h_px",)
ACTIVE_FIELDS = ("h_profile", "h_p50", "h_p90", "rel_profile_height",
                 "rel_top_height", "rel_width")

_FIELD_ALIASES = {"H_profile": "h_profile", "H_p50": "h_p50", "H_p90": "h_p90"}

FIELD_TIERS = {
    **{name: "legacy" for name in LEGACY_FIELDS},
    **{name: "auxiliary" for name in AUXILIARY_FIELDS},
    **{name: "auxiliary_px" for name in AUXILIARY_PX_FIELDS},
    **{name: "active" for name in ACTIVE_FIELDS},
}


def field_tier(name: str) -> str:
    """`active` / `auxiliary` / `auxiliary_px` / `legacy` / `unknown`。"""
    return FIELD_TIERS.get(_FIELD_ALIASES.get(name, name), "unknown")


def assert_no_legacy_field_in_active_output(row: dict) -> None:
    """新产出的活跃行不得携带 legacy 值；历史回读不走这个断言。"""
    bad = {name: row[name] for name in LEGACY_FIELDS
           if name in row and row[name] is not None}
    if bad:
        raise AssertionError(f"legacy 字段不得出现在活跃输出里：{bad}")


def assert_not_region_height_as_cluster_height(metric: str) -> None:
    """`cluster_h_px`/`cluster_h_mm` 是形态摘要，不是簇高；被当高度口径用就报错。"""
    if metric in ("cluster_h_px", "cluster_h_mm", "mean_cluster_h_mm"):
        raise AssertionError(f"{metric} 是外接框跨度，不是袋口以上高度（spec §4.2）")


#: 票 11 §3 的枚举 + `no_angle_profile` + `level_view`。
QUALITY_FLAGS = (
    "verified",              # 检出可信，可用于分析（当前无一行是它）
    "no_detection",          # 无有效检出，长度列全 NULL。**"没有候选可判"（本帧真的没找到东西）
                             # 与 `unreliable`（找到了、但标尺/置信度不可靠）是两件事**：
                             # 零候选 ⇒ 本码；有候选但判不可信 ⇒ `unreliable`。
                             # 这是**刻意的 fail-closed**，不是漏判——把零候选报成 `unreliable`
                             # 等于"在事实之外编一层解释"（Chief 2026-10-14 裁定，票 15 §14-2）。
    "baseline",              # ⚠️ **历史值：2026-10-14 起停发**（Chief 四条裁定）。
                             # 语义是"基线/定标帧，不是生长测量"，但停发后产的是**估算行**，
                             # 这三个字会被读成"定标帧"⇒ 误导性命名，比没有标记更糟。
                             # **枚举保留**：历史行仍可能带它，读兼容必须留（不是不可用标记）。
    "cap_level_approx",      # 菌盖数值为平视近似
    "stem_absent(level0)",   # 菌柄缺失，长度无意义
    "low_confidence",        # 置信度不足（实例拆分未生效 / 无种子 / 前景无法分离）
    "no_angle_profile",      # 该量在当前角度档下不可测（原因：无对应角度档）
    "level_view",            # **平视口径的旁白**：视角事实的权威字段是 `angle_profile`；
                             # 本标记**不参与可用性判定**，也不决定任何行为（票 11 §3）
    "no_light",              # 整帧近黑（灯未亮/无信息量）⇒ 该帧不可用，不是降权
    # 估算/预测三码（Chief 2026-10-14 裁定 5；命名对齐票 18，不许混成一个）
    "estimate",              # 行级 fail-safe：本行有估算值（与 verified 正交，但单独出现不算可用）
    "unreliable",            # 远排/低置信 ⇒ 值写 NULL，处置=不可用（与 no_light 同级）。
                             # **产出前提 = 有候选被判不可信**（L1 返回候选 → `row_is_reliable()`
                             # 判 → `all_rows_unreliable()` 发射）；零候选走 `no_detection`。
    "no_scale",              # **完全没有标尺**（无参考物/找不到同排菌包/标准尺寸缺失）⇒ mm 写 NULL
    "no_theta",              # **有标尺、只是缺俯角 θ** ⇒ 投影项无法补偿：竖直量写 NULL，
                             # 水平量照出。**与 no_scale 严格分开**（Chief 2026-10-14）：
                             # 旧实现把"θ 缺"也记成 no_scale，而 no_scale 属"整行不可用"
                             # ⇒ 213/218 帧明明出了有效的 mean_cluster_w_mm 却被下游整行丢掉，
                             # 那是"把有数据说成没数据"。本标记**不进 NON_USABLE_FLAGS**：
                             # 它只说明"哪个修正项缺"，字段级取舍交给 estimated_fields（票 20）。
    "baseline_invalid",      # v6：袋顶基线不可用 ⇒ H 三兄弟 NULL，区域指标不受影响
    "association_ambiguous", # v6：一袋一簇归属失败 ⇒ 袋级指标 NULL，锚点级仍出
    "merged_multiple_bags",  # v6：多袋合并进一个 cluster ⇒ 不拆、不分摊
    "insufficient_contour",  # v6：有效列不足 ⇒ 高度缺测
    "bag_mask_missing",      # v6：有 cluster 无 bag ⇒ 区域指标 NULL（高度不受影响）
    "no_region_after_debag", # v6：去袋后为空 ⇒ 区域指标 NULL
    "baseline_or_mask_inconsistent",  # v6：delta 一致性检查超差（只作 QA）
    "cap_model_unavailable", # v6：帽模型不可用 ⇒ 帽级 NULL，不阻塞簇级
    "no_cap",                # v6：无合格帽 ⇒ 帽级 NULL
    "panel_coverage_low",    # v6：固定集合覆盖不足 ⇒ 曲线点标不可比
    "no_usable_bags",        # v6：该锚点该帧没有可用袋
    "height_uncorrected",    # **未做 cosθ 补偿的竖直投影跨度（mm）**——Chief 2026-10-14 裁定 (b)：
                             # 竖直列在 θ 未注入时不再恒 NULL，而是出"投影跨度"并挂本标记。
                             # 它**不是长度**：页面必须显示「估算（未做俯角补偿）」；
                             # 与 `estimate` 并存，**不进 verified/区间表/建议层**；
                             # 只在标注完成后允许**相对比较**（同站位、同档、同 imaging_profile）；
                             # 将来 θ 注入后，补偿与未补偿的量**不得同列不加区分地比**。
                             # 默认开关关（`allow_uncorrected_height=False`），保住 NULL 语义；
                             # 开启时机由 Chief 在票 20 接线时定。**不进 NON_USABLE_FLAGS**。
)

#: 出现其中任意一个 ⇒ 这一行**没有可用测量**。
#: `no_light` / `unreliable` / `no_scale` 都是"不可用"（值应为 NULL），
#: 而不是降权；`estimate` **不在此列**——它与 `verified` 正交（票 18 §2），
#: 但"只有 estimate 没有 verified"的行仍不得进统计，见 `field_is_usable()`。
NON_USABLE_FLAGS = ("no_detection", "low_confidence", "no_light", "unreliable",
                    "no_scale", "blurry", "overexposed")


def field_is_usable(quality: str | None, estimated_fields: str | None,
                    field: str) -> bool:
    """票 18 §2 的过滤规则：字段 X 可用 ⇔ `quality` 含 `verified` 且 X 不在估算清单里。

    这是**区间表/建议层/统计**的唯一入口；只看展示的消费者可以不用它。
    """
    flags = parse_flags(quality)
    if "verified" not in flags:
        return False
    estimated = {f for f in (estimated_fields or "").split("+") if f}
    return field not in estimated

#: 多标记连接符（票 11 §3）。
JOINER = "+"


def format_estimated_fields(value) -> str | None:
    """列名清单 → 入库用的 `+` 串（票 20，analysis 侧唯一形状）。

    收 `["a", "b"]` / `"a+b"` / `None`；**去重保序**；空 ⇒ **None（入库 NULL）**，
    不返回空串。语义与 `analysis/evidence.py` 的 `format_estimated_fields()` 一致——
    那边是消费端归一，这边是生产端出口，**两边都不许把形状留给下游猜**。
    """
    if value is None or value == "":
        return None
    if isinstance(value, str):
        items = value.split(JOINER)
    elif isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        items = [str(value)]
    out: list[str] = []
    for item in items:
        text = item.strip()
        if text and text not in out:
            out.append(text)
    return JOINER.join(out) if out else None


def without_legacy_fields(value) -> str | None:
    """从 `estimated_fields` 里剔掉 legacy 列；全被剔光 ⇒ `None`（入库 NULL）。"""
    if value is None or value == "":
        return None
    items = value.split(JOINER) if isinstance(value, str) else [str(v) for v in value]
    return format_estimated_fields([i for i in items if i.strip() not in LEGACY_FIELDS])


def parse_flags(quality: str | None) -> set[str]:
    """解析标记串 → 集合（**读兼容历史别名**：`underexposed` ≡ `no_light`）。"""
    from measure.quality import canonical_flag

    if not quality:
        return set()
    return {canonical_flag(f) for f in quality.split(JOINER) if f}


def format_flags(flags) -> str:
    """标记集合 → 规范串（去重、按枚举顺序、未知标记排末尾）。"""
    known = {f: i for i, f in enumerate(QUALITY_FLAGS)}
    items = {f for f in flags if f}
    return JOINER.join(sorted(items, key=lambda f: (known.get(f, len(QUALITY_FLAGS)), f)))


def is_usable(quality: str | None) -> bool:
    """这一行有没有可用测量（下游门控的便捷函数）。"""
    return not (parse_flags(quality) & set(NON_USABLE_FLAGS))

"""估算层：像素量 → 带不确定度的 mm 估算（**两个现场值必须显式注入**）。

## 铁律（Chief 2026-10-14 裁定）

1. **没有任何内置猜测值**：菌包直径（mm）与俯角（deg）只能由调用方注入；
   缺任一个 ⇒ 该量**写 NULL**（不写 0、不退化上报像素量）；
2. **失效码不许混成一个**（四条，Chief 2026-10-14 追加第 4 条）：
   - `no_scale`：**完全没有标尺**（无参考物 / 找不到同排菌包 / 标准尺寸没注入）⇒ mm 写 NULL；
   - `no_theta`：**有标尺、只是缺俯角 θ** ⇒ 只影响竖直量（写 NULL），水平量照出；
     **它与 `no_scale` 严格分开，且不算"整行不可用"**——旧实现把两者都记成 `no_scale`，
     导致 213/218 帧明明出了有效的 `mean_cluster_w_mm` 却被下游整行丢弃（"把有数据说成没数据"）；
   - `no_detection`：本帧没有可用菇体实例；
   - `unreliable`：有实例但所在排不可信（远排）；
3. 只要产出了估算值：字段级 `estimated_fields` + 行级 `quality` 含 `estimate`（票 18 §2），
   二者**必须同时出现**；`estimate` 与 `verified` 正交，但单独出现**绝不算可用**；
4. 一期交付边界：**相对长势**（同站位跨时间）+ 带不确定度的估算；**绝对 mm 不作验收项**。
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

import numpy as np

from measure import contract
from measure.bag_ruler import BagRegion, nearest_bag_in_same_row
from measure.fruitbody import FruitInstance

FLAG_ESTIMATE = "estimate"

#: **菌包标准尺寸**（不是现场待测量）：仓库既有依据——
#: `src/segmentation/geometry.py:35-40` 的 `BagReference(width_mm, height_mm)`、
#: `src/scripts/processing/run_segmentation_inference.py:440,443` 的默认
#: `--bag-width-mm 240.0` / `--bag-height-mm 140.0`、`src/segmentation/README.md` §5
#: "用菌袋标准尺寸做参考物"。
#: **口径（Chief 2026-10-14 定）**：画面**水平**可见宽 ↔ 240 mm（袋长）；
#: **竖直** ↔ 140 mm（袋径）。两者同深度下应一致，不一致 = 透视/朝向问题，要报出来。
BAG_LENGTH_MM = 240.0
BAG_DIAMETER_MM = 140.0
#: 允许的横/纵尺度不一致比例（超过就报 `scale_inconsistent`）
SCALE_AGREE_TOL = 0.20


@dataclass
class EstimateConfig:
    #: 分割误差（像素）：竖直跨度与参考宽度的 1σ
    span_err_px: float = 3.0
    width_err_px: float = 4.0
    #: 标尺本身相对误差（现场量测给出；None = 未知 ⇒ 不确定度里只报"标尺项未知"）
    bag_diameter_rel_err: float | None = None
    #: 局部标尺允许的最小/最大 mm/px（防离谱值；不是物理常数，是量级护栏）
    min_mm_per_px: float = 0.02
    max_mm_per_px: float = 5.0


@dataclass
class ScaleResult:
    mm_per_px: float | None
    source: str                     # bag_diameter / none
    ref_mm: float | None
    ref_px: float | None
    flags: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class StipeEstimate:
    mean_len_mm: float | None       # 缺失写 None（NULL），绝不写 0
    mean_len_px: float | None
    n_instances: int
    n_reliable: int
    uncertainty_mm: float | None
    estimated_fields: tuple[str, ...]
    quality: str
    scale: ScaleResult
    rows_used: tuple[int, ...] = ()
    note: str = ""


def local_scale(instance: FruitInstance, bags: list[BagRegion], *,
                bag_diameter_mm: float | None,
                cfg: EstimateConfig | None = None,
                bag_cfg=None) -> ScaleResult:
    """给单个实例定**同排**局部 mm/px。

    ``bag_diameter_mm=None`` ⇒ 不猜，返回 `no_scale`（mm_per_px = None）。
    标尺取**袋体特征宽度**（不是"实例所在行"的跨度：菇体长在袋顶上，那一行只有菇体自己）。
    """
    cfg = cfg or EstimateConfig()
    if bag_diameter_mm is None:
        return ScaleResult(None, "none", None, None, ["no_scale"],
                           "未注入菌包直径（mm）")
    bag = nearest_bag_in_same_row(bags, instance.cy, bag_cfg, x=instance.cx) if bags else None
    if bag is None:
        return ScaleResult(None, "none", None, None, ["no_scale"],
                           "该实例附近找不到同排菌包（禁止跨排借用尺度）")
    ref_px = float(bag.characteristic_width_px() or bag.width_px)
    if ref_px <= 1:
        return ScaleResult(None, "none", None, None, ["no_scale"], "菌包宽度像素无效")
    mm_per_px = float(bag_diameter_mm) / ref_px
    if not (cfg.min_mm_per_px <= mm_per_px <= cfg.max_mm_per_px):
        return ScaleResult(None, "none", float(bag_diameter_mm), ref_px, ["no_scale"],
                           f"局部尺度离谱（{mm_per_px:.3f} mm/px），拒绝使用")
    return ScaleResult(mm_per_px, "bag_diameter", float(bag_diameter_mm), ref_px, [],
                       f"同排菌包 #{bag.row_id} 在 y={instance.cy:.0f} 处宽 {ref_px:.0f}px")


def filter_above_bag_top(instances: list[FruitInstance], bags: list[BagRegion], *,
                         margin_px: float = 10.0) -> tuple[list[FruitInstance], dict]:
    """只留**长在袋肩上方/袋顶线附近**的实例（L3 的袋肩门）。

    为什么必须有这道门（2026-10-14 实测，帧 `20261013_S401_level0_145911.jpg` /
    `20261013_S110_level0_152136.jpg`，2880×1616）：L3 默认配置在袋肩 ROI 上出
    **221–235 个实例/帧**，但按"实例底端 `base_y` 与所属袋的袋顶线"分类，
    **72–83% 落在袋体内部**（袋顶线以下）——那是**袋面纹理**，不是菇体；
    只有 **16–27%** 在袋肩上方。不设门，交付的"菌簇直径"会把整袋宽量进去
    （实测袋级簇宽 291–426 mm、最大 925 mm，明显不是菌簇）。

    规则：
    - 实例归属用 `nearest_bag_in_same_row()`（**禁止跨排**）；
    - `base_y <= 袋顶线 + margin_px` ⇒ 留下（长在肩上的菇是从袋顶往上长的）；
    - 找不到同排袋的实例 ⇒ **丢弃**（fail-closed：证不了它长在袋上，不猜）。

    返回 ``(保留的实例, 诊断 dict)``；诊断里的计数供日志/评估引用。
    """
    kept: list[FruitInstance] = []
    above = inside = unassigned = 0
    for inst in instances:
        bag = nearest_bag_in_same_row(bags, inst.cy, None, x=inst.cx) if bags else None
        if bag is None:
            unassigned += 1
            continue
        bag_top = bag.centroid_y - bag.height_px / 2.0
        if inst.base_y <= bag_top + float(margin_px):
            above += 1
            kept.append(inst)
        else:
            inside += 1
    diag = {"kept": len(kept), "above_bag_top": above,
            "inside_bag_body": inside, "unassigned": unassigned,
            "kept_frac": (len(kept) / len(instances)) if instances else 0.0}
    return kept, diag


def scale_dispersion_rel_err(scales: list[float]) -> float | None:
    """**标尺自身的相对误差**：一帧里逐实例局部 mm/px 的相对散布（1σ/均值）。

    为什么这是主导项（2026-10-14 实测，票 15 §3.2/§13.1）：排内袋宽 CV **中位 0.249**
    （p10 0.148 / p90 0.362）⇒ 袋宽分割（粘连/边缘）比"±3px 实例跨度"大一个量级。
    旧的 `propagate_uncertainty(..., ref_px=None)` **把这一项整个漏掉了**（Δw/w 根本没进公式），
    所以这里从数据里**实测**它，而不是拍一个常数。

    ``len < 2``（只有一个尺度可测）⇒ 返回 None（报"standalone 不可估"，不假装知道）。
    """
    vals = [float(s) for s in scales if s and s > 0]
    if len(vals) < 2:
        return None
    mean = float(np.mean(vals))
    if mean <= 0:
        return None
    return float(np.std(vals, ddof=1) / mean)


def propagate_uncertainty(*, mean_len_px: float | None, ref_px: float | None,
                          mm_per_px: float | None, depression_deg: float | None,
                          cfg: EstimateConfig | None = None) -> tuple[float | None, str]:
    """误差传播：相对不确定度 ≈ √((Δs/s)² + (Δw/w)² + (ΔD/D)²) + (1−cosθ)。

    `ΔD/D` 未注入时**不假装知道**：该项留空并在 note 里说明（不影响其它项）。
    """
    cfg = cfg or EstimateConfig()
    if mean_len_px is None or mm_per_px is None:
        return None, "无像素量或尺度 ⇒ 不确定度不可算"
    terms, notes = [], []
    if mean_len_px > 0:
        terms.append((cfg.span_err_px / mean_len_px) ** 2)
    if ref_px:
        terms.append((cfg.width_err_px / ref_px) ** 2)
    if cfg.bag_diameter_rel_err is not None:
        terms.append(float(cfg.bag_diameter_rel_err) ** 2)
    else:
        notes.append("标尺相对误差未注入（ΔD/D 未计入）")
    rel = math.sqrt(sum(terms)) if terms else 0.0
    if depression_deg is not None:
        rel += 1.0 - math.cos(math.radians(float(depression_deg)))
    else:
        notes.append("俯角未注入（投影项未计入）")
    mm = rel * float(mean_len_px) * float(mm_per_px)
    return float(mm), "；".join(notes)


def estimate_stipe_length(instances: list[FruitInstance], bags: list[BagRegion], *,
                          bag_diameter_mm: float | None,
                          depression_deg: float | None,
                          row_reliable: callable | None = None,
                          cfg: EstimateConfig | None = None) -> StipeEstimate:
    """一帧的"菌杆平均长度"估算。

    ``row_reliable(instance) -> bool``：该实例所在排是否可信（远排传 False）。
    缺省把所有实例视为可信（调用方应显式传入排级判定）。
    """
    cfg = cfg or EstimateConfig()
    flags: set[str] = set()
    if not instances:
        if all_rows_unreliable(bags):
            return StipeEstimate(None, None, 0, 0, None, (),
                                 contract.format_flags({"unreliable"}),
                                 ScaleResult(None, "none", None, None, ["no_scale"]),
                                 note="整帧只剩不可信的排（远排），不出测量")
        return StipeEstimate(None, None, 0, 0, None, (), contract.format_flags({"no_detection"}),
                             ScaleResult(None, "none", None, None, ["no_scale"]),
                             note="本帧没有菇体实例")

    reliable = [i for i in instances if (row_reliable(i) if row_reliable else True)]
    if not reliable:
        return StipeEstimate(None, median_or_none(instances), len(instances), 0, None, (),
                             contract.format_flags({"unreliable"}),
                             ScaleResult(None, "none", None, None, ["no_scale"]),
                             note="实例都在不可信的排上（远排），不出 mm")

    heights = [i.height_px for i in reliable]
    mean_len_px = float(np.mean(heights))
    # 局部尺度：逐实例算，取中位（每个实例只用同排菌包）
    scales = [local_scale(i, bags, bag_diameter_mm=bag_diameter_mm, cfg=cfg) for i in reliable]
    ok_scales = [s for s in scales if s.mm_per_px is not None]
    if not ok_scales:
        flags.add("no_scale")
        return StipeEstimate(None, mean_len_px, len(instances), len(reliable), None, (),
                             contract.format_flags(flags | {"estimate"}),
                             scales[0] if scales else ScaleResult(None, "none", None, None,
                                                                  ["no_scale"]),
                             note="缺标尺/俯角或找不到同排菌包 ⇒ mm 写 NULL")
    mm_per_px = float(np.median([s.mm_per_px for s in ok_scales]))
    ref_px = float(np.median([s.ref_px for s in ok_scales if s.ref_px]))
    cos_t = math.cos(math.radians(float(depression_deg))) if depression_deg is not None else None
    if cos_t is None or cos_t <= 0:
        # **θ 缺 ≠ 没标尺**（Chief 2026-10-14）：本帧标尺是有的，只是投影项补不了
        flags.add("no_theta")
        return StipeEstimate(None, mean_len_px, len(instances), len(reliable), None, (),
                             contract.format_flags(flags | {"estimate"}),
                             ScaleResult(mm_per_px, "bag_diameter",
                                         bag_diameter_mm, ref_px),
                             note="未注入俯角（deg）⇒ 投影无法补偿，mm 写 NULL")
    mean_len_mm = mean_len_px * mm_per_px / cos_t
    unc, unc_note = propagate_uncertainty(mean_len_px=mean_len_px, ref_px=ref_px,
                                          mm_per_px=mm_per_px,
                                          depression_deg=depression_deg, cfg=cfg)
    flags.add("estimate")
    note = f"局部标尺 {mm_per_px:.4f} mm/px（同排菌包，n={len(ok_scales)}），cosθ={cos_t:.3f}"
    if unc_note:
        note += f"；{unc_note}"
    return StipeEstimate(
        mean_len_mm=float(mean_len_mm), mean_len_px=mean_len_px,
        n_instances=len(instances), n_reliable=len(reliable),
        uncertainty_mm=unc, estimated_fields=("mean_len_mm",),
        quality=contract.format_flags(flags),
        scale=ScaleResult(mm_per_px, "bag_diameter", bag_diameter_mm, ref_px),
        rows_used=tuple(sorted({i.cy // 100 for i in reliable})),
        note=note,
    )


def median_or_none(instances: list[FruitInstance]) -> float | None:
    return None if not instances else float(np.median([i.height_px for i in instances]))


def make_row_reliable(bags: list[BagRegion], bag_cfg=None):
    """构造"该实例所在排是否可信"的判定回调（供发射层出 `unreliable`）。

    实例 → 找同排菌包 → 该排可信否。找不到同排菌包 ⇒ 不可信（**不跨排借尺度**）。
    """
    from measure.bag_ruler import row_is_reliable

    def _reliable(inst: FruitInstance) -> bool:
        bag = nearest_bag_in_same_row(bags, inst.cy, bag_cfg, x=inst.cx)
        if bag is None:
            return False
        return row_is_reliable(bags, bag.row_id, bag_cfg)

    return _reliable


def all_rows_unreliable(bags: list[BagRegion], bag_cfg=None) -> bool:
    """整帧是否**只剩不可信的排**（用于"没实例也要出 unreliable 而不是 no_detection"）。"""
    from measure.bag_ruler import row_is_reliable

    if not bags:
        return False
    rows = {r.row_id for r in bags}
    return not any(row_is_reliable(bags, rid, bag_cfg) for rid in rows)


# ---------- 一期验收量：菌簇长度 / 菌簇直径（Chief 2026-10-14 裁定） ----------

@dataclass
class ClusterEstimate:
    """菌簇（一帧内可信菇体的集合）的估算：竖直跨度与横向范围。

    对应契约既有列 `mean_cluster_h_mm`（竖直）与 `mean_cluster_w_mm`（横向），
    **不另开名**（票 11 §1）。
    """

    cluster_h_mm: float | None          # 竖直跨度（需俯角 cosθ 补偿）
    cluster_w_mm: float | None          # 横向范围（水平方向不受俯角影响）
    cluster_h_px: float | None
    cluster_w_px: float | None
    n_instances: int
    uncertainty_h_mm: float | None
    uncertainty_w_mm: float | None
    estimated_fields: tuple[str, ...]
    quality: str
    mm_per_px_x: float | None
    mm_per_px_y: float | None
    scale_note: str = ""
    note: str = ""


def estimate_cluster(instances: list[FruitInstance], bags: list[BagRegion], *,
                     bag_length_mm: float | None = BAG_LENGTH_MM,
                     bag_diameter_mm: float | None = BAG_DIAMETER_MM,
                     depression_deg: float | None,
                     row_reliable: callable | None = None,
                     allow_uncorrected_height: bool = False,
                     cfg: EstimateConfig | None = None) -> ClusterEstimate:
    """一帧可信菇体 → 菌簇竖直跨度 / 横向范围（mm 估算）。

    - **水平**用袋长标准值（240mm）定局部 `mm_per_px_x`；**竖直**用袋径标准值（140mm）
      定 `mm_per_px_y` 作**交叉校验**，两者不一致超过 `SCALE_AGREE_TOL` 就在 note 里报；
    - 竖直跨度还要 **÷cosθ**：`depression_deg=None` ⇒ `cluster_h_mm` 写 NULL + **`no_theta`**
      （**不是 `no_scale`**：标尺是有的，只是投影项缺；横向仍可出，因为水平方向不受俯角影响）；
    - `allow_uncorrected_height=True`（**默认关**，Chief 2026-10-14 裁定 (b)）：θ 未注入时
      改为出**未做 cosθ 补偿的竖直投影跨度**（`h_px × mm_per_px_y`）+ 标记 `height_uncorrected`，
      同时保留 `no_theta`。该值**不是长度**、只能作相对比较（同站位/同档/同 imaging_profile）。
    - 远排（`row_reliable` 为 False）/ 无实例 ⇒ 分别 `unreliable` / `no_detection`。
    """
    cfg = cfg or EstimateConfig()
    flags: set[str] = set()
    if not instances:
        # **远排不许静默消失**：若整帧只剩不可信的排，出 `unreliable` 而不是 `no_detection`
        if all_rows_unreliable(bags):
            return ClusterEstimate(None, None, None, None, 0, None, None, (),
                                   contract.format_flags({"unreliable"}),
                                   None, None, note="整帧只剩不可信的排（远排），不出测量")
        return ClusterEstimate(None, None, None, None, 0, None, None, (),
                               contract.format_flags({"no_detection"}),
                               None, None, scale_note="", note="本帧没有菇体实例")

    reliable = [i for i in instances if (row_reliable(i) if row_reliable else True)]
    if not reliable:
        return ClusterEstimate(None, None, None, None, len(instances), None, None, (),
                               contract.format_flags({"unreliable"}),
                               None, None, note="实例都在不可信的排上（远排），不出 mm")

    ys = [i.cy for i in reliable]
    xs = [i.cx for i in reliable]
    w_px = (max(i.x1 for i in reliable) - min(i.x0 for i in reliable)) or 1
    h_px = (max(i.y1 for i in reliable) - min(i.y0 for i in reliable)) or 1

    if bag_length_mm is None or bag_diameter_mm is None:
        flags.add("no_scale")
        return ClusterEstimate(None, None, h_px, w_px, len(instances), None, None, (),
                               contract.format_flags(flags), None, None,
                               note="缺菌包标准尺寸 ⇒ mm 写 NULL")

    # 每个实例只用**同排**菌包定局部尺度（水平用袋长、竖直用袋径）
    sx, sy = [], []
    for inst in reliable:
        bag = nearest_bag_in_same_row(bags, inst.cy, None, x=inst.cx) if bags else None
        if bag is None:
            continue
        ref_px = float(bag.characteristic_width_px() or bag.width_px)
        if ref_px > 1:
            sx.append(float(bag_length_mm) / ref_px)
            sy.append(float(bag_diameter_mm) / ref_px)
    if not sx:
        flags.add("no_scale")
        return ClusterEstimate(None, None, h_px, w_px, len(instances), None, None, (),
                               contract.format_flags(flags), None, None,
                               note="找不到同排菌包（禁止跨排借用尺度）⇒ mm 写 NULL")
    mpp_x = float(np.median(sx))
    mpp_y = float(np.median(sy))
    disagree = abs(mpp_x - mpp_y) / max(mpp_x, mpp_y)

    # **标尺自身相对误差（Δw/w）**：优先用调用方注入的现场值，否则用本帧逐实例局部尺度的散布
    # （2026-10-14 实测：这是主导项 20–30%，旧的 ref_px=None 写法把它整个漏掉了）
    ref_rel = (float(cfg.bag_diameter_rel_err) if cfg.bag_diameter_rel_err is not None
               else scale_dispersion_rel_err(sx))
    eff_cfg = cfg if ref_rel is None or ref_rel == cfg.bag_diameter_rel_err else dataclasses.replace(
        cfg, bag_diameter_rel_err=ref_rel)
    scale_note = (f"袋长240mm→{mpp_x:.4f} mm/px（水平）；袋径140mm→{mpp_y:.4f} mm/px（竖直，"
                  f"交叉校验，差 {disagree*100:.0f}%）")
    if ref_rel is not None:
        src = "现场注入" if cfg.bag_diameter_rel_err is not None else "本帧逐实例局部尺度散布"
        scale_note += f"；标尺相对误差 Δw/w={ref_rel*100:.0f}%（{src}，n={len(sx)}）"

    w_mm = w_px * mpp_x
    unc_w, unc_w_note = propagate_uncertainty(mean_len_px=w_px, ref_px=None,
                                              mm_per_px=mpp_x,
                                              depression_deg=None, cfg=eff_cfg)
    flags.add("estimate")
    h_mm = unc_h = None
    unc_h_note = ""
    if depression_deg is None:
        # **θ 缺 ≠ 没标尺**（Chief 2026-10-14）：水平量是有效值，不许整行标成不可用
        flags.add("no_theta")
        if allow_uncorrected_height:
            # 裁定 (b)：出**未做 cosθ 补偿的竖直投影跨度**——不是长度、只作相对比较
            h_mm = h_px * mpp_y
            flags.add("height_uncorrected")
            unc_h, unc_h_note = propagate_uncertainty(mean_len_px=h_px, ref_px=None,
                                                      mm_per_px=mpp_y,
                                                      depression_deg=None, cfg=eff_cfg)
            note = ("竖直为**未做 cosθ 补偿的投影跨度（mm）**，不是长度；"
                    "仅可作相对比较（同站位/同档/同 imaging_profile）")
        else:
            note = "未注入俯角（deg）⇒ 竖直跨度写 NULL（水平仍可出：不受俯角影响）"
    else:
        cos_t = math.cos(math.radians(float(depression_deg)))
        h_mm = h_px * mpp_y / max(cos_t, 1e-6)
        unc_h, unc_h_note = propagate_uncertainty(mean_len_px=h_px, ref_px=None,
                                                  mm_per_px=mpp_y,
                                                  depression_deg=depression_deg, cfg=eff_cfg)
        note = f"竖直 ÷cosθ（θ={float(depression_deg):.1f}°）"
    fields = tuple(f for f, v in (("mean_cluster_h_mm", h_mm),
                                  ("mean_cluster_w_mm", w_mm)) if v is not None)
    if unc_w_note:
        note += f"；{unc_w_note}"
    if unc_h_note:
        note += f"；{unc_h_note}"
    return ClusterEstimate(
        cluster_h_mm=h_mm, cluster_w_mm=w_mm, cluster_h_px=h_px, cluster_w_px=w_px,
        n_instances=len(instances), uncertainty_h_mm=unc_h, uncertainty_w_mm=unc_w,
        estimated_fields=fields, quality=contract.format_flags(flags),
        mm_per_px_x=mpp_x, mm_per_px_y=mpp_y, scale_note=scale_note, note=note,
    )

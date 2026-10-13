"""图像质量门控（票 05 / 票 13）：模糊 / 过曝 / 近黑（灯未亮）。

**这一份是帧级可用性的唯一尺度**（Chief 2026-10-13 裁定）：任何别处的"这帧能不能用"
判定都必须对齐本模块，不许造第二套；需要不同阈值就写票据。

## 判据与处置

| 判据 | 含义 | 处置 |
| --- | --- | --- |
| `blurry` | 拉普拉斯方差过低（纹理不足/失焦） | **不可用**（不产测量行） |
| `overexposed` | 接近纯白像素占比过高（加湿期水汽、反光） | **不可用** |
| `no_light` | 整帧近黑（灯未亮/无信息量） | **不可用**——降权会把它放进统计 |

三者的阈值**都不许写成拍脑袋的常数**：当前默认值是占位（沿用既有 100 / 0.05，近黑
0.95），必须用真实历史帧（跨轮次、跨天、跨生长阶段 + 已知坏帧对照）的分布标定，
见票 13。**别拿单张强晕影帧当"近黑"样本**：整屏明暗渐变会让均值判据误判，
所以近黑用"高分位亮度"而不是"均值"。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

#: 阈值标定：2026-10-13 用 218 张线上真机帧（2880×1616，`.scratch/frame-quality-samples/`）
#: 的分布定的，不是拍脑袋。**刻度约定：`lapvar` 必须在 0–255 灰度上算**
#: （`np.asarray(img).mean(axis=2)`），在 0–1 上算会小 65025 倍，绝对下限会失效。
#: 该批实测：`lapvar` min 48.0 / p10 53.7 / p50 71.3 / p90 114.3；
#: ⇒ 旧的默认 100 会拒掉 **78.4%** 的正常帧（中位数本身就低于它）。
#: ⚠️ 这是**当前现场（单档平视 + 矮菇 + 这批光照）**的分布：换角/换灯/相机参数变更后
#: 必须用同一脚本重算（`measure/tools/calibrate_quality_thresholds.py`）。
#: 旧的"严格"模糊门 100 保留为**参考值**但不再作默认：标定证明它拒掉 78.4% 的正常帧。
#: 需要"只看纹理很差的帧"时显式传 `blur_threshold=100`（或更高）。
LEGACY_STRICT_BLUR_THRESHOLD = 100.0
DEFAULT_MAX_OVEREXPOSED = 0.05
#: 近黑（欠曝端）：p99 亮度低于此值（[0,1] 归一化）。ok 帧实测 p99 min = 0.722、
#: 两张真暗帧 p99 = 0.008/0.012 ⇒ 0.12 落在巨大的空隙里，两侧都有 6 倍以上余量。
#: （灰阶口径 ≈ p99 < 31，而 ok 帧最低 184、真暗帧 2–3。）
DEFAULT_DARK_P99 = 0.12
#: **清晰度绝对下限**（票 14，Chief 2026-10-13 加的约束）：相对基线判"塌了"之前，
#: 先看绝对清晰度。没有它，相机**整体**失焦/镜头被污染时，每帧相对自己的基线都
#: "正常"，系统性漂移会被相对判据吃掉。
#: 定标：ok 帧 min = 48.0、p05 = 52.6 ⇒ 取 **45.0**（紧贴正常下限之下，
#: 误杀 0/218），只拦"连正常下限都没到"的帧。判"纹理塌了"主要靠相对判据。
DEFAULT_ABS_LAPVAR_FLOOR = 45.0


def _as_unit(gray: np.ndarray) -> np.ndarray:
    """灰度图统一到 [0,1]（uint8 或已是浮点都吃）。"""
    g = np.asarray(gray, dtype=float)
    if g.size and g.max() > 1.5:
        g = g / 255.0
    return g


def laplacian_variance(gray: np.ndarray) -> float:
    """拉普拉斯方差——经典清晰度指标，越低越模糊。

    **刻度**：请传 0–255 的灰度（`np.asarray(img).mean(axis=2)`）。传 0–1 归一化图会
    小 65025 倍，`DEFAULT_ABS_LAPVAR_FLOOR` 这类绝对阈值就失去意义。
    """
    g = gray.astype(float)
    if g.ndim != 2 or g.shape[0] < 3 or g.shape[1] < 3:
        raise ValueError("需要 2D 灰度图且尺寸 ≥3×3")
    lap = (-4.0 * g[1:-1, 1:-1]
           + g[:-2, 1:-1] + g[2:, 1:-1]
           + g[1:-1, :-2] + g[1:-1, 2:])
    return float(lap.var())


def is_blurry(gray: np.ndarray, threshold: float = DEFAULT_ABS_LAPVAR_FLOOR) -> bool:
    return laplacian_variance(gray) < threshold


def overexposed_fraction(gray: np.ndarray, level: int = 250) -> float:
    """接近纯白的像素占比。"""
    return float((np.asarray(gray) >= level).mean())


def is_overexposed(gray: np.ndarray, max_fraction: float = DEFAULT_MAX_OVEREXPOSED,
                   level: int = 250) -> bool:
    return overexposed_fraction(gray, level) > max_fraction


def p99_brightness(gray: np.ndarray) -> float:
    """高分位亮度（[0,1]）：整帧近黑时它也很低。

    为什么不用均值：真机帧常有整屏明暗渐变（S306 实测边缘近黑、中心亮），
    均值会被"边缘黑"拉低，把一张有信息的帧误判成灯没亮。
    """
    g = _as_unit(gray)
    return float(np.percentile(g, 99)) if g.size else 0.0


def is_dark(gray: np.ndarray, *, dark_p99: float = DEFAULT_DARK_P99) -> bool:
    """整帧近黑（灯未亮/无信息量）⇒ 该帧不可用。"""
    return p99_brightness(gray) < dark_p99


#: 曝光轴的**唯一标记名**（Chief 2026-10-13 裁定：判定可以发生在不同时刻，定义只能一份）。
#: `no_light` 覆盖"欠曝/近黑/灯未亮"这一端，`overexposed` 覆盖另一端——同一个曝光轴两侧。
FLAG_NO_LIGHT = "no_light"
FLAG_OVEREXPOSED = "overexposed"
#: 历史别名：`patrol/framing.py` 的 `judge_frame` 曾用 `underexposed` 指同一件事。
#: 解析（读）时认它，**产出（写）时绝不生成**——保证线上只有一套名字。
LEGACY_ALIASES = {"underexposed": FLAG_NO_LIGHT}


def canonical_flag(flag: str) -> str:
    """把历史别名折回唯一名字（读兼容，写唯一）。"""
    return LEGACY_ALIASES.get(flag, flag)


@dataclass(frozen=True)
class ExposureAssessment:
    """一帧的曝光判定结果（**同时面向测量与取景两处调用者**）。

    为什么把逐帧的 `p99` / `overexposed_fraction` 也带出来：`patrol/framing.py` 的取景
    判断需要这两个量本身（例如"看起来偏暗"就多等一会儿灯）。把量给它，它可以**只读**
    这个结果做判断，而不必自己再定义一套阈值与标记名（Chief 裁定）。
    """

    flags: tuple[str, ...]
    p99: float
    overexposed_fraction: float
    underexposed: bool
    overexposed: bool


def assess_exposure(gray: np.ndarray, *, dark_p99: float = DEFAULT_DARK_P99,
                    max_overexposed: float = DEFAULT_MAX_OVEREXPOSED,
                    over_level: int = 250) -> ExposureAssessment:
    """曝光轴（欠曝端 + 过曝端）的**唯一判定入口**。"""
    p99 = p99_brightness(gray)
    over = overexposed_fraction(gray, level=over_level)
    underexposed = p99 < dark_p99
    overexposed = over > max_overexposed
    flags: list[str] = []
    if underexposed:
        flags.append(FLAG_NO_LIGHT)
    if overexposed:
        flags.append(FLAG_OVEREXPOSED)
    return ExposureAssessment(flags=tuple(flags), p99=p99, overexposed_fraction=over,
                              underexposed=underexposed, overexposed=overexposed)


def frame_quality(gray: np.ndarray, *, blur_threshold: float = DEFAULT_ABS_LAPVAR_FLOOR,
                  max_overexposed: float = DEFAULT_MAX_OVEREXPOSED,
                  dark_p99: float = DEFAULT_DARK_P99) -> tuple[bool, str]:
    """返回 ``(是否可用, 标记串)``；标记串 `+` 分隔（票 11 §3），空串表示通过。

    三判据：清晰度（绝对下限）、欠曝（`no_light`）、过曝（`overexposed`）。
    曝光那两端走 `assess_exposure()`——**唯一一份定义**。
    """
    flags: list[str] = []
    if is_blurry(gray, blur_threshold):
        flags.append("blurry")
    flags.extend(assess_exposure(gray, dark_p99=dark_p99,
                                 max_overexposed=max_overexposed).flags)
    return (not flags), "+".join(flags)


# ---------- 相对化判据（票 14；绝对兜底 + 同站位基线） ----------

@dataclass(frozen=True)
class FrameStats:
    """一帧的客观统计量（相对判据的输入）。"""

    lapvar: float
    p99: float
    overexposed_fraction: float


@dataclass(frozen=True)
class StationBaseline:
    """同站位 + 同角度档的近窗基线（中位数 + MAD，抗少数坏帧污染）。"""

    n: int
    lapvar_median: float
    lapvar_mad: float
    p99_median: float
    p99_mad: float


#: 基线至少要有这么多帧才敢用（票 14 §2）
MIN_BASELINE_FRAMES = 10

#: 拒绝码 / 标记
VERDICT_OK = "ok"
VERDICT_LOW = "low_confidence"
VERDICT_REJECT = "reject"


def frame_stats(gray: np.ndarray) -> FrameStats:
    """灰度帧 → `FrameStats`（统计量走曝光轴与清晰度的同一份定义）。"""
    exposure = assess_exposure(gray)
    return FrameStats(
        lapvar=laplacian_variance(gray),
        p99=exposure.p99,
        overexposed_fraction=exposure.overexposed_fraction,
    )


def station_baseline(stats: Sequence[FrameStats]) -> StationBaseline | None:
    """近窗帧统计 → 基线；空输入返回 None。"""
    if not stats:
        return None
    lap = np.asarray([s.lapvar for s in stats], dtype=float)
    p99 = np.asarray([s.p99 for s in stats], dtype=float)
    return StationBaseline(
        n=len(stats),
        lapvar_median=float(np.median(lap)),
        lapvar_mad=float(np.median(np.abs(lap - np.median(lap)))),
        p99_median=float(np.median(p99)),
        p99_mad=float(np.median(np.abs(p99 - np.median(p99)))),
    )


def relative_verdict(
    now: FrameStats,
    base: StationBaseline | None,
    *,
    dark_p99: float = DEFAULT_DARK_P99,
    abs_lapvar_floor: float = DEFAULT_ABS_LAPVAR_FLOOR,
    min_frames: int = MIN_BASELINE_FRAMES,
    lapvar_sigma: float = 3.5,
    p99_sigma: float = 4.0,
) -> tuple[str, list[str]]:
    """相对判据 → ``(verdict, reasons)``，verdict ∈ ``ok / low_confidence / reject``。

    判定顺序（票 14 §5）：

    1. **绝对兜底先判**：
       - 近黑（`p99 < dark_p99`）⇒ ``reject``（没有信息量）；
       - 清晰度绝对下限（`lapvar < abs_lapvar_floor`）⇒ ``reject``
         —— **这条不能省**：相机整体失焦时每帧相对自己的基线都"正常"，
         只靠相对判据会把系统性漂移放过去（Chief 2026-10-13 约束）；
    2. **基线不足**（`base is None` 或 `n < min_frames`）⇒ ``low_confidence``
       + `baseline_missing`（**不是 ok**：宁可不发 verified）；
    3. **相对异常**：`lapvar < median − k·MAD`（纹理塌了：先扣镜头污/失焦）
       或 `|p99 − median| > k·MAD`（照度漂到本点位没见过的地方）⇒ ``low_confidence``；
    4. 其余 ⇒ ``ok``。

    注意 ``ok`` **不等于** 发 `verified`：`verified` 还要过"站位×时段"抽样验收
    （指标：该站位该时段的人工判可用帧中，被自动门判低纹理的比例 ≤ 既定上限；
    逐帧看图的成本不可接受，只用抽样）。见票 14 §3 与票 11 §3。
    """
    reasons: list[str] = []
    if now.p99 < dark_p99:
        return VERDICT_REJECT, [FLAG_NO_LIGHT]
    if now.lapvar < abs_lapvar_floor:
        reasons.append("below_abs_lapvar_floor")
        return VERDICT_REJECT, reasons
    if base is None or base.n < min_frames:
        return VERDICT_LOW, ["baseline_missing"]
    # MAD 为 0（近窗帧彼此几乎一样）时退回"只比中位数"，避免除零式的误判
    lap_span = lapvar_sigma * base.lapvar_mad
    if lap_span > 0 and now.lapvar < base.lapvar_median - lap_span:
        reasons.append("lapvar_below_baseline")
    p99_span = p99_sigma * base.p99_mad
    if p99_span > 0 and abs(now.p99 - base.p99_median) > p99_span:
        reasons.append("brightness_off_baseline")
    return (VERDICT_LOW, reasons) if reasons else (VERDICT_OK, [])

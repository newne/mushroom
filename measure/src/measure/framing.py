"""图像侧的微调依据：目标亮区质心相对画面中心的偏移（纯 numpy，不依赖 cv2）。

## 为什么先用"亮区质心"

现场一帧里能稳定区分的东西只有**被补光灯打亮的菇床**和它周围偏暗的框体/背景。
所以一期用最朴素的办法：取亮区（`>= level` 分位阈值）的质心，减去画面中心，得到
`dx_px / dy_px`。它不依赖任何模型，换库房、换角度都不用重训；代价是当画面里有
第二块亮区（反光、灯罩）时会偏——所以：

* 亮区占比超出 `max_frac`（画面被照白）或低于 `min_frac`（没打亮/全黑）时**判为找不到目标**，
  而不是硬算一个质心让机构去追；
* 输出带 `confidence`：亮区越接近理想占比、越集中，置信度越高。

真正的检测器（分割/检测模型）到位后，只要实现同样的返回形状即可替换——`patrol` 侧
只认 `FramingError` 那四个字段，不认这里怎么算出来的。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_FRACTION = 0.02     # 亮区占比下限：低于它说明没打亮/全黑
MAX_FRACTION = 0.60     # 亮区占比上限：高于它说明过曝/糊成一片
IDEAL_FRACTION = 0.18   # 理想占比（置信度 1.0 的点）


@dataclass(frozen=True)
class Offset:
    """与 `patrol.framing.FramingError` 同形（`patrol` 不认识这个类，只按字段用）。"""

    dx_px: float = 0.0
    dy_px: float = 0.0
    found: bool = True
    confidence: float = 1.0
    reason: str = ""


def otsu_level(gray: np.ndarray, *, bins: int = 64) -> int:
    """Otsu 阈值：让两类之间方差最大的那个分界。

    为什么不用"中位数与最大值的中点"：亮区一旦占了大半画面，中位数就被亮区同化了，
    阈值会贴到最大值上去，"过曝"这种恰恰最该被识别的情形反而先掉进"没对比度"。
    Otsu 只看直方图的两个峰在哪，与两类各占多少无关。
    """
    g = np.asarray(gray, dtype=float)
    hist, edges = np.histogram(g, bins=bins)
    total = hist.sum()
    if total == 0:
        return 0
    centers = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(hist)
    w1 = total - w0
    valid = (w0 > 0) & (w1 > 0)
    if not valid.any():
        return int(centers[0])
    m0 = np.cumsum(hist * centers)
    m1 = (np.sum(hist * centers) - m0)
    mu0 = np.divide(m0, w0, out=np.zeros_like(m0), where=w0 > 0)
    mu1 = np.divide(m1, w1, out=np.zeros_like(m1), where=w1 > 0)
    var = np.where(valid, w0 * w1 * (mu0 - mu1) ** 2, -1.0)
    return int(centers[int(np.argmax(var))])


def bright_fraction(gray: np.ndarray, level: int = 0) -> float:
    """亮区占比；``level<=0`` 时用 Otsu 阈值。"""
    g = np.asarray(gray, dtype=float)
    thr = level if level > 0 else otsu_level(g)
    return float((g >= thr).mean())


def bright_region_offset(gray: np.ndarray, *, level: int = 0,
                         min_fraction: float = MIN_FRACTION,
                         max_fraction: float = MAX_FRACTION) -> Offset:
    """亮区质心相对画面中心的偏移（像素）。dx>0 = 偏右，dy>0 = 偏**下**。

    判定顺序刻意是"先看能不能用，再算质心"：占比不合格时返回 ``found=False``，
    让 `patrol.framing` 一步都不挪。
    """
    g = np.asarray(gray, dtype=float)
    if g.ndim != 2 or g.shape[0] < 8 or g.shape[1] < 8:
        return Offset(found=False, confidence=0.0, reason="图像太小或不是灰度图")

    lo, hi = float(g.min()), float(g.max())
    if hi - lo < 8.0:
        # 整幅一个亮度：过曝和全黑都落在这里，但两句提示要分开——现场处理方式不同
        # （一个去调曝光/关灯，一个去查补光与相机）。
        if hi >= 200.0:
            return Offset(found=False, confidence=0.0,
                          reason=f"整幅都是亮的（{hi:.0f}），像是过曝，调完曝光再拍")
        return Offset(found=False, confidence=0.0,
                      reason=f"画面几乎没有对比度（{lo:.0f}…{hi:.0f}）")

    thr = level if level > 0 else otsu_level(g)

    mask = g >= thr
    frac = float(mask.mean())
    if frac < min_fraction:
        return Offset(found=False, confidence=0.0,
                      reason=f"亮区只占 {frac:.1%}，像是没打亮")
    if frac > max_fraction:
        return Offset(found=False, confidence=0.0,
                      reason=f"亮区占到 {frac:.1%}，像是过曝/糊了")

    ys, xs = np.nonzero(mask)
    h, w = g.shape
    cx, cy = float(xs.mean()), float(ys.mean())
    dx, dy = cx - (w - 1) / 2.0, cy - (h - 1) / 2.0

    # 置信度：占比离理想值越远越低（0.02 → 0，0.18 → 1）
    confidence = 1.0 - min(1.0, abs(frac - IDEAL_FRACTION) / IDEAL_FRACTION)
    return Offset(dx_px=dx, dy_px=dy, found=True, confidence=float(confidence),
                  reason=f"亮区占比 {frac:.1%}，质心 ({cx:.0f}, {cy:.0f})")


def to_gray(rgb: np.ndarray) -> np.ndarray:
    """RGB(A) → 灰度（Rec.601），供只有彩色帧的相机使用。"""
    a = np.asarray(rgb, dtype=float)
    if a.ndim == 2:
        return a
    if a.ndim != 3 or a.shape[2] < 3:
        raise ValueError("需要 HxW 或 HxWx3+ 的数组")
    return a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114

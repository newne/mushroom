"""L1 对齐：平移优先的 ECC 配准（spec §6 L1；票 11）。

## 它做什么

同一锚点的两帧（**参考帧** = 模板参考帧，**第二帧** = 本帧原图），在**静态背景掩码**上估一个
**平移**变换，并把坐标在两帧之间互相映射。v1 写死 `cv2.MOTION_TRANSLATION`：线性部分恒为
单位阵，**不默认用透视变换**修视角变化。将来要小角度旋转时只换 motion 类型，2×3 接口不变。

## 方向（写死；最容易搞反的就是这里）

- `transform_ref_to_moving`：**第一帧 → 第二帧**，`p2 = M @ [x1, y1, 1]`。
  与 OpenCV 同向：`findTransformECC(reference, moving, …)` 返回的就是它，
  `warpAffine(moving, M, …, WARP_INVERSE_MAP)` 用它采样。
- `to_reference(x, y)`：**第二帧 → 第一帧**（本票交付口径「把第二帧坐标映射回第一帧」）。
- `to_moving(x, y)`：第一帧 → 第二帧（下游把模板几何 / ROI 映射到本帧时用）。

## 为什么低分辨率估、原图出

真机帧 2880×1616，全分辨率 ECC 会吃掉整轮预算（spec §6.1：单帧 ≤3.75 s）。默认按**整数因子**
块平均把长边降到 ≤ `max_estimate_edge_px` 再估，平移量乘因子回到**原图像素**——
下游拿到的 `T` 与 `map_roi()` 一律是原图坐标（spec §6.2「低分辨率估变换 → 映射 ROI →
在原图坐标量测」同一条原则）。

## 掩码（不用菌簇配准）

`background_mask` 是**模板的静态背景掩码**（货架、管线；True = 参与配准），参考帧坐标系。
菌簇本身在长，把它算进配准等于拿变化的东西当基准 ⇒ 调用方必须给静态掩码。

## 失败状态（不抛异常、不硬凑）

| status | 含义 |
| --- | --- |
| `ok` | 变换可用 |
| `registration_failed` | ECC 没收敛 / 数值退化（空白帧、无梯度）/ 线性部分不是单位阵 |
| `view_changed` | 平移超过 `max_shift_px` 上限 ⇒ **不能复用参考帧几何** |
| `registration_unreliable` | 收敛但残差超门（`score < min_score`）或静态背景不足 |

失败时 `transform_ref_to_moving` / `transform_moving_to_ref` 一起置 `None`，下游按 spec §6 的
失败码**拒绝**。`score` **不是唯一放行条件**（P3-1 验收）：上限、掩码覆盖率、收敛性各自独立成门。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from measure.imageops import block_downscale

#: 配准成功
ALIGNED = "ok"
#: ECC 没收敛 / 数值退化
REGISTRATION_FAILED = "registration_failed"
#: 位移超过上限：机位变了，不能复用参考帧几何
VIEW_CHANGED = "view_changed"
#: 收敛了但残差超门，或静态背景不足
REGISTRATION_UNRELIABLE = "registration_unreliable"

_CV2_HINT = (
    "measure.align 需要 OpenCV（cv2）。本仓库按用户裁定用 uv + 国内源装："
    "uv pip install --index-url https://mirrors.aliyun.com/pypi/simple opencv-python-headless"
)


def _cv2():
    """惰性导入 cv2：`measure` 其余模块仍只需 numpy（现场机依赖表的现实）。"""
    try:
        import cv2  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - 环境问题
        raise ImportError(_CV2_HINT) from exc
    return cv2


@dataclass(frozen=True)
class AlignConfig:
    """阈值初值（spec §4.5 同一套冻结纪律：开发集后冻结，冻结后不回改）。"""

    #: 估变换用的长边上限（整数因子块平均；0/None = 原分辨率）
    max_estimate_edge_px: int = 720
    ecc_iterations: int = 100
    ecc_eps: float = 1e-6
    ecc_gauss_filter: int = 5
    #: 平移上限（原图像素）；超过 ⇒ view_changed
    max_shift_px: float = 64.0
    #: 残差门：ECC 相关系数下界
    min_score: float = 0.85
    #: 静态背景覆盖率下界
    min_mask_frac: float = 0.05


@dataclass(frozen=True)
class Alignment:
    """一次配准的结果。**失败态不抛异常**：`status` + `transform=None` 交给下游拒绝。"""

    status: str
    #: 第一帧（参考帧）→ 第二帧（本帧）：p2 = M @ [x1, y1, 1]，原图像素
    transform_ref_to_moving: tuple[tuple[float, float, float], tuple[float, float, float]] | None
    #: 第二帧 → 第一帧，原图像素
    transform_moving_to_ref: tuple[tuple[float, float, float], tuple[float, float, float]] | None
    score: float | None
    #: 参考帧 → 第二帧的平移（原图像素）；失败为 None
    shift_px: tuple[float, float] | None
    #: 原图 / 估计图 的倍率（1 = 没降分辨率）
    estimate_scale: int
    moving_hw: tuple[int, int]
    #: 诊断（各道门的实测值）；失败时写明卡在哪一条
    reasons: tuple[str, ...] = ()
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == ALIGNED

    def to_reference(self, x: float, y: float) -> tuple[float, float]:
        """**第二帧 → 第一帧**（票 11 的交付口径）。"""
        return _apply(self._require(self.transform_moving_to_ref), x, y)

    def to_moving(self, x: float, y: float) -> tuple[float, float]:
        """第一帧 → 第二帧（模板几何 / ROI 映射到本帧）。"""
        return _apply(self._require(self.transform_ref_to_moving), x, y)

    def to_reference_points(self, points: np.ndarray) -> np.ndarray:
        return _apply_points(self._require(self.transform_moving_to_ref), points)

    def to_moving_points(self, points: np.ndarray) -> np.ndarray:
        return _apply_points(self._require(self.transform_ref_to_moving), points)

    def map_roi(self, box: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        """参考帧 ROI → 第二帧整数框，`(x0, y0, x1, y1)` 半开区间（可直接切片）。

        向外取整（min 向下、max 向上）以免切掉内容，再夹到第二帧边界。
        """
        m = self._require(self.transform_ref_to_moving)
        x0, y0, x1, y1 = (float(v) for v in box)
        ax0, ay0 = _apply(m, x0, y0)
        ax1, ay1 = _apply(m, x1, y1)
        h, w = self.moving_hw
        return (max(0, int(np.floor(min(ax0, ax1)))), max(0, int(np.floor(min(ay0, ay1)))),
                min(w, int(np.ceil(max(ax0, ax1)))), min(h, int(np.ceil(max(ay0, ay1)))))

    def _require(self, matrix):
        if matrix is None:
            raise ValueError(f"对齐未通过（status={self.status}）：没有可用的变换；下游必须先看 ok")
        return matrix


def _apply(matrix, x: float, y: float) -> tuple[float, float]:
    (a, b, tx), (c, d, ty) = matrix
    return (a * x + b * y + tx, c * x + d * y + ty)


def _apply_points(matrix, points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    (a, b, tx), (c, d, ty) = matrix
    out = np.empty_like(pts)
    out[:, 0] = a * pts[:, 0] + b * pts[:, 1] + tx
    out[:, 1] = c * pts[:, 0] + d * pts[:, 1] + ty
    return out


def _as_gray(image: np.ndarray) -> np.ndarray:
    """(h, w) / (h, w, 1) / (h, w, 3+) → float32 [0, 1] 单通道（C 连续）。

    灰度权重写死（Rec.601），不用 cv2 的色彩转换：这里**不许**有 BGR/RGB 顺序歧义。
    """
    arr = np.asarray(image)
    if arr.ndim == 3:
        if arr.shape[2] == 1:
            arr = arr[:, :, 0]
        else:
            a = arr[:, :, :3].astype(np.float32)
            if a.max() > 1.5:
                a = a / 255.0
            arr = 0.299 * a[:, :, 0] + 0.587 * a[:, :, 1] + 0.114 * a[:, :, 2]
    if arr.ndim != 2:
        raise ValueError(f"图像必须是 (h, w) 或 (h, w, c)，实际 shape={np.shape(image)}")
    out = arr.astype(np.float32)
    if out.max() > 1.5:
        out = out / 255.0
    return np.ascontiguousarray(out)


def _estimate_factor(h: int, w: int, max_edge: int | None) -> int:
    """块平均的整数因子：把长边降到 `max_edge` 附近，同时短边不降到 64 px 以下。

    取满足 `max(h, w) // factor >= max_edge` 的**最大** factor（再大一档就低于上限了）。
    整数因子是刻意的：块平均无插值、原图↔估计图的倍率精确，平移量乘回去不引入缩放误差。
    """
    if not max_edge or max_edge <= 0:
        return 1
    factor = 1
    while max(h, w) // (factor + 1) >= max_edge and min(h, w) // (factor + 1) >= 64:
        factor += 1
    return factor


def estimate_translation(reference: np.ndarray, moving: np.ndarray,
                         background_mask: np.ndarray, *,
                         config: AlignConfig | None = None) -> Alignment:
    """参考帧 + 第二帧 + 静态背景掩码 → 平移优先的 `Alignment`（原图像素）。

    `background_mask` 必填（参考帧坐标系，True = 参与配准）：spec §6 L1 明令用稳定背景，
    不用菌簇。全帧都算背景时传 `np.ones(shape, bool)`。
    """
    cfg = config or AlignConfig()
    t0 = time.perf_counter()
    ref = _as_gray(reference)
    mov = _as_gray(moving)
    if ref.shape != mov.shape:
        raise ValueError(f"两帧必须同形：reference{ref.shape} vs moving{mov.shape}")
    mask = np.asarray(background_mask, dtype=bool)
    if mask.shape != ref.shape:
        raise ValueError(f"静态掩码必须与帧同形：mask{mask.shape} vs frame{ref.shape}")
    h, w = ref.shape
    factor = _estimate_factor(h, w, cfg.max_estimate_edge_px)

    cv2 = _cv2()
    ref_s = block_downscale(ref, factor)
    mov_s = block_downscale(mov, factor)
    mask_s = block_downscale(mask.astype(np.float32), factor) > 0.5   # 多数块才算背景
    mask_frac = float(mask_s.mean())

    # 门 0：静态背景不足 ⇒ 没资格配准（掩码太空时 ECC 只会给一个"看着收敛"的假结果）
    if mask_frac < cfg.min_mask_frac:
        return _failure(REGISTRATION_UNRELIABLE, factor, (h, w), t0,
                        [f"background_mask_too_small:{mask_frac:.4f}<{cfg.min_mask_frac}"])

    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                int(cfg.ecc_iterations), float(cfg.ecc_eps))
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        score, warp = cv2.findTransformECC(
            np.ascontiguousarray(ref_s, np.float32), np.ascontiguousarray(mov_s, np.float32),
            warp, cv2.MOTION_TRANSLATION, criteria,
            np.ascontiguousarray(mask_s.astype(np.uint8) * 255), int(cfg.ecc_gauss_filter))
    except cv2.error as exc:            # 空白帧 / 无梯度 / NaN：不把异常抛给下游
        return _failure(REGISTRATION_FAILED, factor, (h, w), t0,
                        [f"ecc_error:{_brief(str(exc))}"])
    score = float(score)

    # 低分辨率 → 原图：纯平移按整数因子精确放大（下游只认原图坐标）
    dx = float(warp[0, 2]) * factor
    dy = float(warp[1, 2]) * factor
    diag = [f"ecc_score={score:.4f}", f"shift_px=({dx:.2f},{dy:.2f})",
            f"mask_frac={mask_frac:.4f}", f"estimate_factor={factor}"]

    # 门 1：平移模式下线性部分必须是单位阵（防"偷偷上旋转/缩放/透视"）
    linear = np.asarray(warp, np.float64)[:, :2]
    if not np.allclose(linear, np.eye(2), atol=1e-4):
        return _failure(REGISTRATION_FAILED, factor, (h, w), t0,
                        diag + [f"linear_part_not_identity:{linear.ravel().round(6).tolist()}"],
                        shift=(dx, dy), score=score)

    # 门 2：位移上限 —— 超出就不能复用参考帧几何
    shift_norm = float(np.hypot(dx, dy))
    if shift_norm > cfg.max_shift_px:
        return _failure(VIEW_CHANGED, factor, (h, w), t0,
                        diag + [f"shift_over_limit:{shift_norm:.2f}>{cfg.max_shift_px}"],
                        shift=(dx, dy), score=score)

    # 门 3：残差门（ECC 相关系数；**不是唯一放行条件**，上面两道门各自独立）
    if score < cfg.min_score:
        return _failure(REGISTRATION_UNRELIABLE, factor, (h, w), t0,
                        diag + [f"ecc_score_below_gate:{score:.4f}<{cfg.min_score}"],
                        shift=(dx, dy), score=score)

    return Alignment(
        status=ALIGNED,
        transform_ref_to_moving=((1.0, 0.0, dx), (0.0, 1.0, dy)),
        transform_moving_to_ref=((1.0, 0.0, -dx), (0.0, 1.0, -dy)),
        score=score,
        shift_px=(dx, dy),
        estimate_scale=factor,
        moving_hw=(h, w),
        reasons=tuple(diag),
        elapsed_s=time.perf_counter() - t0,
    )


def _brief(text: str, limit: int = 120) -> str:
    """cv2 报错是多行的：只留最后一行，别把整个栈塞进 reasons。"""
    stripped = text.strip()
    line = stripped.splitlines()[-1].strip() if stripped else ""
    return line[:limit]


def _failure(status: str, factor: int, moving_hw: tuple[int, int], t0: float,
             reasons: list[str], *, shift: tuple[float, float] | None = None,
             score: float | None = None) -> Alignment:
    """失败态：`transform_*` 一起为 None（下游按失败码拒绝，不硬套变换）。"""
    return Alignment(status=status, transform_ref_to_moving=None, transform_moving_to_ref=None,
                     score=score, shift_px=shift, estimate_scale=factor, moving_hw=moving_hw,
                     reasons=tuple(reasons), elapsed_s=time.perf_counter() - t0)

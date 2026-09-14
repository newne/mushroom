"""把相机帧转成可判读的数字（模型读不了图时用；也可作为"相机还正常吗"的体检）。

## 这份分析能证明什么、不能证明什么（**别过度解读**）

- **能**：画面是"有结构的物体"还是"均匀背景"；有没有货架/框那种周期性格线；
  亮度是否落在相机工作区间（近黑/过曝都会让采图无效）；相机是否仍为黑白/红外（R≡G≡B）。
- **不能**：区分"有蘑菇"与"空托盘"——两者都有结构、都有横向格线。
  **"库房里有没有蘑菇"必须由人眼确认**，本脚本只回答"现在的画面长什么样"。

  2026-09-13 实测佐证这一点：滑块在原点（Y=0）与另一位置的两次抓帧，
  统计量几乎完全一致（均值 124.86 / 124.23，块间极差 67 / 66，横向周期 17.7× / 17.9×）
  ——**统计量分辨不出场景差异**，所以它不能当"有没有蘑菇"的判据。

用法：python3 frame-stats.py <图片路径> [<图片路径> ...]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image


def block_means(gray: np.ndarray, rows: int = 6, cols: int = 8) -> np.ndarray:
    h, w = gray.shape
    out = np.zeros((rows, cols))
    for i in range(rows):
        for j in range(cols):
            tile = gray[i * h // rows:(i + 1) * h // rows, j * w // cols:(j + 1) * w // cols]
            out[i, j] = tile.mean()
    return out


def gradient_energy(gray: np.ndarray) -> float:
    """平均梯度幅值：结构越多（边缘越多）越大。均匀墙面会很低。"""
    gy, gx = np.gradient(gray.astype(np.float32))
    return float(np.hypot(gx, gy).mean())


def banding(gray: np.ndarray) -> tuple[float, str]:
    """横向格线的强度：货架/框的横档会在行均值序列里形成周期性起伏。"""
    row_mean = gray.mean(axis=1)
    cent = row_mean - row_mean.mean()
    spec = np.abs(np.fft.rfft(cent))
    # 只看"每张图 3–30 条横纹"这个量级（货架横档）
    lo, hi = max(1, len(spec) // 60), max(2, len(spec) // 6)
    band = spec[lo:hi]
    peak = float(band.max()) / (float(spec[1:].mean()) + 1e-9) if len(band) else 0.0
    verdict = "有横向周期结构（货架/框横档）" if peak > 6.0 else "无强横向周期结构"
    return peak, verdict


def report(path: str) -> None:
    img = Image.open(path)
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    rgb = np.asarray(img.convert("RGB"), dtype=np.int16)
    h, w = gray.shape
    print(f"\n=== {Path(path).name} ===")
    print(f"尺寸 {w}×{h}   模式 {img.mode}")
    print(f"灰度：均值 {gray.mean():7.2f}  标准差 {gray.std():7.2f}  "
          f"min {gray.min():.0f} max {gray.max():.0f}")
    dark = float((gray < 16).mean()) * 100
    blown = float((gray > 240).mean()) * 100
    print(f"      近黑(<16) {dark:5.2f}%   近白(>240) {blown:5.2f}%")
    print(f"梯度能量（结构量）: {gradient_energy(gray):.2f}")
    peak, verdict = banding(gray)
    print(f"横向周期性: {peak:.1f}×  → {verdict}")

    # 彩色通道是否相等（黑/红外相机通常 R≡G≡B）
    ch_diff = float(np.abs(rgb[:, :, 0] - rgb[:, :, 1]).mean()
                    + np.abs(rgb[:, :, 1] - rgb[:, :, 2]).mean())
    print(f"通道差 |R-G|+|G-B| 均值: {ch_diff:.3f}  "
          f"→ {'黑白/红外（R≡G≡B）' if ch_diff < 1.0 else '有彩色信息'}")

    print("分块亮度（6 行 × 8 列，看空间分布是否均匀）:")
    bm = block_means(gray)
    for row in bm:
        print("   " + " ".join(f"{v:5.0f}" for v in row))
    spread = bm.max() - bm.min()
    print(f"块间极差 {spread:.0f} → "
          f"{'画面内有明显明暗分区（有物体/阴影）' if spread > 40 else '画面基本均匀'}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    for p in sys.argv[1:]:
        report(p)

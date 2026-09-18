"""判断一张图是不是被"压进了 limited range"（16..235），以及细节到底丢没丢。

用法：python levels.py <图片路径> [<图片路径> ...]

## 为什么单独一个脚本

`frame-stats.py` 回答"画面里有没有结构"；而"图糊/发灰"这种反馈的第一嫌疑是**电平被压**，
不是细节被丢。两件事必须分得开，否则会在相机上白查半天：

- **看端点**（min/max、近黑/近白占比）：limited-range 的 JPEG，数据实测服从
  `out = 16 + in*219/255`。浏览器按 full-range 解码，于是黑停在 16、白停在 235，
  整幅发灰、对比度塌掉 —— 看起来就像"糊"。
- **看拉开电平之后的梯度能量**：若把电平拉开后与 RTSP 直出的帧同量级，说明**细节没丢**，
  纯粹是电平契约错了（我们这边把它拉回来就行，不用去动相机）。

## 判据（2026-09-18 现场实测的锚点）

| 来源 | gray min/max | 近黑(<16)% | 结论 |
| --- | --- | --- | --- |
| RTSP 直出（预览帧） | 0 / 255 | >0 | 正常 full-range |
| XCloudSDK 抓拍（补丁前） | ≈11 / ≈240 | ≈0 | limited range，需要重映射 |
| XCloudSDK 抓拍（补丁后） | ≈0 / ≈255 | >0 | 已修正 |

`SQUEEZE` 这一段就是补丁要做的事（`(v-16)*255/219` 截断到 0..255），打印出来是为了
**不发版也能先看到修复后的样子**。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageFilter

#: limited → full 的还原公式。219 = 235-16，255 = 满量程。
_LUT = [min(255, max(0, round((v - 16) * 255 / 219))) for v in range(256)]


def _pct(hist: list[int], q: float) -> int:
    total = sum(hist)
    acc = 0
    for v, n in enumerate(hist):
        acc += n
        if acc >= total * q:
            return v
    return 255


def _edge_mean(img: Image.Image) -> float:
    """平均梯度幅值（结构越多越大）。固定用同一套算法，before/after 才可比。"""
    edges = img.convert("L").filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    total = sum(hist)
    return sum(v * n for v, n in enumerate(hist)) / total if total else 0.0


def report(path: str) -> None:
    img = Image.open(path)
    gray = img.convert("L")
    hist = gray.histogram()
    total = sum(hist)
    mean = sum(v * n for v, n in enumerate(hist)) / total
    var = sum((v - mean) ** 2 * n for v, n in enumerate(hist)) / total
    lo, hi = gray.getextrema()
    near_black = sum(hist[:16]) / total * 100
    near_white = sum(hist[241:]) / total * 100

    expanded = gray.point(_LUT)

    print(f"\n=== {Path(path).name} ===")
    print(f"  文件 {Path(path).stat().st_size / 1024:8.1f} KB   尺寸 {img.size[0]}×{img.size[1]}"
          f"   模式 {img.mode}")
    print(f"  灰度   均值 {mean:6.2f}  标准差 {var ** 0.5:6.2f}  "
          f"min {lo:3d}  max {hi:3d}  p01 {_pct(hist, 0.01):3d}  p99 {_pct(hist, 0.99):3d}")
    print(f"  端点   近黑(<16) {near_black:6.2f}%   近白(>240) {near_white:6.2f}%")
    print(f"  梯度能量  原图 {_edge_mean(gray):6.3f}   拉开电平后 {_edge_mean(expanded):6.3f}")

    verdict = ("端点贴满 0..255，full-range 正常"
               if lo <= 4 and hi >= 251 else
               "端点缩在 16..235 附近 ⇒ 疑似 limited-range（需要重映射）")
    print(f"  判定   {verdict}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    for p in sys.argv[1:]:
        report(p)

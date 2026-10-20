"""相邻帧长势探索：同一锚点相邻两轮帧 → 顶部轮廓抬升（px），多锚点取中位数。

## 为什么是"相邻帧的轮廓抬升"

真机是**固定站位 + 平视**：菌簇从袋口往上长，画面里表现为**菌簇顶部轮廓上移**。
本工具量的就是这个：同一站位、对齐后的两帧，逐列取前景（亮菌簇）**顶部边界**，
`delta(x) = top_前(x) − top_后(x)`（正 = 后一帧更高），再对列取中位数。

它**不需要袋顶基线**：基线在两帧里是同一个物理位置，作差时自然消掉。
所以这条链路比"绝对簇高"少一个模板依赖，适合先落地看趋势。

## 它不是什么

- **不是 mm**：输出只有原图像素（spec §4.6：实际尺寸映射延后）。
- **不是"同一朵蘑菇"**：相邻帧之间可能有新菇冒出、旧菇被采，量的是**簇级轮廓**，
  不是单株增长；文案按 spec §7 只叫"簇级形态变化"。
- **不替代 L1–L5 主链路**：无模型、无模板、无基线；它只回答"这两帧之间整片有没有长、
  长了多少像素"，用于**趋势与冒烟**，不产 `verified` 行。

## 算法（四步，全部 numpy + Pillow）

1. **质量门**：复用 `measure.quality.frame_quality`（唯一一份判据），坏帧不进对。
2. **对齐**：只用**静态墙带**（ROI 上方 5%–32% 行）做 FFT 相位相关估平移。
   整帧相关会被"正在长的菌簇"带偏 —— 实测 S102 跨天被菌簇拉到 90 px 假位移，
   误判成 `view_changed`。墙带里只有静止结构，位移才是机位重复性误差。
3. **簇顶包络**：在 1/2 降采样图上取亮实体（`壁面 + k·(p99.5−壁面)`），
   **从画面底部向上传播**，只保留与底部连通的亮像素 —— 菌簇是从袋口往上长的
   连通实体，背景里的亮弧/水汽不连通，被自然排除（这是关键：全局/逐列阈值在
   整屏晕影帧上都会把雾层当菌簇顶）。再在原图上对包络做亚像素细化，并加一道
   "上沿对比"门，雾层那种缓边界不算有效列。同时算 k=0.35/0.55 两档作敏感性。
4. **成对增量**：`delta = median_x(top_前 − (top_后(x−dx) + dy))`，同时报
   IQR、有效列占比、左右两半一致性、轮廓形状相关、两个阈值档的差。

## 输出

`frames.csv` / `pairs.csv` / `anchors.csv` / `summary.md` / `trend-{fine,daily}.svg`。
`pairs.csv` 里每个被拒的对都**留着**并带 `status`，不静默丢。

用法（仓库根，只读 console）：

    python measure/tools/explore_growth_adjacent.py --out .scratch/growth-trend \
        --stations S102,S202,S302,S402 \
        --fine-from 2026-10-18T00:00 --fine-to 2026-10-19T15:00

已有本地帧时加 `--frames-dir <dir>`，不重复下载；`--no-download` 只跑本地已有帧。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import math
import statistics as stats
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "src"))  # measure/src

from measure.imageops import block_downscale  # noqa: E402
from measure.quality import frame_quality  # noqa: E402

Image.MAX_IMAGE_PIXELS = None

# ---------------------------------------------------------------- 状态码

OK = "ok"
QUALITY = "frame_quality"
TOO_DARK = "too_dark"
NO_CONTRAST = "no_contrast"
FEW_COLUMNS = "few_valid_columns"
VIEW_CHANGED = "view_changed"
ALIGN_UNRELIABLE = "align_unreliable"
EXPOSURE_CHANGED = "exposure_changed"
CONTRAST_CHANGED = "contrast_changed"
COVERAGE_CHANGED = "coverage_changed"
OUTLIER = "delta_outlier"
INCONSISTENT = "halves_inconsistent"


@dataclass(frozen=True)
class Config:
    #: 连通阈值：壁面 + k·(p99.5 − 壁面)（在降采样图上算）
    k: float = 0.45
    k_lo: float = 0.35
    k_hi: float = 0.55
    #: ROI 比例（相对整帧）；上沿给最高的菌簇留余量，下沿躲开角标
    roi_x0: float = 0.03
    roi_x1: float = 0.97
    roi_y0: float = 0.26
    roi_y1: float = 0.96
    #: 底部连通传播：降采样因子、横向容差（粗像素）、底部种子占画面高度比例
    prop_factor: int = 2
    prop_dilate_r: int = 2
    prop_seed_frac: float = 0.10
    #: 上沿对比门：边界下 3–9 行与上 9–3 行的均值差 ≥ 此比例 × 帧对比度
    edge_frac: float = 0.12
    #: ROI 平均亮度绝对下限（灯未亮/近黑帧；比 p99 更抗角标文字）
    min_luma: float = 40.0
    #: 两帧对比度（p99.5−壁面）相对变化上限；超了多半是水汽/照度整体变了
    max_span_ratio_dev: float = 0.60
    #: 对齐：只用**静态墙带**估平移（避开正在长的菌簇），降采样因子、位移上限、峰下界
    align_y0: float = 0.05
    align_y1: float = 0.32
    align_factor: int = 4
    max_shift_px: float = 40.0
    min_align_peak: float = 4.0
    #: 对标门限
    min_valid_ratio: float = 0.15
    #: 两帧有效列数之比下限（低于此值 ⇒ 追的不是同一片结构）
    min_coverage_ratio: float = 0.50
    max_luma_ratio_dev: float = 0.25
    #: 单对位移上限（px）；日尺度真长势可以到 100+，这里只挡"换了东西"
    max_growth_px: float = 150.0
    #: 两半一致性：两半中位数之差超过此值 ⇒ 不一致
    max_half_gap_px: float = 12.0


# ---------------------------------------------------------------- 索引与下载


def http_get(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as fh:
        return fh.read()


def fetch_index(base: str, station: str) -> list[dict]:
    query = urllib.parse.urlencode({"station_id": station, "limit": 2000})
    payload = json.loads(http_get(f"{base}/api/images?{query}").decode("utf-8"))
    if payload.get("prod_error"):
        raise RuntimeError(f"索引拉取失败（{station}）：{payload['prod_error']}")
    return [r for r in payload.get("rows") or [] if r.get("ok") == 1 and r.get("object_name")]


def frame_path(frames_dir: Path, row: dict) -> Path:
    ts = str(row["ts"])
    return frames_dir / f"{ts[:10].replace('-', '')}_{row['station_id']}_{ts[11:19].replace(':', '')}.jpg"


def download_missing(base: str, rows: list[dict], frames_dir: Path, workers: int) -> None:
    todo = [r for r in rows if not frame_path(frames_dir, r).exists()]
    if not todo:
        return
    frames_dir.mkdir(parents=True, exist_ok=True)
    print(f"  下载 {len(todo)} 帧（workers={workers}）…")

    def one(row: dict) -> str:
        dest = frame_path(frames_dir, row)
        try:
            data = http_get(f"{base}/api/image?object_name={urllib.parse.quote(row['object_name'])}")
        except Exception as exc:  # noqa: BLE001 - 单帧失败不该毁整批
            return f"FAIL {row['station_id']} {row['ts']}: {exc}"
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return ""

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        failures = [msg for msg in ex.map(one, todo) if msg]
    print(f"  下载完成 {len(todo) - len(failures)}/{len(todo)}，{time.time() - t0:.1f}s")
    for msg in failures[:10]:
        print("   ", msg)


# ---------------------------------------------------------------- 帧特征


def load_gray(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"), dtype=np.float32)


def phase_shift(ref: np.ndarray, mov: np.ndarray, cfg: Config) -> tuple[float, float, float]:
    """`ref` 相对 `mov` 的平移 `(dy, dx)` 与相位相关峰比。

    **只拿静态墙带估平移**（`align_y0..align_y1`）：整帧相关会被"正在往上长的菌簇"
    带偏 —— 实测 S102 跨天被菌簇拉到 90 px 假位移，误判成 view_changed；墙带里
    只有静止结构，位移才是机位真实重复性误差。

    约定（**符号最容易搞反，自检见 `measure/tests/test_explore_growth_adjacent.py`**）：
    `ref(y, x) ≈ mov(y − dy, x − dx)`。降采样估计、乘回原图尺度。
    等价写法：`mov` 是 `ref` 平移 `(−dy, −dx)` 的结果；把 `mov` 对齐回 `ref` 后，
    `t_mov_aligned(x) = t_mov(x − dx) + dy`。
    """
    h_full = ref.shape[0]
    y0 = int(cfg.align_y0 * h_full)
    y1 = int(cfg.align_y1 * h_full)
    f = cfg.align_factor
    a = block_downscale(ref[y0:y1], f).copy()
    b = block_downscale(mov[y0:y1], f).copy()
    h, w = a.shape
    # 遮掉角标时间戳（每帧文字不同）与底部标签，别让它们带偏全局平移
    top = max(2, h // 12)
    a[:top, : max(2, w // 6)] = float(np.median(a))
    b[:top, : max(2, w // 6)] = float(np.median(b))
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32) + 1e-6
    fa = np.fft.rfft2((a - a.mean()) * win)
    fb = np.fft.rfft2((b - b.mean()) * win)
    r = fa * np.conj(fb)
    r /= np.maximum(np.abs(r), 1e-12)
    c = np.fft.irfft2(r, s=(h, w))
    iy, ix = np.unravel_index(int(np.argmax(c)), c.shape)
    peak = float(c[iy, ix] / max(float(c.mean()), 1e-12))

    def sub(v0: float, vm: float, vp: float) -> float:
        den = vm - 2.0 * v0 + vp
        return 0.0 if abs(den) < 1e-12 else 0.5 * (vm - vp) / den

    dy = float(iy) + sub(c[iy, ix], c[(iy - 1) % h, ix], c[(iy + 1) % h, ix])
    dx = float(ix) + sub(c[iy, ix], c[iy, (ix - 1) % w], c[iy, (ix + 1) % w])
    if dy > h / 2:
        dy -= h
    if dx > w / 2:
        dx -= w
    return dy * f, dx * f, peak


def _smooth_rows(gray: np.ndarray, radius: int = 2) -> np.ndarray:
    """列内竖直盒平滑（累计和实现）。"""
    if radius <= 0:
        return gray
    k = 2 * radius + 1
    pad = np.pad(gray, ((radius, radius), (0, 0)), mode="edge")
    c = np.cumsum(pad, axis=0, dtype=np.float32)
    c = np.vstack([np.zeros((1, gray.shape[1]), np.float32), c])
    return (c[k:] - c[:-k]) / float(k)


def _dilate1d(row: np.ndarray, r: int) -> np.ndarray:
    out = row.copy()
    for d in range(1, r + 1):
        out[d:] |= row[:-d]
        out[:-d] |= row[d:]
    return out


@dataclass
class Segmentation:
    """一帧的分割证据：粗分辨率两套掩码 + 全分辨率顶部包络。

    掩码留在**粗分辨率**（1/2），只用于可视化与诊断；量测用全分辨率 `top`。
    """

    top: np.ndarray                  # 全分辨率列，ROI 坐标 y；无效 nan
    n_valid: int
    wall: float
    span: float
    thr: float
    coarse: np.ndarray               # (h/2, w/2) ROI 灰度
    mask: np.ndarray                 # 粗分辨率：过阈亮实体
    keep: np.ndarray                 # 粗分辨率：与底部连通的那部分
    env_coarse: np.ndarray           # 全分辨率列：粗包络（已按 prop_factor 展开）
    edge_ok: np.ndarray              # 全分辨率列：上沿对比门
    has_cross: np.ndarray            # 全分辨率列：亚像素交点存在


def segment_cluster(roi: np.ndarray, cfg: Config, k: float,
                    sm: np.ndarray | None = None) -> Segmentation:
    """**底部连通的亮实体**分割 + 顶包络（原图坐标，像素）。

    为什么不是"每列第一个过阈像素"：墙/水汽在整屏晕影下会整体被抬过阈值，
    逐列阈值也救不了（实测 S102 跨天帧的雾层被当成簇顶，虚高 200+ px）。
    菌簇是**从袋口往上长的连通实体**，所以从画面底部向上传播：
    只保留与底部相连的亮像素，背景里的亮弧/雾层不连通，自然被排除。

    `sm` 可传入已算好的竖直平滑图（三档阈值共用，省两次全图平滑）。
    """
    coarse = block_downscale(roi, cfg.prop_factor)
    h, w = coarse.shape
    wall = float(np.median(coarse[: max(1, h // 4)]))
    span = float(np.percentile(coarse, 99.5) - wall)
    thr = wall + k * span
    empty = np.full(w, np.nan, np.float32)
    if span < 25.0:
        return Segmentation(empty, 0, wall, span, thr, coarse,
                            np.zeros_like(coarse, dtype=bool),
                            np.zeros_like(coarse, dtype=bool),
                            empty, np.zeros(w, bool), np.zeros(w, bool))

    mask = coarse > thr
    seed_rows = max(2, int(cfg.prop_seed_frac * h))
    seed = mask[h - seed_rows:].any(axis=0)
    keep = np.zeros_like(mask)
    for y in range(h - 1, -1, -1):
        row = mask[y] & _dilate1d(seed, cfg.prop_dilate_r)
        keep[y] = row
        seed = row
    has = keep.any(axis=0)
    env = np.argmax(keep, axis=0).astype(np.float32) * cfg.prop_factor
    env[~has] = np.nan
    env[env < 3.0] = np.nan

    # 亚像素 + 上沿对比门：用原图竖直平滑列，在粗包络 ±5 px 内找
    # "暗→亮"的过中点位置；对比不足（雾层边界）就不算有效列。
    if sm is None:
        sm = _smooth_rows(roi, 2)
    H, W = sm.shape
    env = np.repeat(env, cfg.prop_factor)          # 粗列 → 原图列
    if env.shape[0] < W:
        env = np.concatenate([env, np.full(W - env.shape[0], np.nan, np.float32)])
    env = env[:W]
    cols = np.arange(W)
    csum = np.vstack([np.zeros((1, W), np.float32),
                      np.cumsum(sm.astype(np.float32), axis=0, dtype=np.float32)])

    def win_mean(y0: np.ndarray, y1: np.ndarray) -> np.ndarray:
        y0 = np.clip(np.round(y0), 0, H).astype(np.int32)
        y1 = np.clip(np.round(y1), 0, H).astype(np.int32)
        return (csum[y1, cols] - csum[y0, cols]) / np.maximum(y1 - y0, 1)

    t = np.nan_to_num(env, nan=H).astype(np.float32)
    below = win_mean(t + 3, t + 9)
    above = win_mean(t - 9, t - 3)
    edge = below - above
    strong = np.isfinite(env) & (edge >= cfg.edge_frac * span)

    level = (below + above) / 2.0
    offs = np.arange(-5, 4, dtype=np.int32)
    ys = np.clip(t[:, None].astype(np.int32) + offs[None, :], 0, H - 1)
    vals = sm[ys, cols[:, None]]
    is_above = vals > level[:, None]
    prev_below = np.ones_like(is_above)
    prev_below[:, 1:] = ~is_above[:, :-1]
    cross = is_above & prev_below
    has_cross = cross.any(axis=1)
    idx = np.argmax(cross, axis=1)
    y1 = ys[cols, idx].astype(np.float32)
    y0 = np.clip(y1 - 1, 0, H - 1)
    v0 = sm[y0.astype(np.int32), cols]
    v1 = sm[y1.astype(np.int32), cols]
    den = v1 - v0
    frac = np.full(W, 0.5, np.float32)
    np.divide(level - v0, den, out=frac, where=np.abs(den) > 1e-6)
    refined = y0 + np.clip(frac, 0.0, 1.0)
    top = np.where(has_cross, refined, t).astype(np.float32)
    top[~strong] = np.nan
    return Segmentation(top, int(np.isfinite(top).sum()), wall, span, thr,
                        coarse, mask, keep, env, strong, has_cross)


def cluster_top(roi: np.ndarray, cfg: Config, k: float,
                sm: np.ndarray | None = None) -> tuple[np.ndarray, int, float, float]:
    """兼容包装：只取 `(top, n_valid, thr, span)`。"""
    seg = segment_cluster(roi, cfg, k, sm=sm)
    return seg.top, seg.n_valid, seg.thr, seg.span


@dataclass
class Profile:
    ts: str
    station: str
    path: str
    status: str = OK
    flags: str = ""
    thr: float = math.nan
    span: float = math.nan
    wall: float = math.nan
    luma: float = math.nan
    top: np.ndarray | None = None          # 每列亚像素顶部 y（原图坐标），无效为 nan
    top_lo: np.ndarray | None = None
    top_hi: np.ndarray | None = None
    n_valid: int = 0

    @property
    def ok(self) -> bool:
        return self.status == OK and self.top is not None


def profile_frame(path: Path, row: dict, cfg: Config) -> Profile:
    p = Profile(ts=str(row["ts"]), station=str(row["station_id"]), path=str(path))
    gray = load_gray(path)
    h, w = gray.shape
    ok, flags = frame_quality(gray)
    p.flags = flags
    if not ok:
        p.status = QUALITY
        return p
    x0, x1 = int(cfg.roi_x0 * w), int(cfg.roi_x1 * w)
    y0, y1 = int(cfg.roi_y0 * h), int(cfg.roi_y1 * h)
    roi = gray[y0:y1, x0:x1]
    p.luma = float(roi.mean())
    if p.luma < cfg.min_luma:
        p.status = TOO_DARK
        return p
    p.wall = float(np.median(roi[: max(1, roi.shape[0] // 4)]))
    p.span = float(np.percentile(roi, 99.5) - p.wall)
    if p.span < 25.0:                     # 壁面与菌簇几乎同亮度：没有可分的前景
        p.status = NO_CONTRAST
        return p
    sm = _smooth_rows(roi, 2)
    for k, key in ((cfg.k, "top"), (cfg.k_lo, "top_lo"), (cfg.k_hi, "top_hi")):
        top, n_valid, thr, span = cluster_top(roi, cfg, k, sm=sm)
        if key == "top":
            p.thr, p.span, p.n_valid = thr, span, n_valid
        setattr(p, key, top + y0)
    if p.n_valid < 20:
        p.status = FEW_COLUMNS
    return p


# ---------------------------------------------------------------- 成对量测


@dataclass
class PairResult:
    station: str
    ts_a: str
    ts_b: str
    dt_h: float
    status: str = OK
    delta_px: float = math.nan
    delta_p25: float = math.nan
    delta_p75: float = math.nan
    delta_mean: float = math.nan
    delta_k_lo: float = math.nan
    delta_k_hi: float = math.nan
    delta_left: float = math.nan
    delta_right: float = math.nan
    valid_ratio: float = 0.0
    profile_corr: float = math.nan
    shift_dy: float = math.nan
    shift_dx: float = math.nan
    align_peak: float = math.nan


def delta_profile(ta: np.ndarray, tb: np.ndarray, dy: float,
                  dx: float) -> tuple[np.ndarray, np.ndarray]:
    """把 B 对齐回 A 后的逐列差值 `d(x)`；无效列为 nan。

    指标与可视化**共用这一条路径**，避免画出来的图和表里的数不是同一份。
    """
    n = min(ta.shape[0], tb.shape[0])
    x = np.arange(n, dtype=np.float64)
    finite = np.isfinite(tb)
    d = np.full(n, np.nan, np.float32)
    if finite.sum() < 2:
        return d, np.zeros(n, bool)
    # t_B_aligned(x) = t_B(x − dx) + dy（见 phase_shift 的约定）。
    # `np.interp` 会把 B 的**无效段跨过去线性补值**，补出来的列不是量测值，
    # 必须按"最近原始列是否有效"再筛一次，否则空洞被填成平滑弧线混进中位数。
    xi = np.clip(np.round(x - dx).astype(np.int32), 0, n - 1)
    sample_valid = finite[xi]
    tb_shift = np.interp(x, (x - dx)[finite], tb[finite], left=np.nan, right=np.nan)
    d = ta[:n] - (tb_shift + dy)
    d[~sample_valid] = np.nan
    return d.astype(np.float32), np.isfinite(d)


def measure_delta(ta: np.ndarray, tb: np.ndarray, dy: float,
                  dx: float) -> tuple[float, dict]:
    """A（较早）与 B（较晚）的逐列顶部抬升；返回 (median, 诊断)。"""
    n = min(ta.shape[0], tb.shape[0])
    d, valid = delta_profile(ta, tb, dy, dx)
    if valid.sum() < 20:
        return math.nan, {"n": int(valid.sum())}
    v = d[valid]
    q25, q50, q75 = np.percentile(v, [25, 50, 75])
    left_vals = d[: n // 2][np.isfinite(d[: n // 2])]
    right_vals = d[n // 2:][np.isfinite(d[n // 2:])]
    # 轮廓形状相关：同一片菌簇的两条顶部轮廓应当同形；掉到 0.3 以下多半是
    # 换了一个结构（袋沿/反光）在跟踪，delta 不可信。仅作诊断，不当硬门。
    a_v = ta[:n][valid]
    b_v = a_v - d[valid]              # = 对齐后的 B 轮廓（d = A − B_aligned）
    corr = (float(np.corrcoef(a_v, b_v)[0, 1])
            if a_v.size > 2 and a_v.std() > 1e-6 and b_v.std() > 1e-6 else math.nan)
    return float(q50), {
        "n": int(valid.sum()), "p25": float(q25), "p50": float(q50), "p75": float(q75),
        "mean": float(v.mean()),
        "left": float(np.median(left_vals)) if left_vals.size else math.nan,
        "right": float(np.median(right_vals)) if right_vals.size else math.nan,
        "valid_ratio": float(valid.sum() / n),
        "corr": corr,
    }


def measure_pair(pa: Profile, pb: Profile, cfg: Config) -> PairResult:
    dt_h = (datetime.fromisoformat(pb.ts) - datetime.fromisoformat(pa.ts)).total_seconds() / 3600.0
    res = PairResult(station=pa.station, ts_a=pa.ts, ts_b=pb.ts, dt_h=round(dt_h, 3))
    if not pa.ok or not pb.ok:
        res.status = QUALITY if (pa.status == QUALITY or pb.status == QUALITY) else (
            pa.status if pa.status != OK else pb.status)
        return res
    if min(pa.n_valid, pb.n_valid) < 20:
        res.status = FEW_COLUMNS
        return res
    # 有效列数（能拿起轮廓的列）差太多 ⇒ 两帧在追的不是同一片结构；
    # S206 03→06 实测 2041→739，delta 假到 −66 px。
    cov = min(pa.n_valid, pb.n_valid) / max(pa.n_valid, pb.n_valid)
    if cov < cfg.min_coverage_ratio:
        res.status = COVERAGE_CHANGED
        return res
    # 阈值漂移（本列 p20/p98 的中位）只作诊断：菌簇长大后亮点比例会变，
    # 中位阈值本来就会动；拿它当硬门会把"正在长"的帧整对拒掉。
    if abs(pb.span / max(pa.span, 1e-6) - 1.0) > cfg.max_span_ratio_dev:
        res.status = CONTRAST_CHANGED
        return res
    if abs(pb.luma / max(pa.luma, 1e-6) - 1.0) > cfg.max_luma_ratio_dev:
        res.status = EXPOSURE_CHANGED
        return res

    ga = load_gray(Path(pa.path))
    gb = load_gray(Path(pb.path))
    dy, dx, peak = phase_shift(ga, gb, cfg)
    res.shift_dy, res.shift_dx, res.align_peak = dy, dx, peak
    if peak < cfg.min_align_peak:
        res.status = ALIGN_UNRELIABLE
        return res
    if max(abs(dy), abs(dx)) > cfg.max_shift_px:
        res.status = VIEW_CHANGED
        return res

    delta, diag = measure_delta(pa.top, pb.top, dy, dx)
    if not math.isfinite(delta):
        res.status = FEW_COLUMNS
        return res
    res.delta_px = delta
    res.delta_p25 = diag["p25"]
    res.delta_p75 = diag["p75"]
    res.delta_mean = diag["mean"]
    res.delta_left = diag["left"]
    res.delta_right = diag["right"]
    res.valid_ratio = diag["valid_ratio"]
    res.profile_corr = diag.get("corr", math.nan)
    if diag["valid_ratio"] < cfg.min_valid_ratio:
        res.status = FEW_COLUMNS
        return res
    if abs(delta) > cfg.max_growth_px:
        res.status = OUTLIER
        return res
    # 左右两半一致性：日尺度上两簇本来就长得不一样，用"相对 delta"判，
    # 而不是固定 12 px —— 否则长得快的那一天反而被当成异常丢掉。
    half_gap = (abs(res.delta_left - res.delta_right)
                if math.isfinite(res.delta_left) and math.isfinite(res.delta_right)
                else 0.0)
    if half_gap > max(cfg.max_half_gap_px, 0.75 * abs(delta)):
        res.status = INCONSISTENT
        return res

    res.delta_k_lo = measure_delta(pa.top_lo, pb.top_lo, dy, dx)[0]
    res.delta_k_hi = measure_delta(pa.top_hi, pb.top_hi, dy, dx)[0]
    return res


# ---------------------------------------------------------------- 选帧与聚合


def parse_dt(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", ""))


def _dedup_sorted(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out = []
    for r in sorted(rows, key=lambda x: str(x["ts"])):
        key = (str(r.get("station_id")), str(r["ts"]))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def pick_fine(rows: list[dict], start: datetime, end: datetime) -> list[dict]:
    return _dedup_sorted([r for r in rows if start <= parse_dt(str(r["ts"])) <= end])


def pick_daily(rows: list[dict], start_date: str, end_date: str,
               at_hour: int = 12) -> list[dict]:
    """每天取最接近 `at_hour` 的一轮（同一站位）。"""
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        ts = parse_dt(str(r["ts"]))
        day = ts.date().isoformat()
        if start_date <= day <= end_date:
            by_day.setdefault(day, []).append(r)
    picked = []
    for day in sorted(by_day):
        best = min(by_day[day], key=lambda r: abs(parse_dt(str(r["ts"])).hour
                                                 + parse_dt(str(r["ts"])).minute / 60 - at_hour))
        picked.append(best)
    return _dedup_sorted(picked)


def adjacent_pairs(frames: list[dict], max_gap_h: float) -> list[tuple[dict, dict]]:
    out = []
    for a, b in zip(frames, frames[1:]):
        dt = (parse_dt(str(b["ts"])) - parse_dt(str(a["ts"]))).total_seconds() / 3600.0
        if 0 < dt <= max_gap_h:
            out.append((a, b))
    return out


def anchor_curves(pairs: list[PairResult], min_dt: float = 0.0
                  ) -> tuple[dict[str, list[tuple[str, float]]], set[str]]:
    """每个锚点：按时间累加通过门限的 delta（被拒的对不加，曲线留平台）。

    返回 `(curves, valid)`：`valid` 是**至少有一对通过**的锚点。聚合时只算这些——
    否则"整段都是坏帧"的锚点会以 0 参与中位数，把整体长势往零拉。
    """
    by_st: dict[str, list[PairResult]] = {}
    for p in pairs:
        by_st.setdefault(p.station, []).append(p)
    curves: dict[str, list[tuple[str, float]]] = {}
    valid: set[str] = set()
    for station, ps in by_st.items():
        ps = sorted(ps, key=lambda r: r.ts_b)
        cum = 0.0
        pts = [(ps[0].ts_a, 0.0)] if ps else []
        for p in ps:
            if p.status == OK and p.dt_h >= min_dt:
                cum += p.delta_px
                valid.add(station)
            pts.append((p.ts_b, cum))
        curves[station] = pts
    return curves, valid


def hour_bucket(ts: str) -> str:
    """把站位各差几分钟的同一轮并到一起（同一小时的帧算同一轮）。

    不做这一步，`median_curve` 在每个 ts 上只会看到**一个锚点**（S101 的 12:20:16
    与 S106 的 12:20:54 是两个 ts），中位数就退化成单锚点，多锚点聚合是假的。
    """
    return ts[:13] + ":00"


def median_curve(curves: dict[str, list[tuple[str, float]]],
                 valid: set[str] | None = None) -> list[tuple[str, float, int]]:
    """按小时桶聚合：每个锚点在桶内取最后一个累计值，再对锚点取中位数。

    `valid` 只保留至少有一对通过的锚点（见 `anchor_curves`）。
    """
    buckets: dict[str, dict[str, float]] = {}
    for station, pts in curves.items():
        if valid is not None and station not in valid:
            continue
        for ts, v in pts:
            buckets.setdefault(hour_bucket(ts), {})[station] = v
    out = []
    for ts in sorted(buckets):
        vals = list(buckets[ts].values())
        out.append((ts, float(np.median(vals)), len(vals)))
    return out


# ---------------------------------------------------------------- 输出


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def round_or_blank(v: float, nd: int = 1):
    return round(v, nd) if isinstance(v, (int, float)) and math.isfinite(v) else ""


def svg_curve(path: Path, curves: dict[str, list[tuple[str, float]]],
              median: list[tuple[str, float, int]], title: str) -> None:
    """无依赖的 SVG 折线：细灰=各锚点，粗蓝=锚点中位数。"""
    W, H, L, R, T, B = 1000, 420, 70, 20, 28, 50
    if not median:
        path.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        return
    t0 = parse_dt(median[0][0])
    t1 = parse_dt(median[-1][0])
    span = max((t1 - t0).total_seconds(), 1.0)
    vals = [v for _, v, _ in median] + [v for pts in curves.values() for _, v in pts]
    lo, hi = min(vals), max(vals)
    pad = max((hi - lo) * 0.1, 1.0)
    lo, hi = lo - pad, hi + pad

    def x_of(ts: str) -> float:
        return L + (parse_dt(ts) - t0).total_seconds() / span * (W - L - R)

    def y_of(v: float) -> float:
        return T + (hi - v) / (hi - lo) * (H - T - B)

    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' "
             f"viewBox='0 0 {W} {H}' font-family='sans-serif' font-size='12'>",
             f"<rect width='{W}' height='{H}' fill='white'/>"]
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        y = y_of(v)
        parts.append(f"<line x1='{L}' y1='{y:.1f}' x2='{W - R}' y2='{y:.1f}' stroke='#e8e8e8'/>")
        parts.append(f"<text x='{L - 8}' y='{y + 4:.1f}' text-anchor='end' fill='#666'>{v:.0f}</text>")
    for _, pts in curves.items():
        d = " ".join(f"{x_of(ts):.1f},{y_of(v):.1f}" for ts, v in pts)
        parts.append(f"<polyline points='{d}' fill='none' stroke='#c9c9c9' stroke-width='1'/>")
    d = " ".join(f"{x_of(ts):.1f},{y_of(v):.1f}" for ts, v, _ in median)
    parts.append(f"<polyline points='{d}' fill='none' stroke='#1e5fd0' stroke-width='2.5'/>")
    for i in range(5):
        label = datetime.fromtimestamp(t0.timestamp() + span * i / 4).strftime("%m-%d %H:%M")
        parts.append(f"<text x='{L + (W - L - R) * i / 4:.1f}' y='{H - 14}' text-anchor='middle' "
                     f"fill='#666'>{label}</text>")
    parts.append(f"<text x='{L}' y='{T - 10}' fill='#333'>{title}</text>")
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    chars = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return chars[0] * len(values)
    return "".join(chars[min(len(chars) - 1, int((v - lo) / (hi - lo) * (len(chars) - 1)))]
                   for v in values)


# ---------------------------------------------------------------- 主流程


def run_set(name: str, rows: list[dict], frames_dir: Path, cfg: Config,
            max_gap_h: float, min_dt: float = 0.0
            ) -> tuple[list[dict], list[PairResult], dict[str, list[tuple[str, float]]],
                       set[str]]:
    print(f"→ {name}：{len(rows)} 帧，逐帧轮廓…")
    profs: list[Profile] = []
    t0 = time.time()
    for r in rows:
        path = frame_path(frames_dir, r)
        if not path.exists():
            continue
        profs.append(profile_frame(path, r, cfg))
    ok_n = sum(p.ok for p in profs)
    print(f"  {len(profs)} 帧（可用 {ok_n}），{time.time() - t0:.1f}s")
    frame_rows = [{
        "station": p.station, "ts": p.ts, "status": p.status, "flags": p.flags,
        "thr": round_or_blank(p.thr), "span": round_or_blank(p.span),
        "wall": round_or_blank(p.wall), "luma": round_or_blank(p.luma),
        "n_valid": p.n_valid,
        "top_median_px": round_or_blank(float(np.nanmedian(p.top))) if p.ok else "",
    } for p in profs]

    by_st: dict[str, list[Profile]] = {}
    for p in profs:
        by_st.setdefault(p.station, []).append(p)
    pairs: list[PairResult] = []
    for st, ps in by_st.items():
        ps = sorted(ps, key=lambda x: x.ts)
        index = {p.ts: p for p in ps}
        for a, b in adjacent_pairs([{"ts": p.ts, "station_id": st} for p in ps], max_gap_h):
            pairs.append(measure_pair(index[a["ts"]], index[b["ts"]], cfg))
    print(f"  相邻对 {len(pairs)}，通过 {sum(p.status == OK for p in pairs)}")
    curves, valid = anchor_curves(pairs, min_dt)
    return frame_rows, pairs, curves, valid


def main() -> int:
    try:                                  # Windows 控制台默认 GBK，中文/✓ 会炸
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="相邻帧簇顶轮廓抬升（只读历史帧，无模型）")
    ap.add_argument("--base", default="http://10.77.77.39:8002")
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames-dir", default=None, help="本地帧缓存目录（缺省 <out>/frames）")
    ap.add_argument("--stations",
                    default="S102,S106,S111,S202,S206,S211,S302,S306,S311,S402,S406,S411")
    ap.add_argument("--fine-from", default="", help="细粒度相邻帧窗口起点（ISO）")
    ap.add_argument("--fine-to", default="", help="细粒度窗口终点（ISO）")
    ap.add_argument("--daily-from", default="", help="日尺度起点（YYYY-MM-DD）")
    ap.add_argument("--daily-to", default="", help="日尺度终点（YYYY-MM-DD）")
    ap.add_argument("--daily-hour", type=int, default=12,
                    help="日尺度取样时刻（默认 12）。灯窗会漂移，固定时刻可能整帧 too_dark；"
                         "本批 12 点对 layer3/4 常不可用，改用 15 点")
    ap.add_argument("--max-gap-h", type=float, default=4.5, help="细粒度相邻对最大间隔（小时）")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--k", type=float, default=0.45)
    ap.add_argument("--edge-frac", type=float, default=0.12)
    ap.add_argument("--min-luma", type=float, default=40.0)
    ap.add_argument("--no-download", action="store_true")
    args = ap.parse_args()

    cfg = Config(k=args.k, edge_frac=args.edge_frac, min_luma=args.min_luma)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(args.frames_dir) if args.frames_dir else out / "frames"

    stations = [s.strip() for s in args.stations.split(",") if s.strip()]
    print(f"→ 索引：{len(stations)} 个站位 {stations}")
    fine_rows: list[dict] = []
    daily_rows: list[dict] = []
    fine_from = parse_dt(args.fine_from) if args.fine_from else None
    fine_to = parse_dt(args.fine_to) if args.fine_to else None
    for st in stations:
        rows = fetch_index(args.base, st)
        rows = [r for r in rows if (r.get("angle_profile") or "level0") == "level0"]
        print(f"  {st}: {len(rows)} 帧")
        if fine_from and fine_to:
            fine_rows += pick_fine(rows, fine_from, fine_to)
        if args.daily_from and args.daily_to:
            daily_rows += pick_daily(rows, args.daily_from, args.daily_to, args.daily_hour)
    if not args.no_download:
        download_missing(args.base, fine_rows + daily_rows, frames_dir, args.workers)

    all_frames: list[dict] = []
    all_pairs: list[dict] = []
    set_results: dict[str, tuple[dict[str, list[tuple[str, float]]],
                                 list[tuple[str, float, int]]]] = {}
    if fine_rows:
        fr, fp, fc, fv = run_set("细粒度（相邻轮次）", fine_rows, frames_dir, cfg, args.max_gap_h)
        all_frames += fr
        all_pairs += [{"set": "fine", **p.__dict__} for p in fp]
        set_results["fine"] = (fc, median_curve(fc, fv))
    if daily_rows:
        dr, dp, dc, dv = run_set("日尺度（每日同一时刻）", daily_rows, frames_dir, cfg, 30.0, min_dt=12.0)
        all_frames += dr
        all_pairs += [{"set": "daily", **p.__dict__} for p in dp]
        set_results["daily"] = (dc, median_curve(dc, dv))

    write_csv(out / "frames.csv", all_frames,
              ["station", "ts", "status", "flags", "thr", "span", "wall", "luma",
               "n_valid", "top_median_px"])
    write_csv(out / "pairs.csv", all_pairs,
              ["set", "station", "ts_a", "ts_b", "dt_h", "status",
               "delta_px", "delta_p25", "delta_p75", "delta_mean",
               "delta_k_lo", "delta_k_hi", "delta_left", "delta_right",
               "valid_ratio", "profile_corr", "shift_dy", "shift_dx", "align_peak"])
    write_csv(out / "anchors.csv",
              [{"set": key, "station": station, "ts": ts, "cum_delta_px": round(v, 2)}
               for key, (curves, _) in set_results.items()
               for station, pts in curves.items() for ts, v in pts],
              ["set", "station", "ts", "cum_delta_px"])
    for key, (curves, med) in set_results.items():
        svg_curve(out / f"trend-{key}.svg", curves, med,
                  f"簇顶轮廓累计抬升 [{key}]（px；粗蓝=锚点中位数，细灰=单锚点）")

    lines = ["# 相邻帧簇顶轮廓抬升（探索）", "",
             f"生成：{datetime.now().isoformat(timespec='seconds')}",
             f"底部连通阈值 k={cfg.k}（敏感性对照 {cfg.k_lo}/{cfg.k_hi}），"
             f"降采样 {cfg.prop_factor}×，横向容差 {cfg.prop_dilate_r}（粗像素），"
             f"上沿对比门 {cfg.edge_frac}×帧对比度，"
             f"ROI x[{cfg.roi_x0},{cfg.roi_x1}] y[{cfg.roi_y0},{cfg.roi_y1}]",
             "口径：**原图像素**，不是 mm；量的是簇级轮廓，不是单株；"
             "正数 = 后一帧顶部更高（往上长）。", ""]
    for label, key in (("细粒度（相邻轮次）", "fine"), ("日尺度", "daily")):
        ps = [p for p in all_pairs if p["set"] == key]
        if not ps:
            continue
        good = [p for p in ps if p["status"] == OK]
        lines += [f"## {label}", "",
                  f"- 相邻对 {len(ps)}，通过 {len(good)}（{len(good) / len(ps):.0%}）"]
        if good:
            deltas = [p["delta_px"] for p in good]
            rates = [p["delta_px"] / p["dt_h"] for p in good]
            rate_med = stats.median(rates)
            lines += [f"- 间隔中位 {stats.median([p['dt_h'] for p in good]):.2f} h；"
                      f"单对 delta 中位 {stats.median(deltas):.2f} px；"
                      f"速率中位 {rate_med:.2f} px/h（≈ {rate_med * 24:.0f} px/24h）",
                      f"- delta 正值占比 {sum(d > 0 for d in deltas) / len(good):.0%}；"
                      f"p25/p75 = {np.percentile(deltas, 25):.1f} / {np.percentile(deltas, 75):.1f} px",
                      f"- 锚点间离散（delta 的 MAD）"
                      f"{stats.median([abs(d - stats.median(deltas)) for d in deltas]):.2f} px"]
            sens = [p["delta_k_hi"] - p["delta_k_lo"] for p in good
                    if math.isfinite(p["delta_k_lo"]) and math.isfinite(p["delta_k_hi"])]
            if sens:
                lines.append(f"- 阈值敏感性（k {cfg.k_hi}−{cfg.k_lo}）"
                             f"中位 {stats.median(sens):.2f} px")
            corrs = [p["profile_corr"] for p in good if math.isfinite(p["profile_corr"])]
            if corrs:
                lines.append(f"- 轮廓形状相关中位 {stats.median(corrs):.2f}（越接近 1 越像同一片菌簇）")
        rejects: dict[str, int] = {}
        for p in ps:
            if p["status"] != OK:
                rejects[p["status"]] = rejects.get(p["status"], 0) + 1
        if rejects:
            lines.append("- 被拒：" + "，".join(f"{k}={v}" for k, v in sorted(rejects.items())))
        lines.append("- **累计曲线是下界**：只累加通过门限的对，被拒时段留平台；"
                     "速率看上面的中位，不要拿累计当绝对长势。")
        curves, med = set_results[key]
        if med:
            lines += ["", "| 小时桶 | 累计抬升中位 (px) | 锚点数 |", "| --- | --- | --- |"]
            step = max(1, len(med) // 24)
            for ts, v, n in med[::step]:
                lines.append(f"| {ts} | {v:.1f} | {n} |")
        if curves:
            lines += ["", "| 锚点 | 通过对 | 窗口内累计 (px) |", "| --- | --- | --- |"]
            for station, pts in sorted(curves.items()):
                n_ok = sum(1 for p in good if p["station"] == station)
                lines.append(f"| {station} | {n_ok} | {pts[-1][1]:.1f} |")
        lines.append("")
    lines += ["## 图", "",
              "`trend-fine.svg` / `trend-daily.svg`（细灰=单锚点累计，粗蓝=锚点中位数）", "",
              "## 已知限制", "",
              "- 只有像素，没有 mm；不写 `verified`，不进区间表。",
              "- 相邻对之间可能有新菇冒出/被采，`delta` 是簇级轮廓变化，不是单株增长。",
              "- 加湿期/灯窗切换造成曝光变化时，对会被 `exposure_changed` / `contrast_changed` / "
              "`coverage_changed` 拒掉；灯没亮的轮次整帧 `too_dark`（本批 3h 对的通过率因此只有四分之一）。",
              "- 灯窗不是固定时刻：本批 12 点对 layer3/4 常整帧过暗，日尺度改用 15 点。"
              "上生产时应先拉索引 + 帧亮度，挑当天**可用**的那一轮，而不是写死钟点。",
              "- 跨天对比只看平移：实测墙带位移中位 ≈1 px（p95 ≈3 px），机位重复性够用；"
              "但本工具不修 Z 向缩放 —— 若发生 re-anchor/变焦，菌簇会按离光心距离成比例位移，"
              "平移对齐会把这部分算成\"长势\"。上生产前应加一道墙带上下子带位移差监控。",
              "- `halves_inconsistent` / `delta_outlier` 的对保留在 `pairs.csv` 里，不静默丢。"]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"OK 输出 -> {out}")
    for key, (_, med) in set_results.items():
        if med:
            print(f"  {key}: 末点 {med[-1][1]:.1f} px @ {med[-1][0]}（{med[-1][2]} 锚点）")
            print(f"  {key} spark: {sparkline([v for _, v, _ in med])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

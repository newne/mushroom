"""相邻帧长势算法诊断可视化：把"算法到底看到了什么"画出来。

不是指标工具，也**不下结论**。每个面板都在回答一个可证伪的问题：

- 分割：亮实体（红）与"底部连通"部分（绿）差在哪？背景亮斑/雾层有没有被漏进来？
- 包络：顶部边界贴的是菌帽、袋沿，还是雾层？亚像素细化有没有跳变？
- 成对：A/B 对齐后的两条轮廓是否同形？逐列 delta 的分布是单峰还是双峰？
- 测量：中位数/分位数/左右两半/两档阈值分别在哪？有效列覆盖多少？

用法（仓库根）：

    python measure/tools/viz_growth_diagnostics.py \
        --frames-dir .scratch/growth-trend/frames --out .scratch/growth-vis \
        --pair S102 2026-10-18T15:20:16 2026-10-19T15:20:16 \
        --frame S102 2026-10-19T15:20:16

输出：每对/每帧一张 PNG + 一个 `index.html`（可直接在浏览器打开）。
"""

from __future__ import annotations

import argparse
import html
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import explore_growth_adjacent as eg  # noqa: E402

# 画布
W, H = 1680, 1240
BG = (250, 250, 248)
INK = (35, 35, 35)
MUTED = (110, 110, 110)
GRID = (225, 225, 222)
RED = (220, 60, 50)
GREEN = (30, 160, 80)
BLUE = (30, 95, 208)
YELLOW = (245, 190, 20)
ORANGE = (235, 130, 30)


def _font(size: int = 14) -> ImageFont.ImageFont:
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/System/Library/Fonts/Supplemental/Arial.ttf"):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return ImageFont.load_default()


FONT = _font(15)
FONT_B = _font(19)
FONT_S = _font(12)


def _row(station: str, ts: str) -> dict:
    return {"station_id": station, "ts": ts}


def _crop(gray: np.ndarray, cfg: eg.Config) -> np.ndarray:
    h, w = gray.shape
    return gray[int(cfg.roi_y0 * h): int(cfg.roi_y1 * h),
                int(cfg.roi_x0 * w): int(cfg.roi_x1 * w)]


def _rgb(gray: np.ndarray) -> np.ndarray:
    g = np.clip((gray - np.percentile(gray, 1))
                / max(np.percentile(gray, 99.5) - np.percentile(gray, 1), 1) * 255, 0, 255)
    return np.stack([g] * 3, -1).astype(np.uint8)


def _tint(rgb: np.ndarray, mask: np.ndarray, color: tuple, alpha: float,
          factor: int) -> None:
    m = np.repeat(np.repeat(mask, factor, axis=0), factor, axis=1)
    m = m[: rgb.shape[0], : rgb.shape[1]]
    if m.shape[0] < rgb.shape[0] or m.shape[1] < rgb.shape[1]:
        pad = np.zeros(rgb.shape[:2], bool)
        pad[: m.shape[0], : m.shape[1]] = m
        m = pad
    rgb[m] = (rgb[m].astype(np.float32) * (1 - alpha)
              + np.array(color, np.float32) * alpha).astype(np.uint8)


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """连续 True 段 [start, stop)。"""
    idx = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(np.int8)))
    return [(int(a), int(b)) for a, b in zip(idx[::2], idx[1::2])]


def _fit(img: Image.Image, bw: int, bh: int) -> tuple[Image.Image, float]:
    s = min(bw / img.width, bh / img.height)
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))),
                      Image.LANCZOS), s


def _draw_env(dr: ImageDraw.ImageDraw, top: np.ndarray, s: float,
              ox: float, oy: float, color: tuple, width: int = 2,
              x_off: int = 0, y_off: int = 0) -> None:
    ok = np.isfinite(top)
    for a, b in _runs(ok):
        pts = [(ox + (x - x_off) * s, oy + (top[x] - y_off) * s)
               for x in range(a, b)]
        if len(pts) > 1:
            dr.line(pts, fill=color, width=width)


def _frame_panel(canvas: Image.Image, dr: ImageDraw.ImageDraw, gray: np.ndarray,
                 seg: eg.Segmentation, box: tuple[int, int, int, int],
                 title: str, cfg: eg.Config) -> None:
    x0, y0, x1, y1 = box
    dr.text((x0, y0 - 26), title, fill=INK, font=FONT_B)
    roi = _crop(gray, cfg)
    rgb = _rgb(roi)
    _tint(rgb, seg.mask & ~seg.keep, RED, 0.28, cfg.prop_factor)
    _tint(rgb, seg.keep, GREEN, 0.28, cfg.prop_factor)
    img, s = _fit(Image.fromarray(rgb), x1 - x0, y1 - y0 - 34)
    ox = x0 + (x1 - x0 - img.width) // 2
    oy = y0 + 30 + (y1 - y0 - 34 - img.height) // 2
    canvas.paste(img, (ox, oy))
    _draw_env(dr, seg.top, s, ox, oy, YELLOW, 2)
    dr.text((x0, y0 + 2),
            f"thr={seg.thr:.0f}  span={seg.span:.0f}  valid={seg.n_valid}",
            fill=MUTED, font=FONT_S)
    dr.text((x0, y1 - 16), "red = bright but not connected   green = bottom-connected   yellow = top envelope",
            fill=MUTED, font=FONT_S)


def _plot_delta(dr: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                d: np.ndarray, valid: np.ndarray, stats: dict) -> None:
    x0, y0, x1, y1 = box
    dr.text((x0, y0 - 26), "delta(x) = top_A − top_B_aligned   [px per column]", fill=INK, font=FONT_B)
    px0, px1 = x0 + 52, x1 - 16
    py0, py1 = y0 + 16, y1 - 34
    v = d[valid]
    if v.size == 0:
        dr.text((px0, py0 + 20), "no valid columns", fill=RED, font=FONT)
        return
    lo = min(float(np.percentile(v, 1)) - 10, -10.0)
    hi = max(float(np.percentile(v, 99)) + 10, 60.0)
    hi = min(hi, 420.0)

    def X(x: float) -> float:
        return px0 + (x / max(d.shape[0] - 1, 1)) * (px1 - px0)

    def Y(y: float) -> float:
        return py1 - (y - lo) / (hi - lo) * (py1 - py0)

    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        yy = lo + (hi - lo) * frac
        dr.line([(px0, Y(yy)), (px1, Y(yy))], fill=GRID)
        dr.text((x0, Y(yy) - 7), f"{yy:.0f}", fill=MUTED, font=FONT_S)
    for q, color in ((stats.get("p25"), (150, 150, 150)),
                     (stats.get("p50"), BLUE),
                     (stats.get("p75"), (150, 150, 150))):
        if q is not None and math.isfinite(q):
            dr.line([(px0, Y(q)), (px1, Y(q))], fill=color, width=2)
    dr.line([(px0, Y(0)), (px1, Y(0))], fill=(0, 0, 0), width=1)
    for a, b in _runs(valid):
        pts = [(X(x), Y(d[x])) for x in range(a, b)]
        if len(pts) > 1:
            dr.line(pts, fill=(70, 70, 70), width=1)
    step = max(1, d.shape[0] // 700)      # 有效列覆盖条
    for x in range(0, d.shape[0], step):
        if valid[x]:
            xx = X(x)
            dr.line([(xx, py1 + 2), (xx, py1 + 8)], fill=GREEN, width=1)
    dr.text((px0, y1 - 14), "column →   (green ticks = valid in both frames)",
            fill=MUTED, font=FONT_S)


def _plot_hist(dr: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
               d: np.ndarray, valid: np.ndarray, stats: dict) -> None:
    x0, y0, x1, y1 = box
    dr.text((x0, y0 - 26), "delta histogram (5 px bins)", fill=INK, font=FONT_B)
    v = d[valid]
    if v.size == 0:
        dr.text((x0, y0 + 20), "no valid columns", fill=RED, font=FONT)
        return
    lo = min(float(np.percentile(v, 1)) - 10, -20.0)
    hi = max(float(np.percentile(v, 99)) + 10, 80.0)
    hi = min(hi, 420.0)
    bins = np.arange(lo, hi + 5, 5.0)
    counts, edges = np.histogram(v, bins=bins)
    c_max = max(int(counts.max()), 1)
    px0, px1, py0, py1 = x0 + 40, x1 - 12, y0 + 10, y1 - 26
    bw = (px1 - px0) / max(len(counts), 1)
    for i, c in enumerate(counts):
        if c <= 0:
            continue
        bx0 = px0 + i * bw
        by = py1 - c / c_max * (py1 - py0)
        dr.rectangle([bx0, by, bx0 + max(bw - 1, 1), py1], fill=(120, 160, 210))
    for q, color in ((stats.get("p25"), (150, 150, 150)),
                     (stats.get("p50"), BLUE),
                     (stats.get("p75"), (150, 150, 150))):
        if q is not None and math.isfinite(q):
            xx = px0 + (q - lo) / max(hi - lo, 1) * (px1 - px0)
            dr.line([(xx, py0), (xx, py1)], fill=color, width=2)
    dr.text((px0, y1 - 16), f"{lo:.0f} … {hi:.0f} px", fill=MUTED, font=FONT_S)


def _stats_block(dr: ImageDraw.ImageDraw, box: tuple[int, int, int, int],
                 pa: eg.Profile, pb: eg.Profile, stats: dict,
                 dy: float, dx: float, peak: float) -> None:
    x0, y0, x1, y1 = box
    dr.text((x0, y0 - 26), "measurement / gates", fill=INK, font=FONT_B)
    rows = [
        ("status A / B", f"{pa.status} / {pb.status}"),
        ("shift dy, dx", f"{dy:+.2f}, {dx:+.2f} px"),
        ("phase peak", f"{peak:.0f}"),
        ("valid columns", f"{stats.get('n', 0)}  ({stats.get('valid_ratio', 0):.2f})"),
        ("p25 / p50 / p75", f"{stats.get('p25', math.nan):.1f} / "
                            f"{stats.get('p50', math.nan):.1f} / {stats.get('p75', math.nan):.1f}"),
        ("mean", f"{stats.get('mean', math.nan):.1f}"),
        ("left / right half", f"{stats.get('left', math.nan):.1f} / "
                              f"{stats.get('right', math.nan):.1f}"),
        ("profile corr", f"{stats.get('corr', math.nan):.3f}"),
        ("delta k_lo / k_hi", f"{stats.get('k_lo', math.nan):.1f} / "
                              f"{stats.get('k_hi', math.nan):.1f}"),
        ("A/B luma", f"{pa.luma:.0f} / {pb.luma:.0f}"),
    ]
    y = y0 + 6
    for k, val in rows:
        dr.text((x0, y), k, fill=MUTED, font=FONT_S)
        dr.text((x0 + 180, y), str(val), fill=INK, font=FONT_S)
        y += 21


def _zoom_panel(canvas: Image.Image, dr: ImageDraw.ImageDraw, gray: np.ndarray,
                seg: eg.Segmentation, box: tuple[int, int, int, int],
                cfg: eg.Config) -> None:
    x0, y0, x1, y1 = box
    dr.text((x0, y0 - 26), "full-resolution boundary (1:1)", fill=INK, font=FONT_B)
    top = seg.top
    ok = np.isfinite(top)
    if not ok.any():
        dr.text((x0, y0 + 20), "no valid envelope", fill=RED, font=FONT)
        return
    xs = np.flatnonzero(ok)
    xc = int(xs[np.argmin(top[ok])])
    yc = int(top[xc])
    roi = _crop(gray, cfg)
    h, w = roi.shape
    cw, ch = min(360, w), min(200, h)
    cx0 = int(np.clip(xc - cw // 2, 0, max(w - cw, 0)))
    cy0 = int(np.clip(yc - ch // 2, 0, max(h - ch, 0)))
    crop = roi[cy0:cy0 + ch, cx0:cx0 + cw]
    img = Image.fromarray(_rgb(crop))
    canvas.paste(img, (x0, y0 + 4))
    pts = []
    for x in range(xs[0], xs[-1] + 1):
        if np.isfinite(top[x]):
            px = x - cx0
            py = top[x] - cy0
            if 0 <= px < img.width and 0 <= py < img.height:
                pts.append((x0 + px, y0 + 4 + py))
    if len(pts) > 1:
        dr.line(pts, fill=YELLOW, width=2)
    xx = xc - cx0
    yy = top[xc] - cy0
    if 0 <= xx < img.width and 0 <= yy < img.height:
        dr.line([(x0 + xx, y0 + 4 + yy - 8), (x0 + xx, y0 + 4 + yy + 8)], fill=RED, width=2)
        dr.text((x0 + 6, y0 + 4 + img.height + 6),
                f"x={xc}  top={top[xc]:.2f}  edge_ok={bool(seg.edge_ok[xc])}",
                fill=MUTED, font=FONT_S)


def render_pair(a_path: Path, b_path: Path, out: Path, cfg: eg.Config,
                station: str, ts_a: str, ts_b: str) -> dict:
    ga, gb = eg.load_gray(a_path), eg.load_gray(b_path)
    pa = eg.profile_frame(a_path, _row(station, ts_a), cfg)
    pb = eg.profile_frame(b_path, _row(station, ts_b), cfg)
    dy, dx, peak = eg.phase_shift(ga, gb, cfg)
    if not (pa.ok and pb.ok):
        raise RuntimeError(f"帧不可用：A={pa.status} B={pb.status}")
    # profile 的 top 是**整帧** y；绘图裁的是 ROI，先统一到 ROI 坐标。
    # 两条轮廓同减一个常数，delta 不变。
    y0 = int(cfg.roi_y0 * ga.shape[0])
    ta, tb = pa.top - y0, pb.top - y0
    d, valid = eg.delta_profile(ta, tb, dy, dx)
    d_klo, _ = eg.delta_profile(pa.top_lo - y0, pb.top_lo - y0, dy, dx)
    d_khi, _ = eg.delta_profile(pa.top_hi - y0, pb.top_hi - y0, dy, dx)
    stats = {}
    stats.update(eg.measure_delta(ta, tb, dy, dx)[1])
    stats["k_lo"] = float(np.nanmedian(d_klo)) if np.isfinite(d_klo).any() else math.nan
    stats["k_hi"] = float(np.nanmedian(d_khi)) if np.isfinite(d_khi).any() else math.nan

    sega = eg.segment_cluster(_crop(ga, cfg), cfg, cfg.k)
    segb = eg.segment_cluster(_crop(gb, cfg), cfg, cfg.k)

    canvas = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(canvas)
    dr.text((40, 24), f"{station}  {ts_a} → {ts_b}", fill=INK, font=FONT_B)
    vr = float(stats.get("valid_ratio", 0.0))
    corr = float(stats.get("corr", math.nan))
    notes = []
    if vr < cfg.min_valid_ratio:
        notes.append(f"coverage {vr:.2f} < gate {cfg.min_valid_ratio} → few_valid_columns")
    if math.isfinite(corr) and corr < 0.70:
        notes.append(f"corr {corr:.2f} < 0.70 → 形状不一致")
    if float(stats.get("p50", 0.0)) < 0:
        notes.append("delta < 0 → 先按事件查（采收/换面），不要读成负增长")
    dr.text((40, 50), f"delta median = {stats.get('p50', math.nan):.1f} px   "
                      f"coverage = {vr:.2f}   corr = {corr:.2f}   "
                      f"（探索量，不是已验证的绝对长势）", fill=MUTED, font=FONT)
    if notes:
        dr.text((40, 72), "  |  ".join(notes), fill=RED, font=FONT_S)
    _frame_panel(canvas, dr, ga, sega, (40, 130, 820, 540),
                 "A  earlier frame — segmentation", cfg)
    _frame_panel(canvas, dr, gb, segb, (860, 130, 1640, 540),
                 "B  later frame — segmentation", cfg)

    # 两条包络叠在 A 上（裁到菌簇区域，别让整屏 ROI 把细节压没）
    x = np.arange(min(ta.shape[0], tb.shape[0]))
    finite = np.isfinite(tb)
    xi = np.clip(np.round(x - dx).astype(np.int32), 0, len(tb) - 1)
    tb_shift = np.interp(x, (x - dx)[finite], tb[finite], left=np.nan, right=np.nan)
    tb_shift[~finite[xi]] = np.nan
    roi = _crop(ga, cfg)
    both = np.concatenate([ta, tb_shift + dy])
    ok = np.isfinite(both)
    if ok.any():
        ylo = max(0, int(np.nanmin(both[ok])) - 60)
        yhi = min(roi.shape[0], int(np.nanmax(both[ok])) + 130)
    else:
        ylo, yhi = 0, roi.shape[0]
    cols_ok = np.flatnonzero(np.isfinite(ta) | np.isfinite(tb_shift))
    if cols_ok.size:
        xlo = max(0, int(cols_ok[0]) - 30)
        xhi = min(roi.shape[1], int(cols_ok[-1]) + 30)
    else:
        xlo, xhi = 0, roi.shape[1]
    sub = roi[ylo:yhi, xlo:xhi]
    img, s = _fit(Image.fromarray(_rgb(sub)), 800, 330)
    ox, oy = 40, 610
    canvas.paste(img, (ox, oy))
    _draw_env(dr, ta[xlo:xhi], s, ox, oy, RED, 2, y_off=ylo)
    _draw_env(dr, (tb_shift + dy)[xlo:xhi], s, ox, oy, GREEN, 2, y_off=ylo)
    dr.text((40, 570), "A with both envelopes: red = A, green = B aligned", fill=INK, font=FONT_B)
    dr.text((40, 948), "B gaps stay gaps (no interpolation); crop = cluster bounding box",
            fill=MUTED, font=FONT_S)

    _plot_delta(dr, (880, 610, 1620, 860), d, valid, stats)
    _plot_hist(dr, (880, 890, 1620, 1090), d, valid, stats)
    _zoom_panel(canvas, dr, ga, sega, (40, 990, 420, 1230), cfg)
    _stats_block(dr, (480, 990, 860, 1230), pa, pb, stats, dy, dx, peak)

    canvas.save(out)
    return {"station": station, "ts_a": ts_a, "ts_b": ts_b,
            "stats": {k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                      for k, v in stats.items()},
            "dy": dy, "dx": dx, "peak": peak,
            "status": f"{pa.status}/{pb.status}"}


def render_frame(path: Path, out: Path, cfg: eg.Config, station: str, ts: str) -> dict:
    g = eg.load_gray(path)
    p = eg.profile_frame(path, _row(station, ts), cfg)
    if not p.ok:
        raise RuntimeError(f"帧不可用：{p.status}")
    seg = eg.segment_cluster(_crop(g, cfg), cfg, cfg.k)
    canvas = Image.new("RGB", (W, 760), BG)
    dr = ImageDraw.Draw(canvas)
    dr.text((40, 24), f"{station}  {ts}   frame segmentation", fill=INK, font=FONT_B)
    _frame_panel(canvas, dr, g, seg, (40, 90, 1080, 700),
                 "ROI — red: bright only; green: bottom-connected; yellow: envelope", cfg)
    _zoom_panel(canvas, dr, g, seg, (1120, 90, 1640, 420), cfg)
    y = 470
    for k, val in (("thr", f"{seg.thr:.1f}"), ("wall", f"{seg.wall:.1f}"),
                   ("span", f"{seg.span:.1f}"), ("valid columns", f"{seg.n_valid}"),
                   ("edge-gated columns", f"{int(seg.edge_ok.sum())}")):
        dr.text((1120, y), k, fill=MUTED, font=FONT_S)
        dr.text((1300, y), val, fill=INK, font=FONT_S)
        y += 24
    canvas.save(out)
    return {"station": station, "ts": ts, "thr": seg.thr, "span": seg.span,
            "valid": seg.n_valid, "edge_ok": int(seg.edge_ok.sum())}


def write_index(out_dir: Path, items: list[dict]) -> None:
    cards = []
    for it in items:
        if it["kind"] == "pair":
            s = it["stats"]
            rows = "".join(
                f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
                for k, v in (
                    ("delta p25 / p50 / p75", f"{s.get('p25', 0):.1f} / "
                                             f"{s.get('p50', 0):.1f} / {s.get('p75', 0):.1f}"),
                    ("valid columns", f"{s.get('n', 0)} ({s.get('valid_ratio', 0):.2f})"),
                    ("left / right", f"{s.get('left', 0):.1f} / {s.get('right', 0):.1f}"),
                    ("profile corr", f"{s.get('corr', 0):.3f}"),
                    ("k_lo / k_hi", f"{s.get('k_lo', 0):.1f} / {s.get('k_hi', 0):.1f}"),
                    ("shift dy,dx", f"{it['dy']:+.2f}, {it['dx']:+.2f}"),
                ))
            title = f"{it['station']} &nbsp; {it['ts_a']} → {it['ts_b']}"
        else:
            rows = "".join(
                f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
                for k, v in (("thr", f"{it['thr']:.1f}"), ("span", f"{it['span']:.1f}"),
                             ("valid columns", it["valid"]),
                             ("edge-gated", it["edge_ok"])))
            title = f"{it['station']} &nbsp; {it['ts']} &nbsp; (single frame)"
        cards.append(
            f"<div class='card'><h2>{title}</h2>"
            f"<img src='{html.escape(it['img'])}' alt='{html.escape(it['img'])}'>"
            f"<table>{rows}</table></div>")
    page = f"""<!doctype html><meta charset='utf-8'>
<title>growth diagnostics</title>
<style>
body{{font-family:system-ui,-apple-system,'Microsoft YaHei',sans-serif;margin:24px;background:#fafaf8;color:#222}}
h1{{font-size:20px}} .note{{color:#666;max-width:1100px;line-height:1.6}}
.card{{background:#fff;border:1px solid #e5e5e0;border-radius:8px;padding:14px;margin:18px 0}}
.card h2{{font-size:15px;margin:2px 0 10px;font-weight:600}}
.card img{{width:100%;height:auto;display:block;border:1px solid #eee}}
table{{border-collapse:collapse;margin-top:10px;font-size:13px}}
td{{border-top:1px solid #eee;padding:3px 14px 3px 0;color:#444}}
</style>
<h1>相邻帧长势算法：分割与测量诊断</h1>
<p class='note'>这些图是<b>诊断</b>，不是结论：每张图把分割掩码、顶部包络、逐列
delta 与门限数字放在一起，用来回答“算法看到的边界是不是我以为的那条”。
图里的 delta 是探索量（原图像素），没有 mm 标定，也没有经过锁定测试集验收。</p>
{''.join(cards)}
"""
    (out_dir / "index.html").write_text(page, encoding="utf-8")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="相邻帧长势算法诊断可视化（不产指标）")
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pair", nargs=3, action="append", metavar=("STATION", "TS_A", "TS_B"))
    ap.add_argument("--frame", nargs=2, action="append", metavar=("STATION", "TS"))
    args = ap.parse_args()

    cfg = eg.Config()
    frames_dir = Path(args.frames_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict] = []

    for station, ts_a, ts_b in args.pair or []:
        a = eg.frame_path(frames_dir, _row(station, ts_a))
        b = eg.frame_path(frames_dir, _row(station, ts_b))
        name = f"pair_{station}_{ts_a[:10]}_{ts_b[:10]}_{ts_a[11:13]}{ts_b[11:13]}.png"
        try:
            info = render_pair(a, b, out_dir / name, cfg, station, ts_a, ts_b)
        except Exception as exc:  # noqa: BLE001 - 诊断工具：单对失败不影响其余
            print(f"pair {station} {ts_a} → {ts_b}: SKIP {exc}")
            continue
        info.update(kind="pair", img=name)
        items.append(info)
        print(f"pair {station} {ts_a} → {ts_b}: p50={info['stats'].get('p50'):.1f} px, "
              f"n={info['stats'].get('n')}, saved {name}")

    for station, ts in args.frame or []:
        p = eg.frame_path(frames_dir, _row(station, ts))
        name = f"frame_{station}_{ts[:10]}_{ts[11:13]}{ts[11:16].replace(':', '')}.png"
        try:
            info = render_frame(p, out_dir / name, cfg, station, ts)
        except Exception as exc:  # noqa: BLE001
            print(f"frame {station} {ts}: SKIP {exc}")
            continue
        info.update(kind="frame", img=name)
        items.append(info)
        print(f"frame {station} {ts}: valid={info['valid']}, saved {name}")

    write_index(out_dir, items)
    print(f"OK -> {out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

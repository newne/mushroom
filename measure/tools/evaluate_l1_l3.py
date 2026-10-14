"""L1–L3 离线评估（Chief 验收项 3，2026-10-14）：218 帧样本可复跑。

跑什么（每帧，全部是**生产路径的默认配置**）：

- **L1** `find_bags(BagConfig())` 全帧：袋数、排数、**逐排袋宽与排内 CV**、可信排数；
- **L2** 袋肩 ROI：取画面下 `--roi-frac`（默认 0.45）作菇体候选带；
- **L3** `find_fruitbodies(ROI)`：实例数（有/无检测）、按排实例数；
- **自标对照**：同一排菌包"水平 240mm 口径"与"竖直 140mm 口径"推出的 mm/px 差异——
  两者在同深度下应一致，不一致就是透视/朝向问题（票 15 §12）。
- **交付量**：`estimate_cluster()` 的 `mean_cluster_w_mm`（240mm 水平口径，缺 θ 也能出）
  与 `mean_cluster_h_mm`（140mm 竖直 + ÷cosθ，未注入 θ ⇒ NULL + `no_theta`）。

用法（仓库根）：

    python measure/tools/evaluate_l1_l3.py --out .scratch/l1-l3-eval
    python measure/tools/evaluate_l1_l3.py --limit 5 --out .scratch/l1-l3-eval-smoke

产物：`<out>/per_frame.csv`（逐帧）、`<out>/summary.md`（汇总，给票面引用）。
**只读样本，不写库、不连网。** 脚本本身不判"精度合格"——它就是给数。
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "src"))  # measure/src

from measure import contract  # noqa: E402
from measure.bag_ruler import (  # noqa: E402
    BagConfig,
    find_bags,
    nearest_bag_in_same_row,
    row_is_reliable,
    row_width_cv,
)
from measure.estimate import (  # noqa: E402
    BAG_DIAMETER_MM,
    BAG_LENGTH_MM,
    estimate_cluster,
    filter_above_bag_top,
)
from measure.fruitbody import FruitConfig, find_fruitbodies  # noqa: E402

DEFAULT_SAMPLES = Path(r"D:\code\mushroom\.scratch\frame-quality-samples")


def _read_gray(path: Path) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB")).astype(np.float32).mean(axis=2)


def _rows_of(bags) -> dict[int, list]:
    rows: dict[int, list] = {}
    for b in bags:
        rows.setdefault(b.row_id, []).append(b)
    return rows


def evaluate_frame(gray: np.ndarray, *, roi_frac: float,
                   bag_cfg: BagConfig, fruit_cfg: FruitConfig) -> dict:
    h, w = gray.shape
    t0 = time.monotonic()
    bags, bdiag = find_bags(gray, bag_cfg)
    t_l1 = time.monotonic() - t0

    rows = _rows_of(bags)
    widths = [b.width_px for b in bags]
    reliable_rows = [r for r in rows if row_is_reliable(bags, r, bag_cfg)]
    # 排内 CV：只对 ≥2 袋的排有意义
    cvs = [row_width_cv(bags, r) for r in rows]
    cvs = [c for c in cvs if c is not None]

    # 自标对照：水平(240mm) vs 竖直(140mm) 口径的 mm/px
    cross: list[float] = []
    for b in bags:
        if b.width_px > 1 and b.height_px > 1:
            mx = BAG_LENGTH_MM / b.width_px
            my = BAG_DIAMETER_MM / b.height_px
            cross.append(abs(mx - my) / max(mx, my))

    y0 = int(h * (1.0 - roi_frac))
    roi = gray[y0:]
    t1 = time.monotonic()
    insts, fdiag = find_fruitbodies(roi, None, fruit_cfg, origin=(y0, 0))
    t_l3 = time.monotonic() - t1

    # **袋肩门**（2026-10-14 实测：72–83% 的 L3 实例落在袋体内部 = 袋面纹理）
    kept, gdiag = filter_above_bag_top(insts, bags)

    # **袋级菌簇**（交付口径）：每个实例归到它自己那袋，按袋算跨度、
    # 用该袋自己的宽度定局部 mm/px。`estimate_cluster()` 给的是**整帧跨度**
    # （一帧 18 袋 ⇒ 2000+ mm，不是"菌簇直径"），这里两套都出数便于对照。
    def bag_metrics(inst_list) -> tuple[list[float], list[float], int]:
        per_bag: dict[int, list] = {}
        for i in inst_list:
            b = nearest_bag_in_same_row(bags, i.cy, bag_cfg, x=i.cx)
            if b is None:
                continue
            per_bag.setdefault(id(b), []).append(i)
        bag_index = {id(b): k for k, b in enumerate(bags)}
        w_mm: list[float] = []
        h_px_list: list[float] = []
        for bid, group in per_bag.items():
            bag = bags[bag_index[bid]]
            ref_px = float(bag.characteristic_width_px() or bag.width_px)
            if ref_px <= 1:
                continue
            span_w = max(i.x1 for i in group) - min(i.x0 for i in group)
            span_h = max(i.y1 for i in group) - min(i.y0 for i in group)
            w_mm.append(span_w * (BAG_LENGTH_MM / ref_px))
            h_px_list.append(span_h)
        return w_mm, h_px_list, len(per_bag)

    bag_w_mm, bag_h_px, n_bags_kept = bag_metrics(kept)
    bag_w_mm_ungated, _, n_bags_ungated = bag_metrics(insts)

    est = estimate_cluster(kept or insts, bags,
                           depression_deg=None,
                           row_reliable=lambda i: any(
                               row_is_reliable(bags, b.row_id, bag_cfg)
                               for b in bags if abs(b.centroid_y - i.cy) <= bag_cfg.row_band_px))
    inst_rows = sorted({_nearest_row(bags, i.cy, bag_cfg) for i in kept})
    n_reliable_rows_with_inst = sum(
        1 for i in kept
        if any(row_is_reliable(bags, b.row_id, bag_cfg)
               for b in bags if abs(b.centroid_y - i.cy) <= bag_cfg.row_band_px))

    return {
        "n_bags": len(bags),
        "n_rows": len(rows),
        "n_reliable_rows": len(reliable_rows),
        "bag_w_med": float(np.median(widths)) if widths else None,
        "bag_w_cv_med": float(np.median(cvs)) if cvs else None,
        "bag_w_cv_max": float(max(cvs)) if cvs else None,
        "cross_scale_disagree_med": float(np.median(cross)) if cross else None,
        "l1_sec": round(t_l1, 3),
        "n_instances": len(insts),
        "n_inst_kept": len(kept),
        "kept_frac": round(gdiag["kept_frac"], 3),
        "n_inst_rows": len(inst_rows),
        "n_inst_on_reliable": n_reliable_rows_with_inst,
        "l3_sec": round(t_l3, 3),
        "n_bags_with_inst": n_bags_kept,
        "n_bags_with_inst_ungated": n_bags_ungated,
        "bag_cluster_w_mm_med": float(np.median(bag_w_mm)) if bag_w_mm else None,
        "bag_cluster_w_mm_max": float(max(bag_w_mm)) if bag_w_mm else None,
        "bag_cluster_w_mm_med_ungated": (float(np.median(bag_w_mm_ungated))
                                        if bag_w_mm_ungated else None),
        "bag_cluster_h_px_med": float(np.median(bag_h_px)) if bag_h_px else None,
        "frame_cluster_w_mm": est.cluster_w_mm,
        "frame_cluster_h_mm": est.cluster_h_mm,
        "mm_per_px_x": est.mm_per_px_x,
        "mm_per_px_y": est.mm_per_px_y,
        "quality": est.quality,
        "estimated_fields": "|".join(est.estimated_fields),
        "tophat_threshold": fdiag.get("tophat_threshold"),
        "n_components": fdiag.get("n_components"),
    }


def _nearest_row(bags, cy: float, cfg: BagConfig) -> int:
    same = [b for b in bags if abs(b.centroid_y - cy) <= cfg.row_band_px]
    if not same:
        return -1
    return min(same, key=lambda b: abs(b.centroid_y - cy)).row_id


def _summary(rows: list[dict], frames: int, roi_frac: float) -> str:
    def col(name):
        return [r[name] for r in rows if r.get(name) is not None]

    def stat(name, fmt="{:.3f}"):
        v = col(name)
        if not v:
            return "—"
        return (fmt.format(float(np.median(v))) +
                f"（p10 {fmt.format(float(np.percentile(v, 10)))}"
                f" / p90 {fmt.format(float(np.percentile(v, 90)))}，n={len(v)}）")

    det_frames = sum(1 for r in rows if r["n_instances"] > 0)
    scale_frames = sum(1 for r in rows if r["frame_cluster_w_mm"] is not None)
    h_frames = sum(1 for r in rows if r["frame_cluster_h_mm"] is not None)
    no_scale = sum(1 for r in rows if "no_scale" in (r["quality"] or ""))
    no_theta = sum(1 for r in rows if "no_theta" in (r["quality"] or ""))
    unreliable = sum(1 for r in rows if "unreliable" in (r["quality"] or ""))
    no_det = sum(1 for r in rows if "no_detection" in (r["quality"] or ""))
    total_l1 = sum(r["l1_sec"] for r in rows)
    total_l3 = sum(r["l3_sec"] for r in rows)
    lines = [
        f"# L1–L3 离线评估（{frames} 帧，ROI = 下 {roi_frac:.0%}）",
        "",
        f"- **L1**：袋数中位 {stat('n_bags', '{:.1f}')}；排数中位 {stat('n_rows', '{:.1f}')}；"
        f"可信排中位 {stat('n_reliable_rows', '{:.1f}')}",
        f"- **L1 袋宽**：中位 {stat('bag_w_med', '{:.0f}')} px；"
        f"排内 CV 中位 {stat('bag_w_cv_med')}，最大排 CV 中位 {stat('bag_w_cv_max')}",
        f"- **自标对照**（同袋 240mm 横 vs 140mm 竖的 mm/px 相对差）：{stat('cross_scale_disagree_med', '{:.1%}')}",
        f"- **L3 有/无检测**：**{det_frames}/{frames}** 帧有实例"
        f"（{det_frames / frames:.1%}）；实例数中位 {stat('n_instances', '{:.1f}')}",
        f"- **袋肩门**（去掉「袋体内部」的实例，2026-10-14 实测那是袋面纹理）："
        f"保留比例中位 {stat('kept_frac', '{:.1%}')}；保留后实例数中位 {stat('n_inst_kept', '{:.1f}')}",
        f"- **袋级菌簇直径（交付口径，开门后）**：中位 {stat('bag_cluster_w_mm_med', '{:.1f}')} mm"
        f"（最大袋中位 {stat('bag_cluster_w_mm_max', '{:.1f}')} mm）；"
        f"**门关对照** 中位 {stat('bag_cluster_w_mm_med_ungated', '{:.1f}')} mm",
        f"- **帧级跨度对照**（`estimate_cluster()` 现实现 = 整帧所有实例跨度，**不是菌簇直径**）："
        f"{stat('frame_cluster_w_mm', '{:.1f}')} mm",
        f"- **交付量**：`mean_cluster_w_mm` 出值 **{scale_frames}/{frames}** 帧；"
        f"`mean_cluster_h_mm` 出值 **{h_frames}/{frames}** 帧（未注入 θ ⇒ 预期 0）",
        f"- **失效码**（四条分开计数）：`no_theta` {no_theta} / `no_scale` {no_scale} / "
        f"`no_detection` {no_det} / `unreliable` {unreliable} 帧——"
        f"θ 缺只记 `no_theta`（有值但缺修正项，**不算整行不可用**）；"
        f"`no_scale` 只留给「完全没有标尺」",
        f"- **耗时**：L1 合计 {total_l1:.0f} s（均 {total_l1 / frames:.2f} s/帧）；"
        f"L3 ROI 合计 {total_l3:.0f} s（均 {total_l3 / frames:.2f} s/帧）；"
        f"**合计均 {(total_l1 + total_l3) / frames:.2f} s/帧**",
        "",
        "> 结论允许是「远排不可用」；本表只给数，不做精度判定（精度要人工标注 20 帧）。",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="L1–L3 离线评估（只读样本）")
    ap.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 帧（冒烟用）")
    ap.add_argument("--roi-frac", type=float, default=0.45)
    ap.add_argument("--downscale", type=int, default=2)
    args = ap.parse_args()

    samples = Path(args.samples)
    labels = samples / "labels.csv"
    if not labels.exists():
        print(f"找不到 {labels}", file=sys.stderr)
        return 2
    with labels.open(encoding="utf-8") as fh:
        entries = [r for r in csv.DictReader(fh) if r.get("label_auto") == "ok"
                   and r.get("label_final", "") != "reject"]
    if args.limit:
        entries = entries[: args.limit]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    bag_cfg = BagConfig(downscale=args.downscale)
    fruit_cfg = FruitConfig(downscale=args.downscale)

    rows: list[dict] = []
    t_all = time.monotonic()
    for i, e in enumerate(entries, 1):
        path = samples / e["file"]
        if not path.exists():
            print(f"[{i}/{len(entries)}] 缺文件 {path}，跳过")
            continue
        try:
            gray = _read_gray(path)
            rec = evaluate_frame(gray, roi_frac=args.roi_frac,
                                 bag_cfg=bag_cfg, fruit_cfg=fruit_cfg)
        except Exception as ex:  # noqa: BLE001 - 单帧失败不该毁掉整轮评估
            print(f"[{i}/{len(entries)}] {e['file']} 失败：{type(ex).__name__}: {ex}")
            continue
        rec = {"file": e["file"], "station_id": e.get("station_id", ""),
               "ts": e.get("ts", ""), "label": e.get("label_auto", ""), **rec}
        rows.append(rec)
        print(f"[{i}/{len(entries)}] {e['file']} 袋 {rec['n_bags']} / 实例 {rec['n_instances']} "
              f"/ 袋级簇宽={rec['bag_cluster_w_mm_med']} / 帧级={rec['frame_cluster_w_mm']} "
              f"L1 {rec['l1_sec']}s L3 {rec['l3_sec']}s")

    if rows:
        cols = list(rows[0].keys())
        with (out / "per_frame.csv").open("w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=cols)
            wr.writeheader()
            wr.writerows(rows)
        (out / "summary.md").write_text(_summary(rows, len(rows), args.roi_frac),
                                        encoding="utf-8")
    print(f"\n完成 {len(rows)} 帧，总耗时 {time.monotonic() - t_all:.0f} s → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

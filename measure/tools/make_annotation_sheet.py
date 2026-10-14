"""生成 20 帧人工标注工单（预标注 + 人只复核，票 21）。

## 为什么是"预标注 + 人只复核"

一期只有 20 帧预算，从零点框要人马几千个框；而 L1/L3 已经能给出**候选框**。
所以流程是：**团队用现役分割结果预标注 → 人只做确认/删改/补漏**，并逐框记
`review_status`（confirmed/edited/rejected/human_added）——这样**预标注的 precision
与漏检的 recall 都能算**，不是只得到一份"人工真值"。

## 产物（`--out` 目录）

| 文件 | 内容 |
| --- | --- |
| `frames.csv` | 每帧一行（帧元数据 + 预标注版本 + 复核人/日期，留给人工填） |
| `instances.csv` | **每实例一行**（菌包 + 菌包肩门后的菇体候选）；坐标一律**原图 2880×1616 px** |
| `overlays/<frame_id>.jpg` | 原图 + 绿框（预标注菇体）/蓝框（菌包）/**红细框（被袋肩门丢掉的候选）** |
| `README.md` | 复核说明 + 边界情况判据（遮挡/触边框/黏连/不可判） |

**红线**：坐标一律原图坐标，**不许在 `ds=2` 工作坐标里标**（否则 mm 换算与将来训练都错）；
被袋肩门丢掉的候选**必须画进 overlay**（否则算不出漏检），但不逐条进 CSV（那是几千行人工拒绝）。

用法（仓库根）：

    python measure/tools/make_annotation_sheet.py --out .scratch/annotation-20 --n 20
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "src"))  # measure/src

from measure.bag_ruler import (  # noqa: E402
    BagConfig,
    find_bags,
    nearest_bag_in_same_row,
    row_is_reliable,
)
from measure.estimate import filter_above_bag_top  # noqa: E402
from measure.fruitbody import FruitConfig, find_fruitbodies  # noqa: E402

DEFAULT_SAMPLES = Path(r"D:\code\mushroom\.scratch\frame-quality-samples")
ROI_FRAC = 0.45

#: 不进标注样本的已知坏轮（票 13-round-content-integrity；与 analysis 的 LEGACY_EXCLUSIONS 同窗口）
EXCLUDED_WINDOWS = (("2026-10-09T18:20:00", "2026-10-09T18:28:00"),)

FRAME_COLUMNS = [
    "frame_id", "file", "station_id", "layer", "col", "ts", "date", "angle_profile",
    "w_px", "h_px", "stratum", "pre_annotator", "pre_version", "reviewer",
    "review_date", "n_bag_pre", "n_fruit_pre", "n_fruit_gate_kept", "n_fruit_gate_dropped",
    "notes",
]

INSTANCE_COLUMNS = [
    # 身份
    "frame_id", "instance_id", "category", "source", "review_status",
    # 框（原图 px，2880×1616）
    "bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1",
    # 菇体专属：归属与簇
    "bag_id", "cluster_id", "base_y", "tip_y",
    # 判据与边界
    "height_valid", "truncated", "occluded_frac", "ignore", "gate", "reviewer_note",
]

#: 被袋肩门丢掉的候选（**不进 instances.csv**，只给复核页当"漏检线索"画红框用）
DROPPED_COLUMNS = [
    "frame_id", "candidate_id", "bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1",
    "reason", "bag_id",
]


def _in_excluded(ts: str) -> bool:
    for start, end in EXCLUDED_WINDOWS:
        if start <= ts <= end:
            return True
    return False


def select_frames(entries: list[dict], n: int) -> list[dict]:
    """系统性抽样：按 ts 排序后等间隔取 n 帧（可复现），并排除已知坏轮。"""
    ok = [e for e in entries
          if e.get("label_auto") == "ok" and not _in_excluded(e.get("ts", ""))]
    ok.sort(key=lambda e: e.get("ts", ""))
    if len(ok) <= n:
        return ok
    step = len(ok) / float(n)
    picked, seen = [], set()
    for i in range(n):
        e = ok[min(len(ok) - 1, int(i * step))]
        if e["file"] not in seen:
            seen.add(e["file"])
            picked.append(e)
    return picked


def _read_gray(path: Path) -> np.ndarray:
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB")).astype(np.float32).mean(axis=2)


def annotate_one(entry: dict, samples: Path, out: Path, *, seq: int, bag_cfg: BagConfig,
                 fruit_cfg: FruitConfig, pre_version: str) -> tuple[dict, list[dict], list[dict]]:
    from PIL import Image, ImageDraw

    gray = _read_gray(samples / entry["file"])
    h, w = gray.shape
    bags, bdiag = find_bags(gray, bag_cfg)
    y0 = int(h * (1.0 - ROI_FRAC))
    insts, fdiag = find_fruitbodies(gray[y0:], None, fruit_cfg, origin=(y0, 0))
    kept, gdiag = filter_above_bag_top(insts, bags)

    # frame_id 要**唯一且可读**：序号 + 站位 + 紧凑时刻（例：F07_S110_20261013T152136）
    ts = entry.get("ts", "")
    stamp = ts.replace("-", "").replace(":", "") if ts else entry.get("date", "?")
    fid = f"F{seq:02d}_{entry.get('station_id', 'S?')}_{stamp}"
    rows: list[dict] = []
    idx = 0
    bag_ids: dict[int, str] = {}
    for b in bags:
        idx += 1
        bag_id = f"{fid}-B{idx:02d}"
        bag_ids[id(b)] = bag_id
        rows.append({
            "frame_id": fid, "instance_id": f"{fid}-{idx:03d}", "category": "bag",
            "source": "pre", "review_status": "pending",
            "bbox_x0": int(b.x0), "bbox_y0": int(b.y0),
            "bbox_x1": int(b.x1), "bbox_y1": int(b.y1),
            "bag_id": "", "cluster_id": "",
            "base_y": "", "tip_y": "",
            "height_valid": "", "truncated": "",
            "occluded_frac": "", "ignore": "", "gate": "", "reviewer_note": "",
        })
    # 簇：同袋上的实例归一个 cluster_id（供"菌簇长度/直径"复算）
    cluster_seq: dict[str, int] = {}
    for inst in kept:
        bag = nearest_bag_in_same_row(bags, inst.cy, bag_cfg, x=inst.cx)
        bid = bag_ids.get(id(bag), "") if bag is not None else ""
        if bid:
            cluster_seq[bid] = cluster_seq.get(bid, 0) + 1
            cluster_id = f"{bid}-C{cluster_seq[bid]:02d}"
        else:
            cluster_id = ""
        idx += 1
        rows.append({
            "frame_id": fid, "instance_id": f"{fid}-{idx:03d}", "category": "fruitbody",
            "source": "pre", "review_status": "pending",
            "bbox_x0": int(inst.x0), "bbox_y0": int(inst.y0),
            "bbox_x1": int(inst.x1), "bbox_y1": int(inst.y1),
            "bag_id": bid, "cluster_id": cluster_id,
            "base_y": int(inst.base_y), "tip_y": int(inst.tip_y),
            # 两端都可见才允许用高度（被切/被遮 ⇒ 0 = 杆高不可用，但仍参与有/无检测）
            "height_valid": 0 if (inst.base_y <= 0 or inst.tip_y <= 0) else 1,
            "truncated": 1 if (inst.x0 <= 0 or inst.y0 <= 0 or inst.x1 >= w - 1
                               or inst.y1 >= h - 1) else 0,
            "occluded_frac": "", "ignore": "", "gate": "gated", "reviewer_note": "",
        })

    # overlay：绿=保留的菇体、蓝=菌包、红细=被袋肩门丢掉的候选（漏检证据）
    img = Image.open(samples / entry["file"]).convert("RGB")
    draw = ImageDraw.Draw(img)
    for b in bags:
        draw.rectangle([b.x0, b.y0, b.x1, b.y1], outline=(0, 120, 255), width=4)
    dropped = {id(i) for i in insts} - {id(i) for i in kept}
    dropped_rows: list[dict] = []
    cand_seq = 0
    for i in insts:
        if id(i) not in dropped:
            continue
        cand_seq += 1
        bag = nearest_bag_in_same_row(bags, i.cy, bag_cfg, x=i.cx)
        bid = bag_ids.get(id(bag), "") if bag is not None else ""
        if bag is None:
            reason = "unassigned_no_same_row_bag"
        else:
            bag_top = bag.centroid_y - bag.height_px / 2.0
            reason = ("inside_bag_body" if i.base_y > bag_top + 10 else "other")
        dropped_rows.append({
            "frame_id": fid, "candidate_id": f"{fid}-D{cand_seq:03d}",
            "bbox_x0": int(i.x0), "bbox_y0": int(i.y0),
            "bbox_x1": int(i.x1), "bbox_y1": int(i.y1),
            "reason": reason, "bag_id": bid,
        })
    for i in insts:
        if id(i) in dropped:
            draw.rectangle([i.x0, i.y0, i.x1, i.y1], outline=(255, 0, 0), width=2)
    for i in kept:
        draw.rectangle([i.x0, i.y0, i.x1, i.y1], outline=(0, 255, 0), width=3)
    (out / "overlays").mkdir(parents=True, exist_ok=True)
    img.save(out / "overlays" / f"{fid}.jpg", quality=88)

    frame_row = {
        "frame_id": fid, "file": entry["file"], "station_id": entry.get("station_id", ""),
        "layer": entry.get("layer", ""), "col": entry.get("col", ""),
        "ts": entry.get("ts", ""), "date": entry.get("date", ""),
        "angle_profile": entry.get("angle_profile", ""), "w_px": w, "h_px": h,
        "stratum": _stratum(entry), "pre_annotator": "measure L1+L3",
        "pre_version": pre_version, "reviewer": "", "review_date": "",
        "n_bag_pre": len(bags), "n_fruit_pre": len(insts),
        "n_fruit_gate_kept": len(kept), "n_fruit_gate_dropped": len(insts) - len(kept),
        "notes": f"bdiag n_rows={bdiag.get('n_rows')} weak_retry={bdiag.get('weak_retry')}; "
                 f"gate kept_frac={gdiag['kept_frac']:.2f}",
    }
    return frame_row, rows, dropped_rows


def _stratum(entry: dict) -> str:
    ts = entry.get("ts", "")
    hour = int(ts[11:13]) if len(ts) > 13 else -1
    light = "day" if 6 <= hour < 18 else "night"
    return f"{entry.get('date', '?')}-{light}"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 20 帧标注工单（预标注 + 人复核）")
    ap.add_argument("--samples", default=str(DEFAULT_SAMPLES))
    ap.add_argument("--out", required=True)
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--downscale", type=int, default=2)
    args = ap.parse_args()

    samples = Path(args.samples)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with (samples / "labels.csv").open(encoding="utf-8") as fh:
        entries = list(csv.DictReader(fh))
    picked = select_frames(entries, args.n)

    import subprocess
    try:
        rev = subprocess.run(["git", "-C", str(_HERE.parents[2]), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
    except Exception:      # noqa: BLE001
        rev = "unknown"
    pre_version = f"{rev}@{datetime.now():%Y-%m-%d}"

    bag_cfg, fruit_cfg = BagConfig(downscale=args.downscale), FruitConfig(downscale=args.downscale)
    frame_rows, inst_rows, dropped_rows = [], [], []
    for i, e in enumerate(picked, 1):
        fr, rows, drops = annotate_one(e, samples, out, seq=i, bag_cfg=bag_cfg,
                                       fruit_cfg=fruit_cfg, pre_version=pre_version)
        frame_rows.append(fr)
        inst_rows.extend(rows)
        dropped_rows.extend(drops)
        print(f"[{i}/{len(picked)}] {fr['frame_id']} 菌包 {fr['n_bag_pre']} / "
              f"菇体候选 {fr['n_fruit_pre']} → 门后 {fr['n_fruit_gate_kept']}")

    with (out / "frames.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=FRAME_COLUMNS)
        wr.writeheader()
        wr.writerows(frame_rows)
    with (out / "instances.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=INSTANCE_COLUMNS)
        wr.writeheader()
        wr.writerows(inst_rows)
    # 被门丢掉的候选：**给复核页画红框用**（不进 instances.csv，避免几千行人工拒绝）
    with (out / "dropped.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=DROPPED_COLUMNS)
        wr.writeheader()
        wr.writerows(dropped_rows)
    (out / "README.md").write_text(_readme(pre_version, len(frame_rows), len(inst_rows)),
                                   encoding="utf-8")
    print(f"\n工单 → {out}（{len(frame_rows)} 帧 / {len(inst_rows)} 实例行 / "
          f"{len(dropped_rows)} 个被门丢掉的候选，预标注版本 {pre_version}）")
    return 0


def _readme(pre_version: str, n_frames: int, n_inst: int) -> str:
    return f"""# 20 帧人工标注工单（预标注 + 人只复核）

- 预标注版本：`{pre_version}`（measure L1 菌包 + L3 菇体候选 + 袋肩门）
- 帧数：{n_frames}｜实例行：{n_inst}（**不含**被袋肩门丢掉的候选——它们在 overlay 里画红框）
- 坐标单位：**原图像素**（2880×1616）。**禁止**在降采样工作坐标里标。

## 谁做什么：人**只做四件事**

| # | 动作 | 怎么记 | 为什么必须这么记 |
| --- | --- | --- | --- |
| 1 | **确认** | `review_status=confirmed` | 预标注正确，进 precision 的分子 |
| 2 | **改框** | `review_status=edited` + 改 `bbox_*`/`base_y`/`tip_y`/`bag_id` + 一句 note | 改前的框算错、改后的算对 |
| 3 | **删** | `review_status=rejected` + 一句 note（**行必须留**） | **删行 = 把假阳率洗白**；留着才能算假阳率 |
| 4 | **补漏** | **新增一行**：`source=human_added`、`review_status=confirmed` | 预标注漏掉的；这是 recall 的分子 |
| 4b | 补的是**被袋肩门丢掉的真菇**（overlay 红框） | 同上，并写 **`reviewer_note=gate_false_negative`** | **直接量化袋肩门的漏检** |

**两条不许动**：`ignore=1` 的行**必须保留**（删了漏检率的分母就错）；
`truncated=1 ⇒ height_valid=0`，但该实例**仍算检出、参与 recall**（切掉的是杆高，不是"有没有菇"）。

**红线**：坐标一律**原图 px**、`w_px/h_px` 一律 **2880×1616**；**禁止在 `ds=2` 工作坐标里标**。

**最容易被跳过、但价值最高的样本**：**"看起来明显没有菇"的帧也要逐框复核**——
尤其是 overlay 里满是红框的帧（例：工单里的 F01，候选全被袋肩门判掉），
它们是**袋肩门假阴/假阳判据**的来源；跳过它，就等于放弃对门本身的检验。

## 边界情况判据（**必须照此**，否则 precision/recall 没法解释）

| 情况 | 怎么标 |
| --- | --- |
| 触画面边缘（被切） | `truncated=1`；**`height_valid=0`**（杆高不可用），但仍算"检出到了"参与 recall |
| 遮挡 ≤1/3 | `occluded_frac=1`，正常参与统计 |
| 遮挡 1/3–2/3 | `occluded_frac=2`，正常参与统计但**杆高一致性只看 `height_valid=1` 的** |
| 遮挡 >2/3 或分不清是菇还是袋面 | `occluded_frac=3` + **`ignore=1`**；**必须保留该行**并写 note（删了就算不出漏检） |
| 多个菇黏在一起、分不开 | **标成一个实例**，`reviewer_note=merged`（不要按想象拆） |
| 菇长在袋顶上方 → 被袋肩门丢掉（红框） | 若确实是菇：**新增一行** `source=human_added`、`reviewer_note=gate_false_negative`（这条直接量化袋肩门的漏检） |
| 菌包被菇覆盖 | 仍按**可见部分**框一个 `category=bag`；`occluded_frac` 记录遮挡档 |
| 菌包只露出一小角 | 框它，`reviewer_note=partial_bag`；`n_bag` 的 precision/recall 会单独按"可见≥1/3"复核 |

## 复核完成后能算什么（这就是这份工单的目的）

- L1：菌包 **precision/recall**、袋宽 px 的一致性（与预标注对比 ⇒ 分割误差，不是像素测量误差）；
- L3：菇体实例 **precision/recall**（`rejected` 率 = 假阳率，`human_added` = 漏检率）；
- **袋肩门**的假阴/假阳（`gate_false_negative` 与红框侧）；
- 杆高一致性：`height_valid=1` 的实例，比较预标注高度与人工确认高度的差（px，再乘局部 mm/px）。
"""


if __name__ == "__main__":
    raise SystemExit(main())

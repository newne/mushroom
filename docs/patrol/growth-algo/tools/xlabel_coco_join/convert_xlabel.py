"""XLABEL JSON -> COCO(segment) + 侧车 objects.json 的 join 转换器（离线工具）。

定位（Chief 2026-10-14 授权）：
- **离线工具，不进任何镜像**：本文件位于 `docs/patrol/growth-algo/tools/`，镜像只带 measure/patrol/analysis/deploy/web，
  因此"不进镜像"是**结构性**的，不是承诺。
- 不碰 `measure/**`、不改 DDL、不动生产、不写训练/模型代码。
- 只在标准库上运行（无 numpy/PIL 依赖）。

背景（实测，2026-10-14 运维跑 `xlabel2coco --mode segment`）：
COCO 的 annotation 只剩 `area/bbox/category_id/id/ignore/image_id/iscrowd/segmentation`
⇒ **`attributes` / `group_id` / 帧级 flags 全丢**。所以：
- 主档案 = XLABEL JSON（唯一权威）；COCO 是派生物；
- 丢掉的语义必须由侧车 `objects.json` **完整承载**；
- join 键 `(filename, shape_index)` 必须 **1:1 可断言**（对不上报错，不许静默配错）；
- `review_status=rejected` 的对象**不许静默消失**：能进 COCO 的落 `iscrowd=1`，
  进不去的（`bag_top` 折线这类非掩码形态）必须在丢失清单里**点名 + 计数**。

`image_id` 口径（Chief 裁定 2026-10-14）：**派生确定性非空键 + 同一记录里同时带原始字段四元组**
（`room_id` / `ts` / `station_id` / `angle_profile`，后者落在 COCO `images[].raw_key` 与侧车 `frame` 段）。

用法：
    python convert_xlabel.py --images IMG_DIR [--labels JSON_DIR] --classes labels.txt --out OUT_DIR
                             [--room 611] [--expect-size 2880x1616] [--strict]
退出码：0 = join 断言全过；1 = join 断言失败（**不许当成功**）；2 = `--strict` 且存在丢失清单条目。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

# 掩码形态：能进 COCO segmentation；其余（linestrip/line/point…）只能进侧车
MASK_SHAPE_TYPES = {"polygon", "rectangle", "circle", "rotation", "quadrilateral"}

NAME_RE = re.compile(
    r"^(?:(?P<stamp>\d{8})_)?(?P<station>S\d{3})_(?P<profile>[A-Za-z0-9]+)_(?P<time>\d{6})$"
)


class JoinError(RuntimeError):
    """join 断言失败：必须让调用方失败，不许静默继续。"""


def parse_frame_key(filename: str, room: str | None = None, image_path: str | None = None) -> dict:
    """派生确定性非空 image_id，并同时带原始字段四元组。"""
    stem = Path(filename).stem
    m = NAME_RE.match(stem)
    if m:
        date, time = m.group("stamp"), m.group("time")
        ts = None
        if date:
            ts = f"{date[0:4]}-{date[4:6]}-{date[6:8]}T{time[0:2]}:{time[2:4]}:{time[4:6]}"
        image_id = f"{date + 'T' + time if date else time}.{m.group('station')}.{m.group('profile')}"
        station_id, profile = m.group("station"), m.group("profile")
    else:  # 叫不出身份的帧也要有确定性的非空键，不许空
        ts, station_id, profile = None, None, None
        image_id = f"file.{stem}"
    return {
        "image_id": image_id,
        "raw_key": {
            "room_id": room,
            "ts": ts,
            "station_id": station_id,
            "angle_profile": profile,
            "source_file": image_path or filename,
        },
    }


def read_classes(path: Path) -> list[str]:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    classes = [ln for ln in lines if ln and not ln.startswith("#")]
    if not classes:
        raise SystemExit(f"类别文件为空：{path}")
    if len(set(classes)) != len(classes):
        raise SystemExit(f"类别文件有重复：{path}")
    return classes


def load_frames(images_dir: Path, labels_dir: Path | None = None) -> list[tuple[Path, Path, dict]]:
    """返回 [(image_path, json_path, xlabel_dict)]，按文件名排序；无同名 JSON 的图跳过。"""
    labels_dir = labels_dir or images_dir
    out = []
    for img in sorted(p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES):
        js = labels_dir / f"{img.stem}.json"
        if not js.exists():
            continue
        out.append((img, js, json.loads(js.read_text(encoding="utf-8"))))
    return out


def shape_records(frame: dict, filename: str) -> list[dict]:
    """把一帧的全部 shape 摊平，shape_index = 在 `shapes[]` 里的下标（join 键的一半）。"""
    recs = []
    for i, s in enumerate(frame.get("shapes") or []):
        recs.append(
            {
                "filename": filename,
                "shape_index": i,
                "label": s.get("label"),
                "shape_type": s.get("shape_type"),
                "points": s.get("points"),
                "group_id": s.get("group_id"),
                "attributes": s.get("attributes") or {},
                "difficult": s.get("difficult"),
                "description": s.get("description"),
                "score": s.get("score"),
            }
        )
    return recs


def is_rejected(rec: dict) -> bool:
    return str((rec.get("attributes") or {}).get("review_status", "")).lower() == "rejected"


def mask_polygon(rec: dict):
    st = rec.get("shape_type")
    pts = rec.get("points") or []
    if st in ("polygon", "linestrip") and len(pts) >= 3:
        return [[float(x), float(y)] for x, y in pts]
    if st == "rectangle" and len(pts) >= 2:
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
    return None


def convert(images_dir: Path, labels_dir: Path | None, classes: list[str], out_dir: Path,
            room: str | None = None, expect_size: tuple[int, int] | None = None) -> dict:
    frames = load_frames(images_dir, labels_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    coco = {"info": {"description": "XLABEL->COCO join converter", "version": "1.0"},
            "licenses": [], "images": [], "annotations": [], "categories": []}
    for cid, name in enumerate(classes, start=1):
        coco["categories"].append({"id": cid, "name": name, "supercategory": ""})
    class_ids = {name: cid for cid, name in enumerate(classes, start=1)}

    sidecar = {"frames": [], "objects": []}
    all_records: list[dict] = []
    loss_items: list[dict] = []
    next_ann_id = 1

    for idx, (img, js, frame) in enumerate(frames, start=1):
        fk = parse_frame_key(img.name, room=room, image_path=str(img))
        width = frame.get("imageWidth")
        height = frame.get("imageHeight")
        frame_meta = {
            "flags": frame.get("flags") or {},
            "tags": frame.get("tags") or [],
            "checked": frame.get("checked"),
            "description": frame.get("description"),
        }
        coco["images"].append({"id": idx, "file_name": img.name, "width": width, "height": height,
                               "raw_key": fk["raw_key"], "frame_meta": frame_meta,
                               "label_file": js.name})
        sidecar["frames"].append({"coco_image_id": idx, "file_name": img.name, "image_id": fk["image_id"],
                                  "raw_key": fk["raw_key"], "frame_meta": frame_meta,
                                  "width": width, "height": height})
        if expect_size and (width, height) != expect_size:
            loss_items.append({"filename": img.name, "shape_index": None, "label": None,
                               "reason": "size_mismatch", "detail": f"{width}x{height} != {expect_size[0]}x{expect_size[1]}"})

        for rec in shape_records(frame, img.name):
            rec = dict(rec)
            rec["coco_image_id"] = idx
            rec["image_id"] = fk["image_id"]
            all_records.append(rec)
            key = (rec["filename"], rec["shape_index"])
            entry = {
                "key": {"filename": key[0], "shape_index": key[1]},
                "coco_image_id": idx,
                "image_id": fk["image_id"],
                "label": rec["label"],
                "shape_type": rec["shape_type"],
                "points": rec["points"],
                "group_id": rec["group_id"],
                "attributes": rec["attributes"],
                "difficult": rec["difficult"],
                "description": rec["description"],
                "score": rec["score"],
                "coco_annotation_id": None,
                "coco_iscrowd": None,
                "preserved_as": None,
            }
            poly = mask_polygon(rec) if rec["shape_type"] in MASK_SHAPE_TYPES else None
            if rec["label"] not in class_ids:
                raise JoinError(f"类别不在 labels.txt：{rec['label']}（{img.name} shape#{rec['shape_index']}）")
            if poly is not None:
                iscrowd = 1 if is_rejected(rec) else 0
                ann = {"id": next_ann_id, "image_id": idx, "category_id": class_ids[rec["label"]],
                       "segmentation": [sum(poly, [])], "iscrowd": iscrowd,
                       "sidecar_key": {"filename": key[0], "shape_index": key[1]}}
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                ann["bbox"] = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
                ann["area"] = float(abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                                            - poly[(i + 1) % len(poly)][0] * poly[i][1]
                                            for i in range(len(poly))) / 2.0))
                coco["annotations"].append(ann)
                entry["coco_annotation_id"] = next_ann_id
                entry["coco_iscrowd"] = iscrowd
                entry["preserved_as"] = "iscrowd" if iscrowd else "coco_segmentation"
                next_ann_id += 1
            else:
                entry["preserved_as"] = "sidecar_only"
                loss_items.append({"filename": rec["filename"], "shape_index": rec["shape_index"],
                                   "label": rec["label"], "reason": f"non_mask_shape_type:{rec['shape_type']}",
                                   "preserved_as": "sidecar_only"})
            sidecar["objects"].append(entry)

    report = build_loss_report(frames, all_records, sidecar, coco, loss_items)

    (out_dir / "annotations.coco.json").write_text(json.dumps(coco, ensure_ascii=False), encoding="utf-8")
    (out_dir / "objects.json").write_text(json.dumps(sidecar, ensure_ascii=False), encoding="utf-8")
    (out_dir / "coco_loss_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    join_check(all_records, sidecar, coco, report)
    return report


def build_loss_report(frames, records, sidecar, coco, loss_items) -> dict:
    """把"COCO 侧丢了什么、谁承载、谁必须点名"写成可见清单（不是没报错就算对）。"""
    n_flags = sum(1 for f in sidecar["frames"] if f["frame_meta"]["flags"])
    carried = {
        "attributes": sum(1 for r in records if r["attributes"]),
        "group_id": sum(1 for r in records if r["group_id"] is not None),
        "frame_flags": n_flags,
        "difficult": sum(1 for r in records if r["difficult"]),
        "description": sum(1 for r in records if r["description"]),
        "shape_type": len(records),
    }
    rejected = [r for r in records if is_rejected(r)]
    return {
        "verdict": "lossy_but_documented",
        "note": "COCO 无属性位（实测 annotation 只剩 area/bbox/category_id/id/ignore/image_id/iscrowd/segmentation）；"
                "下列语义全部由 objects.json 侧车承载，COCO 仅作训练派生物。",
        "totals": {"frames": len(frames), "shapes": len(records),
                   "coco_annotations": len(coco["annotations"]),
                   "sidecar_objects": len(sidecar["objects"]),
                   "rejected_shapes": len(rejected)},
        "carried_by_sidecar": carried,
        "rejected": [{"filename": r["filename"], "shape_index": r["shape_index"], "label": r["label"],
                      "shape_type": r["shape_type"],
                      "preserved_as": ("iscrowd" if r["shape_type"] in MASK_SHAPE_TYPES and mask_polygon(r) else "sidecar_only")}
                     for r in rejected],
        "items": loss_items,
    }


def join_check(records, sidecar, coco, report) -> None:
    """三条硬断言：1:1、rejected 不静默消失、侧车完整承载。任一条不成立 ⇒ JoinError。"""
    rec_keys = [(r["filename"], r["shape_index"]) for r in records]
    side_keys = [(o["key"]["filename"], o["key"]["shape_index"]) for o in sidecar["objects"]]
    if len(set(rec_keys)) != len(rec_keys):
        raise JoinError("源 JSON 里 (filename, shape_index) 有重复，join 键不成立")
    if sorted(rec_keys) != sorted(side_keys):
        missing = sorted(set(rec_keys) - set(side_keys))
        extra = sorted(set(side_keys) - set(rec_keys))
        raise JoinError(f"join 不是 1:1：sidecar 少 {len(missing)} 条 {missing[:3]} / 多 {len(extra)} 条 {extra[:3]}")

    # 侧车必须完整承载：属性/group_id/帧级 flags 逐条相等
    by_key = {(o["key"]["filename"], o["key"]["shape_index"]): o for o in sidecar["objects"]}
    for r in records:
        o = by_key[(r["filename"], r["shape_index"])]
        if o["attributes"] != r["attributes"] or o["group_id"] != r["group_id"] or o["shape_type"] != r["shape_type"]:
            raise JoinError(f"侧车未完整承载：{r['filename']} shape#{r['shape_index']}")
    frame_by_name = {f["file_name"]: f for f in sidecar["frames"]}
    for img in coco["images"]:
        if frame_by_name[img["file_name"]]["frame_meta"] != img["frame_meta"]:
            raise JoinError(f"帧级 flags 未完整承载：{img['file_name']}")

    # COCO 的每条 annotation 都必须能回到侧车
    ann_ids = [a["id"] for a in coco["annotations"]]
    if len(set(ann_ids)) != len(ann_ids):
        raise JoinError("COCO annotation id 重复")
    for a in coco["annotations"]:
        k = (a["sidecar_key"]["filename"], a["sidecar_key"]["shape_index"])
        if k not in by_key:
            raise JoinError(f"COCO annotation {a['id']} 回不到侧车：{k}")

    # rejected 不许静默消失：掩码形态落 iscrowd=1，非掩码形态必须在本清单里点名
    named = {(i["filename"], i["shape_index"]) for i in report["items"]}
    for r in records:
        if not is_rejected(r):
            continue
        k = (r["filename"], r["shape_index"])
        o = by_key[k]
        if o["coco_annotation_id"] is not None:
            if o["coco_iscrowd"] != 1:
                raise JoinError(f"rejected 对象未落 iscrowd=1：{k}")
        elif k not in named:
            raise JoinError(f"rejected 对象既没进 COCO 也没进丢失清单：{k}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="XLABEL JSON -> COCO(segment) + 侧车 objects.json")
    ap.add_argument("--images", required=True, type=Path)
    ap.add_argument("--labels", type=Path, default=None)
    ap.add_argument("--classes", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--room", default=None)
    ap.add_argument("--expect-size", default=None, help="如 2880x1616；不符进丢失清单")
    ap.add_argument("--strict", action="store_true", help="丢失清单非空 ⇒ 退出码 2")
    args = ap.parse_args(argv)

    size = None
    if args.expect_size:
        w, h = args.expect_size.lower().split("x")
        size = (int(w), int(h))
    report = convert(args.images, args.labels, read_classes(args.classes), args.out,
                     room=args.room, expect_size=size)
    t = report["totals"]
    print(f"joined: {t['frames']} 帧 / {t['shapes']} shape -> COCO {t['coco_annotations']} ann + 侧车 {t['sidecar_objects']}")
    print(f"丢失清单：non-mask/尺寸 等 {len(report['items'])} 条；rejected {t['rejected_shapes']} 个（已点名）")
    if args.strict and report["items"]:
        print("--strict：丢失清单非空 ⇒ 退出码 2", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

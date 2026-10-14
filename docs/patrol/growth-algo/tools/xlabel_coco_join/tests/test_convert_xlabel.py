"""`convert_xlabel.py` 的用例。

两种跑法都可以（本机 venv 曾被并发重建，所以**不依赖 pytest 也能跑**）：
    pytest tests/test_convert_xlabel.py
    python tests/test_convert_xlabel.py    # 自带 runner：逐条 PASS/FAIL，红则退出码 1

Chief 的三条测试要求对应关系：
① join 1:1 正向断言        -> test_join_keys_are_one_to_one / test_sidecar_carries_...
② 反向自证（改坏就红）      -> test_missing_sidecar_entry_is_detected
                              test_rejected_polygon_must_be_iscrowd
                              test_rejected_linestrip_must_be_named_in_loss_report
③ 丢失清单可见             -> test_loss_report_names_every_non_mask_shape / test_loss_report_totals
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import convert_xlabel as cv  # noqa: E402

CLASSES = ["bag", "cluster", "cap", "bag_top", "void"]


def _poly_attrs(review="confirmed"):
    return {"occluded": "no", "truncated": "no", "association_valid": "true",
            "measurement_valid": "true", "rejection_reason": "none", "review_status": review}


def _frame(name="20261013_S406_level0_092634.jpg", shapes=None, flags=None):
    return {
        "version": "4.1.1",
        "flags": flags if flags is not None else {"cap_not_clearly_formed": True},
        "tags": [],
        "shapes": shapes if shapes is not None else [
            {"label": "bag", "shape_type": "polygon", "points": [[10, 20], [50, 20], [50, 80], [10, 80]],
             "group_id": 1, "attributes": _poly_attrs(), "difficult": False, "description": None, "score": None},
            {"label": "cluster", "shape_type": "polygon", "points": [[12, 5], [48, 5], [48, 18], [12, 18]],
             "group_id": 1, "attributes": _poly_attrs(), "difficult": False, "description": None, "score": None},
            {"label": "bag_top", "shape_type": "linestrip", "points": [[10, 20], [30, 18], [50, 20]],
             "group_id": 1, "attributes": {"baseline_valid": "true", "review_status": "confirmed"},
             "difficult": False, "description": None, "score": None},
        ],
        "description": None, "checked": True,
        "imagePath": name, "imageData": None, "imageHeight": 1616, "imageWidth": 2880,
    }


def _write_fixture(tmp: Path, frames: dict, classes=CLASSES):
    img_dir = tmp / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    for name, obj in frames.items():
        (img_dir / name).write_bytes(b"\xff\xd8\xff")          # 只用到后缀，不读像素
        (img_dir / (Path(name).stem + ".json")).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    cls = tmp / "labels.txt"
    cls.write_text("\n".join(classes) + "\n", encoding="utf-8")
    return img_dir, cls


def _run(tmp: Path, frames: dict, **kw):
    img_dir, cls = _write_fixture(tmp, frames)
    out = tmp / "out"
    report = cv.convert(img_dir, None, cv.read_classes(cls), out, room="611", **kw)
    coco = json.loads((out / "annotations.coco.json").read_text(encoding="utf-8"))
    side = json.loads((out / "objects.json").read_text(encoding="utf-8"))
    return report, coco, side, out


def test_join_keys_are_one_to_one():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        report, coco, side, _ = _run(tmp, {"20261013_S406_level0_092634.jpg": _frame(),
                                           "20261013_S209_level0_152232.jpg": _frame("20261013_S209_level0_152232.jpg")})
        rec_keys = [(o["key"]["filename"], o["key"]["shape_index"]) for o in side["objects"]]
        assert len(rec_keys) == len(set(rec_keys)) == report["totals"]["shapes"] == 6
        assert sorted(rec_keys) == sorted((f["file_name"], i)
                                          for f in side["frames"] for i in range(3))
        # 每条 COCO annotation 都能回到侧车
        by = {(o["key"]["filename"], o["key"]["shape_index"]): o for o in side["objects"]}
        for a in coco["annotations"]:
            k = (a["sidecar_key"]["filename"], a["sidecar_key"]["shape_index"])
            assert by[k]["coco_annotation_id"] == a["id"]
        assert len(coco["annotations"]) == 4          # 2 帧 × (bag+cluster)，bag_top 不进 COCO


def test_missing_sidecar_entry_is_detected():
    """反向自证：把 1:1 检查绕过/改坏，这条会红。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        report, coco, side, _ = _run(tmp, {"20261013_S406_level0_092634.jpg": _frame()})
        report["items"] = []
        broken = json.loads(json.dumps(side))
        broken["objects"].pop()                        # 侧车少一条 ⇒ join 不再 1:1
        records = []
        for name, _, obj in cv.load_frames(tmp / "images", None):
            records += cv.shape_records(obj, name)
        for r in records:
            r["coco_image_id"] = 1
        try:
            cv.join_check(records, broken, coco, report)
        except cv.JoinError:
            return
        raise AssertionError("侧车少一条却没报错：join 1:1 断言被改坏了")


def test_sidecar_carries_attributes_group_id_frame_flags():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _, coco, side, _ = _run(tmp, {"20261013_S406_level0_092634.jpg": _frame()})
        bag = next(o for o in side["objects"] if o["label"] == "bag")
        assert bag["attributes"] == _poly_attrs()
        assert bag["group_id"] == 1
        assert bag["shape_type"] == "polygon"
        assert side["frames"][0]["frame_meta"]["flags"] == {"cap_not_clearly_formed": True}
        assert side["frames"][0]["frame_meta"]["checked"] is True
        # COCO 侧确实没有这些键（所以侧车是唯一承载）
        assert "attributes" not in coco["annotations"][0]
        assert "group_id" not in coco["annotations"][0]
        assert "flags" not in coco["images"][0]


def test_rejected_polygon_must_be_iscrowd():
    """rejected 的掩码形态必须落 iscrowd=1——改坏（比如恒为 0）这条就红。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        shp = _frame()["shapes"]
        shp[1]["attributes"] = _poly_attrs(review="rejected")
        report, coco, side, _ = _run(tmp, {"20261013_S406_level0_092634.jpg": _frame(shapes=shp)})
        rej = [o for o in side["objects"] if cv.is_rejected(o)]
        assert len(rej) == 1 and rej[0]["coco_iscrowd"] == 1
        ann = next(a for a in coco["annotations"] if a["sidecar_key"]["shape_index"] == 1)
        assert ann["iscrowd"] == 1
        assert any(r["filename"] == "20261013_S406_level0_092634.jpg" and r["shape_index"] == 1
                   and r["preserved_as"] == "iscrowd" for r in report["rejected"])


def test_rejected_linestrip_must_be_named_in_loss_report():
    """rejected 的折线进不了 COCO ⇒ 必须在丢失清单里点名；删掉那条 ⇒ 报错（反向自证）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        shp = _frame()["shapes"]
        shp[2]["attributes"] = {"baseline_valid": "false", "review_status": "rejected"}
        report, coco, side, _ = _run(tmp, {"20261013_S406_level0_092634.jpg": _frame(shapes=shp)})
        hit = [i for i in report["items"] if i["shape_index"] == 2]
        assert hit and hit[0]["reason"].startswith("non_mask_shape_type:")
        assert hit[0]["preserved_as"] == "sidecar_only"

        records = []
        for name, _, obj in cv.load_frames(tmp / "images", None):
            records += cv.shape_records(obj, name)
        for r in records:
            r["coco_image_id"] = 1
        report["items"] = [i for i in report["items"] if i["shape_index"] != 2]     # 点名被删掉
        try:
            cv.join_check(records, side, coco, report)
        except cv.JoinError:
            return
        raise AssertionError("rejected 折线既没进 COCO 也没在清单里点名，却没报错")


def test_loss_report_names_every_non_mask_shape():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        report, _, _, _ = _run(tmp, {"20261013_S406_level0_092634.jpg": _frame(),
                                     "20261013_S209_level0_152232.jpg": _frame("20261013_S209_level0_152232.jpg")})
        items = report["items"]
        assert len(items) == 2 and all(i["label"] == "bag_top" for i in items)
        assert {i["filename"] for i in items} == {"20261013_S406_level0_092634.jpg",
                                                  "20261013_S209_level0_152232.jpg"}
        t = report["totals"]
        assert (t["frames"], t["shapes"], t["coco_annotations"], t["sidecar_objects"]) == (2, 6, 4, 6)
        assert report["carried_by_sidecar"]["attributes"] == 6          # 2 帧 × 3 shape 全带属性
        assert report["carried_by_sidecar"]["group_id"] == 6
        assert report["carried_by_sidecar"]["frame_flags"] == 2
        assert report["verdict"] == "lossy_but_documented"


def test_image_id_is_deterministic_nonempty_with_raw_tuple():
    a = cv.parse_frame_key("20261013_S406_level0_092634.jpg", room="611", image_path="/x/a.jpg")
    b = cv.parse_frame_key("20261013_S406_level0_092634.jpg", room="611", image_path="/x/a.jpg")
    assert a == b and a["image_id"]
    assert a["image_id"] == "20261013T092634.S406.level0"
    assert a["raw_key"] == {"room_id": "611", "ts": "2026-10-13T09:26:34", "station_id": "S406",
                            "angle_profile": "level0", "source_file": "/x/a.jpg"}
    weird = cv.parse_frame_key("not-a-scheme.jpg", room=None)
    assert weird["image_id"] == "file.not-a-scheme" and weird["image_id"]


def test_size_mismatch_is_listed():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frame = _frame()
        frame["imageWidth"], frame["imageHeight"] = 1440, 808
        img_dir, cls = _write_fixture(tmp, {"20261013_S406_level0_092634.jpg": frame})
        report = cv.convert(img_dir, None, cv.read_classes(cls), tmp / "out", room="611",
                            expect_size=(2880, 1616))
        assert any(i["reason"] == "size_mismatch" for i in report["items"])


if __name__ == "__main__":  # pragma: no cover - 无 pytest 也能跑的 runner
    import traceback

    tests = sorted((k, v) for k, v in list(globals().items())
                   if k.startswith("test_") and callable(v))
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception:
            bad += 1
            print("FAIL", name)
            traceback.print_exc()
    print(f"{len(tests) - bad} passed / {len(tests)} total")
    raise SystemExit(1 if bad else 0)

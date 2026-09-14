"""`deploy.framing_wire`：把 `--framing` 变成真钩子。

测的重点是"什么情况下**不**开启"：关着、缺标定换算、解不了 JPEG——三条都不能
静默地"看起来开了"。
"""

from __future__ import annotations

from argparse import Namespace

import pytest
from deploy.framing_wire import build, make_apply_trim
from patrol.stations import Station


def args(**kw) -> Namespace:
    base = {"framing": "once", "mm_per_px": 0.25, "mm_per_px_z": None,
            "framing_sign_y": 1.0, "framing_sign_z": -1.0}
    base.update(kw)
    return Namespace(**base)


def fake_imread(_data):
    return "gray-array"


def test_off_is_the_default_and_says_so():
    got = build(args(framing="off"))
    assert got.hook is None
    assert "关闭" in got.reason


def test_missing_calibration_refuses_to_enable():
    """没有 mm/px 就只能瞎挪——宁可不开。"""
    got = build(args(mm_per_px=None))
    assert got.hook is None
    assert "mm-per-px" in got.reason


def test_enabled_hook_carries_recipe_and_signs():
    got = build(args(mm_per_px=0.25, mm_per_px_z=0.5), imread=fake_imread)
    assert got.enabled
    r = got.hook.recipe
    assert r.mm_per_px_y == 0.25 and r.mm_per_px_z == 0.5
    assert (r.sign_y, r.sign_z) == (1.0, -1.0)
    assert "开启" in got.reason and "0.2500" in got.reason


def test_mm_per_px_z_defaults_to_the_horizontal_value():
    got = build(args(mm_per_px=0.3), imread=fake_imread)
    assert got.hook.recipe.mm_per_px_z == 0.3


def test_missing_pillow_refuses_clearly():
    def imread(_data):
        raise ImportError("No module named 'PIL'")

    got = build(args(), imread=imread)
    assert got.hook is None
    assert "pillow" in got.reason and "PIL" in got.reason, "要把缺的模块名报出来"


def test_pixels_fetch_from_the_cloud_url():
    seen: list[str] = []
    got = build(args(), fetch=lambda url: (seen.append(url), b"jpg")[1], imread=fake_imread)
    got.hook.pixels(type("R", (), {"cloud_url": "http://minio/mogu/x.jpg"})())
    assert seen == ["http://minio/mogu/x.jpg"]


def test_evaluate_is_the_bright_region_offset():
    got = build(args(), imread=fake_imread)
    import numpy as np

    gray = np.full((64, 64), 20.0)
    # 24×24 的亮块：横向偏右、纵向居中（>2% 才够亮区下限，否则会被判成"没打亮"）
    gray[20:44, 36:60] = 220.0
    err = got.hook.evaluate(gray)
    assert err.found is True
    assert err.dx_px > 0, "亮块在右半边 ⇒ 目标偏画面右"
    assert err.dy_px == pytest.approx(0.0, abs=1.0)


# ---------- trim 落盘 ----------


def test_apply_trim_updates_stations_and_writes_yaml(tmp_path):
    path = tmp_path / "stations.yaml"
    stations = [Station(id="S101", box_id="B101", y=100.0, z=-20.0, layer=1, col=1),
                Station(id="S102", box_id="B102", y=200.0, z=-20.0, layer=1, col=2)]
    logs: list[str] = []
    apply = make_apply_trim(stations, str(path), log=logs.append)

    apply({"S101": (8.5, -3.25)})

    assert stations[0].trim_y == 8.5 and stations[0].trim_z == -3.25
    assert "framing" in stations[0].trim_note
    assert stations[1].trim_y == 0.0, "没学到的站位不动"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "trim_y: 8.5" in text
    assert any("1 个站位" in m for m in logs)


def test_applied_trim_survives_a_reload(tmp_path):
    from patrol.stations import load_stations

    path = tmp_path / "stations.yaml"
    stations = [Station(id="S101", box_id="B101", y=100.0, z=-20.0, layer=1, col=1)]
    make_apply_trim(stations, str(path), log=lambda _m: None)({"S101": (5.0, 2.0)})

    again = load_stations(str(path))[0]
    assert (again.trim_y, again.trim_z) == (5.0, 2.0)
    assert again.target == (105.0, -18.0), "实际目标 = 坐标 + trim"

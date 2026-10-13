"""测量接线的部署侧（票 12）：判定顺序、fail-closed、默认关。

这里测的是**接线**（能不能开、缺什么就报什么、开了之后真的产出 measurement 行）；
产出口径本身在 `measure/tests/` 里（75 项）。
"""

from types import SimpleNamespace

import numpy as np
import pytest
from deploy.measure_wire import MeasureWire, build, room_id_is_usable
from deploy.m1 import build_parser


def args(**kw) -> SimpleNamespace:
    base = dict(measure="off", mm_per_px=None, measure_blur_threshold=None,
                measure_emit_empty=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _gray(value: float = 200.0, size: int = 64) -> np.ndarray:
    return np.full((size, size), value, dtype=float)


def _frame() -> dict:
    return {"ts": "2026-10-13T10:00:00", "box_id": "B101", "station_id": "S101",
            "angle_profile": "level0", "cloud_url": "http://minio/mogu/x.jpg"}


def test_default_is_off():
    """默认关：这是改生产行为，必须现场显式打开（票 12）。"""
    w = build(args())
    assert isinstance(w, MeasureWire) and w.enabled is False
    assert "关闭" in w.reason


def test_missing_mm_per_px_refuses_with_the_reason():
    """缺标定换算 ⇒ 拒绝开启，且**日志里说清缺的是什么**（不静默降级）。"""
    w = build(args(measure="on"), room_id="611")
    assert w.enabled is False
    assert "mm-per-px" in w.reason


@pytest.mark.parametrize("room", ["", "   ", "unset", "UNSET"])
def test_missing_or_placeholder_room_id_refuses(room):
    """`unset`/空是"没配置"，不是库房号：拿它当幂等键等于给数据贴假房间。"""
    w = build(args(measure="on", mm_per_px=0.25), room_id=room)
    assert w.enabled is False and "room_id" in w.reason
    assert room_id_is_usable(room) is False


def test_missing_decoder_refuses_by_name():
    """缺 numpy/Pillow ⇒ 明确拒绝（报出缺的是哪个模块），本次仍只巡检采图。"""
    def decode(_data: bytes):
        raise ImportError("No module named 'PIL'")

    w = build(args(measure="on", mm_per_px=0.25), room_id="611", decode=decode,
              fetch=lambda _url: b"jpeg")
    assert w.enabled is False and "PIL" in w.reason


def test_enabled_wire_produces_measurement_rows():
    """开启后：装配出的是 `measure_fn`，行带 kind/room_id（与线格式同源）。"""
    w = build(args(measure="on", mm_per_px=0.25, measure_emit_empty=True),
              room_id="611", fetch=lambda _url: b"jpeg", decode=lambda _b: _gray())
    assert w.enabled is True and "611" in w.reason and "0.2500 mm/px" in w.reason

    rows = w.fn(SimpleNamespace(results=[_frame()]))
    assert rows and rows[0]["kind"] == "measurement"
    assert rows[0]["room_id"] == "611" and rows[0]["box_id"] == "B101"


def test_blur_threshold_knob_reaches_the_quality_gate():
    """`--measure-blur-threshold` 真的进了质量门：同一个平坦帧，门高判模糊、门 0 通过。"""
    report = SimpleNamespace(results=[_frame()])

    strict = build(args(measure="on", mm_per_px=0.25, measure_emit_empty=True,
                        measure_blur_threshold=1e9),
                   room_id="611", fetch=lambda _url: b"jpeg", decode=lambda _b: _gray())
    assert "blurry" in strict.fn(report)[0]["quality"]

    loose = build(args(measure="on", mm_per_px=0.25, measure_emit_empty=True,
                       measure_blur_threshold=0.0),
                  room_id="611", fetch=lambda _url: b"jpeg", decode=lambda _b: _gray())
    assert "blurry" not in loose.fn(report)[0]["quality"]


def test_real_decoder_path_with_a_real_jpeg():
    """默认解码器（`framing_wire._imread`）也走得通：给真 JPEG 字节，产出 measurement 行。

    这条钉的是"接线用的是同一条取图/解码路"，而不是测试里塞的假数组。
    """
    Image = pytest.importorskip("PIL.Image")
    from io import BytesIO

    buf = BytesIO()
    Image.new("L", (64, 64), 200).save(buf, format="JPEG")

    w = build(args(measure="on", mm_per_px=0.25, measure_emit_empty=True),
              room_id="611", fetch=lambda _url: buf.getvalue())
    assert w.enabled is True
    rows = w.fn(SimpleNamespace(results=[_frame()]))
    assert rows and rows[0]["kind"] == "measurement" and rows[0]["room_id"] == "611"


def test_blur_threshold_defaults_to_the_calibrated_one_in_measure_quality():
    """不给 `--measure-blur-threshold` ⇒ **不覆盖**：走 `measure.quality` 的标定默认。

    `deploy` 侧曾自带常数 100.0（未标定时的临时值）；2026-10-13 用 218 张线上 ok 帧标定
    出中位 71.3、下限 45.0（误杀 0/218），旧常数会拒 78.4% 的正常帧。
    """
    from measure.quality import DEFAULT_ABS_LAPVAR_FLOOR, frame_quality

    w = build(args(measure="on", mm_per_px=0.25, measure_emit_empty=True),
              room_id="611", fetch=lambda _url: b"jpeg", decode=lambda _b: _gray())
    assert w.enabled is True
    # 日志打的是**实际生效值**（从 measure.quality 取，不在 deploy 侧再抄一个数）
    assert f"{float(DEFAULT_ABS_LAPVAR_FLOOR):g}" in w.reason
    assert "标定默认" in w.reason

    # 显式给了就按给的走（旋钮仍在，只是默认不再是那个旧常数）
    strict = build(args(measure="on", mm_per_px=0.25, measure_emit_empty=True,
                        measure_blur_threshold=1e9),
                   room_id="611", fetch=lambda _url: b"jpeg", decode=lambda _b: _gray())
    assert "1e+09" in strict.reason or "1000000000" in strict.reason


def test_cli_defaults_and_flags_exist():
    """命令行默认 off + **模糊门默认不覆盖标定值**；三个旋钮都能被 argparse 接住。"""
    ap = build_parser()
    parsed = ap.parse_args([])
    assert parsed.measure == "off" and parsed.measure_blur_threshold is None
    got = ap.parse_args(["--measure", "on", "--mm-per-px", "0.25",
                         "--measure-blur-threshold", "70.8", "--measure-emit-empty"])
    assert got.measure == "on" and got.mm_per_px == 0.25
    assert got.measure_blur_threshold == 70.8 and got.measure_emit_empty is True

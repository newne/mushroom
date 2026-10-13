"""`patrol.framing`：图像驱动的拍照位微调闭环。

这一块会**动机构**，所以测试的重点不是"能不能居中"，而是"什么情况下它选择不动"：
找不到目标、置信度低、越挪越偏、贴到限位、超过累计上限——每一条都要有对应用例。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from patrol.framing import (
    DEFAULT_MAX_TRIM_MM,
    FramingError,
    FramingRecipe,
    clamp_trim,
    fine_tune,
    gated_evaluate,
)

LIMITS = (0.0, 4492.0, -212.0, 0.0)
NOMINAL = (187.17, -21.2)


def recipe(**kw) -> FramingRecipe:
    base = {"mm_per_px_y": 0.5, "mm_per_px_z": 0.5, "tol_px": 10.0, "max_taps": 3}
    base.update(kw)
    return FramingRecipe(**base)


class Rig:
    """假的"移动 + 拍照 + 评估"三件套，记录每一次动作。"""

    def __init__(self, offsets, *, confidence=1.0, found=True):
        # offsets: 按 tap 顺序给出的偏移；不足时沿用最后一个
        self.offsets = list(offsets)
        self.confidence = confidence
        self.found = found
        self.moves: list[tuple[float, float]] = []
        self.shots: list[tuple[float, float]] = []
        self.pos = NOMINAL
        self.n = 0

    def move(self, y, z):
        self.moves.append((y, z))
        self.pos = (y, z)

    def shoot(self, tap):
        self.shots.append(self.pos)
        self.n += 1
        return b"jpeg", {"object_name": f"probe{tap}", "pos": self.pos}

    def evaluate(self, pixels):
        idx = min(self.n - 1, len(self.offsets) - 1)
        dx, dy = self.offsets[idx]
        return FramingError(dx_px=dx, dy_px=dy, found=self.found, confidence=self.confidence)


def run(rig, r: FramingRecipe | None = None, nominal=NOMINAL, limits=LIMITS, logs=None):
    return fine_tune(
        nominal=nominal,
        recipe=r or recipe(),
        limits=limits,
        move=rig.move,
        shoot=rig.shoot,
        evaluate=rig.evaluate,
        log=(logs.append if logs is not None else (lambda _m: None)),
    )


# ---------- 基本收敛 ----------


def test_already_centered_takes_one_shot_and_does_not_move():
    rig = Rig([(5.0, -3.0)])
    out = run(rig, recipe(tol_px=10.0))
    assert out.converged is True
    assert out.taps == 0
    assert rig.moves == [], "已经居中就不该有任何移动"
    assert len(rig.shots) == 1
    assert out.trim == (0.0, 0.0)
    assert out.reverted is True


def test_converges_in_one_tap_and_reports_trim():
    rig = Rig([(40.0, -20.0), (0.0, 0.0)])
    out = run(rig, recipe(tol_px=10.0))
    # 40px × 0.5mm/px = +20mm（夹到 max_step 15）；-20px × 0.5 × (−1) = +10mm
    assert out.converged is True
    assert out.taps == 1
    assert rig.moves == [(NOMINAL[0] + 15.0, NOMINAL[1] + 10.0)]
    assert out.chosen_pos == rig.moves[0]
    assert out.trim == (15.0, 10.0)
    assert out.chosen == {"object_name": "probe1", "pos": rig.moves[0]}, "交出去的是好位置那张"


def test_z_sign_default_moves_camera_down_when_target_is_low():
    """目标偏画面下方 ⇒ 相机往下走，把目标拉回中间。

    本机 `+Z` **向上**（接线定死），所以"往下"是 Z 的 **−** 方向：默认 `sign_z` 必须从
    机器约定派生（-1）。原点在顶端只是把数值整体平移，不改"哪边是正"。
    """
    rig = Rig([(0.0, 20.0), (0.0, 0.0)])
    run(rig, recipe(tol_px=10.0))
    assert rig.moves[0][1] < NOMINAL[1], "Z 增大 = 向上；目标偏下要往 − 方向挪"
    assert rig.moves[0][1] == pytest.approx(NOMINAL[1] - 10.0)


def test_default_sign_z_follows_the_machine_convention():
    """把"符号"与"Z 往哪边增长"绑在一起：改坐标框架时不会再漏改这一处。"""
    from patrol.framing import FramingRecipe, default_sign_z

    assert default_sign_z() == -1.0
    assert FramingRecipe(mm_per_px_y=1.0, mm_per_px_z=1.0).sign_z == -1.0


def test_sign_flip_reverses_the_step():
    """sign_y=-1 就是"相机反装"的约定；配错方向靠回退兜住，但配置本身要生效。"""
    rig = Rig([(40.0, 0.0), (0.0, 0.0)])
    run(rig, recipe(sign_y=-1.0))
    assert rig.moves[0][0] == pytest.approx(NOMINAL[0] - 15.0)


# ---------- 不动的情况（安全约束） ----------


def test_target_not_found_never_moves():
    rig = Rig([(99.0, 99.0)], found=False)
    out = run(rig)
    assert rig.moves == [], "没找到目标就一步都不许挪"
    assert out.taps == 0
    assert out.trim == (0.0, 0.0)
    assert "没找到目标" in out.reason


def test_low_confidence_never_moves():
    rig = Rig([(99.0, 99.0)], confidence=0.2)
    out = run(rig)
    assert rig.moves == []
    assert "置信度" in out.reason


def test_no_improvement_reverts_to_best_position():
    """方向配错/挪了没用：偏移不下降 ⇒ 退回最好位置，trim 归零。

    这是"越挪越偏"的唯一防线——所以必须有一个专门的用例。
    """
    rig = Rig([(100.0, 0.0), (100.0, 0.0), (100.0, 0.0)])
    logs: list[str] = []
    out = run(rig, recipe(max_taps=3), logs=logs)
    assert out.converged is False
    assert out.chosen_pos == NOMINAL
    assert out.trim == (0.0, 0.0)
    assert rig.moves[-1] == NOMINAL, "最后一次动作必须是退回基准"
    assert any("回退" in m for m in logs)


def test_divergence_reverts_to_the_best_seen_not_the_first():
    rig = Rig([(100.0, 0.0), (30.0, 0.0), (60.0, 0.0), (60.0, 0.0)])
    out = run(rig, recipe(max_taps=3))
    # tap0=100(基准) tap1=30(好) tap2=60(变差) ⇒ 留在 tap1 那个位置
    assert out.chosen_pos == rig.shots[1]
    assert out.trim != (0.0, 0.0)
    assert out.converged is False


def test_already_at_travel_limit_reports_it_instead_of_pretending():
    """已经贴在限位上：一步都挪不动，理由必须说"限位"，不能含糊成"改善不足"。"""
    rig = Rig([(100.0, 0.0)])
    out = run(rig, nominal=(4492.0, -21.2), r=recipe(max_taps=3))
    assert out.converged is False
    assert "限位" in out.reason
    assert rig.moves == []


def test_never_commands_outside_travel_limits():
    """贴着边界时单步会被截短——截短可以，"发一个越界目标"不可以。"""
    rig = Rig([(100.0, 0.0), (100.0, 0.0)])
    run(rig, nominal=(4490.0, -21.2), r=recipe(max_taps=3))
    assert rig.moves, "还有 2mm 余量时应该真的挪过去"
    assert all(0.0 <= y <= 4492.0 and -212.0 <= z <= 0.0 for y, z in rig.moves)


def test_respects_max_total_mm():
    # 每步只改善 15%：能通过 min_gain，但要走很多步 ⇒ 累计上限先拦下来
    rig = Rig([(100.0, 0.0), (85.0, 0.0), (70.0, 0.0), (55.0, 0.0)])
    out = run(rig, recipe(max_step_mm=15.0, max_total_mm=20.0, max_taps=4))
    assert len(rig.moves) <= 2, "累计位移到上限就该停"
    assert "上限" in out.reason


def test_never_exceeds_max_taps():
    rig = Rig([(100.0, 0.0), (90.0, 0.0), (80.0, 0.0), (70.0, 0.0), (60.0, 0.0)])
    run(rig, recipe(max_taps=2, min_gain=0.0))
    assert len(rig.shots) == 3, "max_taps=2 ⇒ 最多拍 3 张（含基准那张）"


def test_progress_requires_min_gain():
    """只进步一点点也算"没进步"——否则会在检测噪声里一路挪下去。"""
    rig = Rig([(100.0, 0.0), (95.0, 0.0), (90.0, 0.0)])
    out = run(rig, recipe(min_gain=0.15, max_taps=3))
    assert out.converged is False
    assert out.chosen_pos == NOMINAL, "5% 的改善不足以继续，退回基准"


# ---------- 换算与边界 ----------


def test_error_scaled_uses_per_axis_scale():
    err = FramingError(dx_px=3.0, dy_px=4.0)
    assert err.scaled(1.0, 2.0) == pytest.approx(math.hypot(3.0, 8.0))
    assert err.scaled(2.0, 2.0) == pytest.approx(10.0)


def test_clamp_trim_caps_runaway_learning():
    assert clamp_trim((100.0, -100.0)) == (DEFAULT_MAX_TRIM_MM, -DEFAULT_MAX_TRIM_MM)
    assert clamp_trim((3.0, -4.0)) == (3.0, -4.0)


@pytest.mark.parametrize("bad", [
    {"mm_per_px_y": 0.0},
    {"mm_per_px_z": -1.0},
    {"sign_y": 0.5},
    {"sign_z": 2.0},
    {"max_taps": -1},
])
def test_recipe_rejects_nonsense(bad):
    with pytest.raises(ValueError):
        FramingRecipe(**{"mm_per_px_y": 0.5, "mm_per_px_z": 0.5, **bad})


def test_outcome_row_is_serialisable_for_the_index():
    rig = Rig([(40.0, -20.0), (0.0, 0.0)])
    row = run(rig).to_row()
    assert row["converged"] is True
    assert row["trim"] == [15.0, 10.0]
    assert [h["tap"] for h in row["history"]] == [0, 1]
    assert row["history"][0]["dx_px"] == 40.0
    assert row["history"][0]["step_mm"] == [15.0, 10.0]


# ---------- 可用帧判定：只测"缝"，不测尺度 ----------
#
# 阈值归 `measure.quality`（Chief 2026-10-13 裁定的唯一尺度，票 13 用 218 张线上真机帧
# 定标）。本文件只测 `patrol.framing` 自己的那部分：坏帧 ⇒ 检测器报"没找到目标"，
# 且**不再喂给检测器**。尺度的单测在 measure 侧，不在这里重复造。


def _assess(result):
    """造一个 assess：返回给定的 (ok, flags)，并记录被问过几次。"""
    calls = []

    def assess(pixels):
        calls.append(pixels)
        return result

    return assess, calls


def test_bad_frame_becomes_not_found_and_never_reaches_the_detector():
    """坏帧 ⇒ `found=False`，且不喂检测器（省一次解码，也避免雾帧算出好看质心）。"""
    inner_calls = []
    assess, assess_calls = _assess((False, "no_light"))

    def inner(pixels):
        inner_calls.append(pixels)
        return FramingError(dx_px=5.0, dy_px=5.0)

    got = gated_evaluate(inner, assess)(b"jpeg")
    assert got.found is False and got.confidence == 0.0
    assert "no_light" in got.reason
    assert len(assess_calls) == 1 and inner_calls == []


def test_good_frame_reaches_the_detector_unchanged():
    assess, _ = _assess((True, ""))

    def inner(pixels):
        return FramingError(dx_px=5.0, dy_px=5.0)

    got = gated_evaluate(inner, assess)(b"jpeg")
    assert got.found is True and got.dx_px == 5.0


def test_missing_flags_still_say_something_useful():
    """assess 只说"不可用"不给标记时，原因里不许出现空括号。"""
    assess, _ = _assess((False, ""))
    got = gated_evaluate(lambda _p: FramingError(), assess)(b"jpeg")
    assert got.found is False and "未报标记" in got.reason


def test_flags_from_the_single_scale_are_passed_through_verbatim():
    """标记串原样透传——本模块不解释语义，现场要看到的就是测量师那串名字。"""
    assess, _ = _assess((False, "blurry+no_light"))
    got = gated_evaluate(lambda _p: FramingError(), assess)(b"jpeg")
    assert "blurry+no_light" in got.reason


# ---------- 与唯一尺度（measure.quality）的对接：真实帧必须全部通过 ----------
#
# `deploy.framing_wire` 用 `measure.quality.frame_quality` 造 assess。这里用仓库内
# 已归档的真实相机帧做一次跨包对接检查：**一张都不该被拒**（误杀会比漏杀更贵——
# 它会让微调永远学不到东西，而现场只会看到"没挪"）。

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_FRAMES = [
    REPO_ROOT / "docs/patrol/prod-deploy/lampcheck/lampcheck_off.jpg",
    REPO_ROOT / "docs/patrol/prod-deploy/lampcheck/lampcheck_on.jpg",
    REPO_ROOT / "data/S306-candidate-Y2058.833-Z117.5-20261008.jpg",
]
# ⚠️ `m0_S101_20260913_004820.jpg` **不在**上面的列表里，原因是实测的：它的
# `lapvar = 44`，低于票 13 标定的清晰度绝对下限 45.0 ⇒ 被唯一尺度判 `blurry`。
# 那不是误杀——它是 2026-09-13 的帧（标定人群 218 张之前：5 层、旧锚点、另一批光照），
# 本就不在标定覆盖范围内。这条边界记在票 01 的 Comments；要动它找测量师，别在这儿改。


@pytest.mark.parametrize("path", REAL_FRAMES, ids=lambda p: p.name)
def test_real_committed_frames_pass_the_single_scale(path):
    pytest.importorskip("PIL")
    pytest.importorskip("measure.quality")
    if not path.exists():
        pytest.skip(f"仓库里没有 {path.name}")
    from io import BytesIO

    from PIL import Image

    with Image.open(path) as im:
        gray = np_asarray(im)
    from measure.quality import frame_quality

    ok, flags = frame_quality(gray)
    assert ok, f"{path.name} 被唯一尺度拒了：{flags}"


def np_asarray(im):
    """PIL → 0–255 灰度 numpy 数组（`measure.quality` 的刻度约定）。"""
    np = pytest.importorskip("numpy")
    return np.asarray(im.convert("L"), dtype=float)

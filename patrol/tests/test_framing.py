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
    FLAG_OVEREXPOSED,
    FLAG_UNDEREXPOSED,
    FrameQuality,
    FramingError,
    FramingRecipe,
    clamp_trim,
    fine_tune,
    gated_evaluate,
    judge_frame,
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


# ---------- 可用帧判定（imaging-quality 票 01 第 2 项：加湿期坏帧） ----------
#
# 阈值不是拍脑袋：全部从已存历史帧的人群统计推出来，判据是"阈值落在正常帧与坏帧之间
# 的空档里"。人群与复算方式见 patrol.framing.judge_frame 的 docstring。


def _np():
    numpy = pytest.importorskip("numpy")
    return numpy


def _flat(value: float, size=(120, 160)) -> "object":
    """一张确定性灰度图（无随机：同一目标两次调用必须逐像素相同）。"""
    np = _np()
    return np.full(size, float(value))


def _with_block(value: float, *, frac: float, size=(1000, 100)) -> "object":
    """底图 + 一块压死的像素，占比精确到 0.01%。"""
    np = _np()
    g = np.full(size, float(value))
    n = int(round(frac * size[0] * size[1]))
    g.flat[:n] = 5.0                     # ≤16 ⇒ 近黑
    return g


def test_healthy_frame_passes_and_carries_its_fingerprint():
    """正常帧必须通过，并且把统计指纹带回来。

    指纹不是装饰：票据 02 的"同站位相对判据"要逐帧的 mean/std/p99，在那儿重算等于
    把 2880×1616 再解码一次。
    """
    np = _np()
    g = np.clip(np.linspace(40, 220, num=120 * 160).reshape(120, 160), 0, 255)
    q = judge_frame(g)
    assert q.ok is True and q.flags == () and q.flag_string == ""
    assert 100 < q.mean < 160 and q.p99 > 200 and q.near_black == 0.0


def test_near_black_frame_is_flagged_underexposed():
    """近黑帧：≥1.5% 的像素压死 ⇒ underexposed。

    对齐实测：正常巡检帧最高 0.476%，10-12 09:27 那轮最低 2.781%。
    """
    q = judge_frame(_with_block(200.0, frac=0.03))
    assert q.ok is False
    assert q.flags == (FLAG_UNDEREXPOSED,)
    assert q.near_black == pytest.approx(0.03)


def test_threshold_sits_inside_the_population_gap():
    """阈值必须落在空档里——这是"可复算"的判据，不是选出来的数。

    空档：正常帧 ≤0.476% / 坏帧 ≥2.781%（5.8 倍）。取 1.5% 而非几何中点 1.15%，
    是为了给"略微压黑"留余量；这条用例把边界两侧都钉住。
    """
    assert judge_frame(_with_block(200.0, frac=0.004)).ok is True     # 0.4%：正常人群上限内
    assert judge_frame(_with_block(200.0, frac=0.016)).ok is False    # 1.6%：已进坏帧区间


def test_dim_frame_without_bright_content_is_flagged():
    """整帧摸不到亮内容（p99 < 150）⇒ 同样算 underexposed。

    证据是单帧 + 物理理由：正常人群 p99 = 183–247，唯一的暗帧 p99 = 99；红外 +
    补光灯窗内曝光正确的帧必然有亮内容，摸不到亮内容与场景明暗无关。
    """
    np = _np()
    g = np.full((120, 160), 60.0)
    g[:, ::2] = 95.0                       # 峰值 95 < 150，且没有近黑像素
    q = judge_frame(g)
    assert q.near_black == 0.0, "这条只该由 p99 触发，不能被近黑规则抢先"
    assert q.ok is False and q.flags == (FLAG_UNDEREXPOSED,)
    assert q.p99 < 150.0


def test_overexposed_uses_the_measurers_scale():
    """过曝阈值**原样沿用测量师**（≥250 且占比 >5%），不另立尺度。"""
    np = _np()
    g = np.full((1000, 100), 120.0)
    g.flat[:6000] = 255.0                  # 6% > 5%
    q = judge_frame(g)
    assert q.ok is False and q.flags == (FLAG_OVEREXPOSED,)
    assert q.overexposed == pytest.approx(0.06)


def test_both_flags_can_fire_together():
    np = _np()
    g = np.full((1000, 100), 120.0)
    g.flat[:6000] = 255.0                  # 6% 过曝
    g.flat[6000:9000] = 5.0                # 3% 近黑
    q = judge_frame(g)
    assert q.flags == (FLAG_UNDEREXPOSED, FLAG_OVEREXPOSED)   # 顺序固定
    assert q.flag_string == "underexposed,overexposed"


def test_flags_are_comma_joined_like_the_measurers():
    """与 `measure.quality.frame_quality` 同形（逗号分隔）——下游要拼
    `measurements.quality` 枚举串时再按 `measure.contract.JOINER`（`+`）接。"""
    q = FrameQuality(ok=False, flags=(FLAG_UNDEREXPOSED,))
    assert q.flag_string == "underexposed"
    assert FrameQuality(ok=True).flag_string == ""


def test_judge_frame_rejects_non_gray():
    np = _np()
    with pytest.raises(ValueError, match="2D 灰度图"):
        judge_frame(np.zeros((4, 4, 3)))


# ---------- 包在检测器外面：坏帧不驱动微调 ----------


def test_gated_evaluate_turns_a_bad_frame_into_not_found():
    """坏帧 ⇒ 检测器报"没找到目标" ⇒ 微调不挪。

    走这条既有路径是有意的：帧照旧交差、不丢图，只是不拿它当定位依据——加湿期的雾
    会造出假的亮区质心，追着它挪只会把 trim 学歪。
    """
    bad = FrameQuality(ok=False, flags=(FLAG_UNDEREXPOSED,),
                       mean=70.0, std=50.0, p99=200.0)
    inner_calls = []

    def inner(pixels):
        inner_calls.append(pixels)
        return FramingError(dx_px=5.0, dy_px=5.0)

    got = gated_evaluate(inner, lambda _pixels: bad)(b"jpeg")
    assert got.found is False and got.confidence == 0.0
    assert "underexposed" in got.reason and "70.0" in got.reason
    assert inner_calls == [], "坏帧不该再喂给检测器（省一次解码）"


def test_gated_evaluate_passes_good_frames_through():
    def inner(pixels):
        return FramingError(dx_px=5.0, dy_px=5.0)

    got = gated_evaluate(inner, lambda _pixels: FrameQuality(ok=True))(b"jpeg")
    assert got.found is True and got.dx_px == 5.0


# ---------- 真实帧：仓库里已归档的相机帧必须全部通过（不许误杀） ----------
#
# 人群证据在 judge_frame 的 docstring：正常巡检帧 ≤16 占比 0.000–0.476%、p99 183–247。
# 这里用**仓库内已归档**的真实相机帧做回归——它们一张都不该被判坏。

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_FRAMES = [
    REPO_ROOT / "docs/patrol/prod-deploy/lampcheck/lampcheck_off.jpg",
    REPO_ROOT / "docs/patrol/prod-deploy/lampcheck/lampcheck_on.jpg",
    REPO_ROOT / "docs/patrol/prod-deploy/captures/m0_S101_20260913_004820.jpg",
    REPO_ROOT / "data/S306-candidate-Y2058.833-Z117.5-20261008.jpg",
]


@pytest.mark.parametrize("path", REAL_FRAMES, ids=lambda p: p.name)
def test_real_committed_frames_pass_the_gate(path):
    """已归档真实帧必须通过；缺文件/缺 Pillow 时跳过而不是失败。"""
    pytest.importorskip("PIL")
    if not path.exists():
        pytest.skip(f"仓库里没有 {path.name}")
    from io import BytesIO

    from PIL import Image

    with Image.open(path) as im:
        gray = _to_gray(im)
    q = judge_frame(gray)
    assert q.ok, f"{path.name} 被误判：{q.flag_string}（均值 {q.mean:.1f} p99 {q.p99:.0f}）"


def _to_gray(im):
    """PIL → 灰度 numpy 数组。"""
    return _np().asarray(im.convert("L"), dtype=float)

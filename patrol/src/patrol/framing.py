"""拍照位微调：用**拍到的图像**把相机挪到真正好的位置。

## 为什么需要它（这一步在"移动到站位"之后，不是替代它）

`S{层}{框:02d}` 的坐标由 `patrol.stations.build_grid` 生成（12 列按 Y 间距布置，4 层 Z 层心
逐层标定，C08 带固定 Y 偏移）。框位装配误差和相机光轴偏置仍可能使目标偏离画面中心：

* 货架是焊出来的，框距有装配误差；
* 相机光轴与滑块坐标之间本来就有一个固定偏置（当前支架装在**平视**位 `level0`；
  改造前那一版是斜拍 45° `top45`，见 `stations.GRID_ANGLES` 的档位说明）；
* 层高不等分时，Z 的推导值会系统性偏。

所以"移动到推导坐标"只是**把目标送进视野**（第一步），"目标真的落在画面中央"要靠
图像说话（第二步）。本模块就是这第二步的闭环：拍 → 量偏移 → 挪 → 再拍，直到居中或
判定挪不动。

## 安全约束（这块会动机构，按最坏情况设计）

1. **永远以推导坐标为基准**。`trim` 是**叠加在**站位坐标上的小量，上限 `max_trim_mm`；
   学歪了最多偏这么一点，不会把站点拖到隔壁框去。
2. **只挪不追**：每一步的位移都夹在 `max_step_mm` 内、总位移夹在 `max_total_mm` 内、
   目标坐标再被行程限位夹一次。三重夹紧，任何一步都不可能是"横穿导轨"。
3. **不进步就回退**。以"偏移的毫米当量"为目标函数，新位置没比历史最好位置好够
   `min_gain`（默认 15%）就退回**最好**的那个位置，而不是继续朝一个可能方向错了的
   方向挪下去。方向反了（`sign_*` 配错）的表现正是"越挪越偏"，这一条直接兜住。
4. **找不到目标就原地不动**。检测器说没找到、或置信度低于阈值 ⇒ 一步都不挪，
   用基准位置那张图交差。宁可交一张"没居中但真实"的图，也不要基于瞎猜的偏移去挪。
5. 每个候选位置都会真的拍一张（否则无从判断），所以最好位置的那张图**就是**最终
   交出去的那张——不重复拍，不浪费 5.2 秒。

## trim 的复用

一次闭环得到的 `trim` 可以写回站位表（`Station.trim_y/trim_z`），下一轮**直接从这里
起步**，于是常规轮次只拍一张、零额外耗时；`--framing always` 才每轮都闭环。这就是
"微调"与"每轮都试"的区别：现场 48 站，每站多拍一张就是每轮多约 4 分钟。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from patrol.motion_profile import M1, MotionProfile

if TYPE_CHECKING:                     # 只为注解存在：patrol 不依赖 numpy（见 judge_frame）
    import numpy as np


def default_sign_z(profile: MotionProfile = M1) -> float:
    """图像 y 正方向（向下）对应 Z 的哪个方向——由机器约定**派生**，不写死。

    相机朝 Y 正方向看、图像 x 向右、图像 y 向下，所以：

    * 目标偏画面下方 ⇒ 相机得往**下**挪；
    * "往下"是 Z 的 + 方向还是 − 方向，取决于 Z 的坐标系怎么摆——本机 `+Z` 向上
      （`positive_towards="up"`），所以"往下"是 **−** 方向 ⇒ **−1**。原点在顶端只是把
      Z 的数值整体平移，不改"哪边是正"。

    这个符号是**最容易配错**的一项：配错的表现是"越挪越偏"。它现在从
    `AxisProfile.positive_towards` 推出来，改坐标框架时不会再漏改这里。
    """
    return 1.0 if profile.z.positive_towards == "down" else -1.0

# 目标在画面里的偏移（像素）→ mm 的换算与边界。
DEFAULT_TOL_PX = 24.0          # 画面 1/20 左右；再小就是在追检测噪声
DEFAULT_MAX_STEP_MM = 15.0     # 单步上限：够跨过装配误差，又不至于跨框
DEFAULT_MAX_TOTAL_MM = 40.0    # 累计上限：半格（框距 374mm 的 1/9）
DEFAULT_MAX_TRIM_MM = 20.0     # 写回站位表的 trim 上限
DEFAULT_MAX_TAPS = 2           # 60 站 × 2 张 ≈ 每轮多 10 分钟，上限必须小
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_MIN_GAIN = 0.15        # 新位置至少要比历史最好位置好 15%，否则算"没进步"


# ---------- 可用帧判定（imaging-quality 票 01 第 2 项：加湿期坏帧） ----------
#
# 判据只回答一件事：**这一帧能不能用来判断长势**。它不判断"有没有蘑菇"（那是检测器的事），
# 也不做静默丢弃——结果是一串标记，由调用方决定记到哪儿（票据 02 交给图像索引侧）。
#
# ## 为什么只有两个轴
#
# 阈值全部从**已存历史帧**的人群统计推出来（可复算：`/api/images?station_id=…&limit=200`
# 拉帧，逐帧算下面这几个量）。人群 = 20 张巡检帧（10-12/10-13，四个轮次）+ 10 张老系统
# sweep 帧 + 4 张仓库 fixture，全部 2880×1616：
#
# | 统计量 | 正常帧人群 | 坏帧人群 | 可分？ |
# | --- | --- | --- | --- |
# | ≤16 像素占比 | 0.000 – 0.476% | 2.781 – 10.83% | ✅ 5.8 倍空档 |
# | ≥250 像素占比 | 0.000 – 2.580% | （本地无样本，沿用测量师 5%） | — 继承 |
# | p99 | 183 – 247 | 99（sweep_236c） | ✅ 单样本 + 物理理由 |
# | σ（对比度） | 22.5 – 46.1 | 31.2 – 37.0 | ❌ **完全重叠** |
# | p99−p1（幅宽） | 79 – 224 | 128 – 155 | ❌ **完全重叠** |
# | 上下半幅亮度差 | 1.5 – 41.0 | 1.5 – 14.9 | ❌ 场景相关，非雾相关 |
#
# ⇒ **"低对比度"做不了单帧门控**：加湿雾气的签名是"均值 +33%、σ −32%"（同站位跨天实测：
# S107 均值 100.6→125.6、σ 37.4→31.2），但 σ≈30 对这台相机是**正常值**（老系统健康帧
# σ 22.5–46.1），任何绝对阈值都会误杀正常帧。所以雾只能**相对判**（同站位的历史基线），
# 配方见票据 02；预防则靠加湿保持（hold）。
#
# ## 与 hold、补光灯窗的对应关系
#
# * **hold（加湿保持）**是预防：加湿段根本不拍，坏帧不会产生。票据 02 的相对判据是补救：
#   没设 hold 又撞上加湿时，用"同站位统计量漂移"把它认出来。
# * **补光灯窗**是曝光轴的来源：巡检抓拍会开 OUT0，所以帧的曝光由灯 + 相机决定。
#   仓库里早有"补光灯实测无效 / 照不到视野"的记录（station-retarget.md §1）——灯没进
#   画面时，曝光只能靠菇房蓝光，于是随环控漂。近黑帧（≤16 占比高）大多是这条路径的产物。

#: 近黑电平：取 limited-range 的黑电平 16，与采图侧 `capture.LEVELS_LOW` 同一个数
#: （别造第二套尺度）。
NEAR_BLACK_LEVEL = 16
#: 近黑占比上限。证据：正常巡检帧最高 0.476%，坏帧最低 2.781%（10-12 09:27 那轮），
#: 阈值落在 5.8 倍的空档中间；取 1.5% 而不是几何中点 1.15%，是为了给"略微压黑"留余量。
MAX_NEAR_BLACK = 0.015
#: 过曝判据**原样沿用测量师**（`measure.quality.is_overexposed` 的默认参数），
#: 不另立尺度；本地人群最高 2.580%，无一触发，故此项只作对齐不作新建。
OVEREXPOSED_LEVEL = 250
MAX_OVEREXPOSED = 0.05
#: "整帧没有亮内容"：p99 低于它说明连高光都摸不到。正常人群 p99 = 183–247，
#: 唯一的暗帧（sweep_236c，均值 42.6）p99 = 99。物理理由：红外 + 补光灯窗内，
#: 曝光正确的帧必然有亮内容；摸不到亮内容与场景明暗无关，只与曝光有关。
MIN_P99 = 150.0

#: 标记名。与测量师 `measure.quality.frame_quality` 的 `blurry`/`overexposed`
#: 同一层（检测级、逗号分隔）；`underexposed` 与 `overexposed` 成对，同一个曝光轴。
FLAG_UNDEREXPOSED = "underexposed"
FLAG_OVEREXPOSED = "overexposed"


@dataclass(frozen=True)
class FrameQuality:
    """一帧的可用性判定 + 它的统计指纹。

    `flags` 顺序固定（先曝光不足、后过曝），空元组 = 可用。`flag_string` 与
    `measure.quality.frame_quality` 的返回值同形（逗号分隔），下游要拼
    `measurements.quality` 枚举串时再按 `measure.contract.JOINER`（`+`）接。

    统计量一并带回是有意的：票据 02 的"同站位相对判据"需要逐帧的 mean/std/p99，
    在那儿重算一遍等于把 2880×1616 的图再解码一次。
    """

    ok: bool
    flags: tuple[str, ...] = ()
    mean: float = 0.0
    std: float = 0.0
    p99: float = 0.0
    near_black: float = 0.0
    overexposed: float = 0.0

    @property
    def flag_string(self) -> str:
        return ",".join(self.flags)


def judge_frame(gray: np.ndarray) -> FrameQuality:
    """灰度图 → 可用性判定（纯函数；numpy 缺了明确报错，不静默返回"可用"）。

    阈值与理由见上面那张人群表。**故意不做模糊判定**：测量师的
    `measure.quality.frame_quality` 已占那一轴，且其 LapVar<100 的门槛在
    2880×1616 上会把 19/20 张正常巡检帧判成 blurry——那是刻度没按分辨率归一，
    归测量师修（票据 03），不在这里另立一个数。
    """
    try:
        import numpy as np
    except ImportError as e:      # patrol 不依赖 numpy：缺了要说得明白
        raise RuntimeError(
            "可用帧判定需要 numpy（patrol 本体不依赖它，由部署侧环境提供）"
        ) from e

    g = np.asarray(gray, dtype=float)
    if g.ndim != 2 or g.shape[0] < 3 or g.shape[1] < 3:
        raise ValueError("需要 2D 灰度图且尺寸 ≥3×3")

    near_black = float((g <= NEAR_BLACK_LEVEL).mean())
    over = float((g >= OVEREXPOSED_LEVEL).mean())
    p99 = float(np.percentile(g, 99))
    flags: list[str] = []
    if near_black > MAX_NEAR_BLACK or p99 < MIN_P99:
        flags.append(FLAG_UNDEREXPOSED)
    if over > MAX_OVEREXPOSED:
        flags.append(FLAG_OVEREXPOSED)
    return FrameQuality(
        ok=not flags,
        flags=tuple(flags),
        mean=float(g.mean()),
        std=float(g.std()),
        p99=p99,
        near_black=near_black,
        overexposed=over,
    )


def frame_quality_gate(imread: Callable[[bytes], np.ndarray]) -> Callable[[bytes], FrameQuality]:
    """把"字节 → 灰度数组"的解码器包成可用帧判定的字节版（给部署侧接线用）。

    与 `evaluate` 同形：都吃 JPEG 字节。单独一个函数而不是塞进 `judge_frame`，
    是因为 patrol 不依赖 Pillow/numpy——解码必须由部署侧注入（与
    `deploy.framing_wire` 的 `_imread` 同一个来源）。
    """
    def gate(pixels: bytes) -> FrameQuality:
        return judge_frame(imread(pixels))

    return gate


def gated_evaluate(
    evaluate: Callable[[bytes], FramingError],
    gate: Callable[[bytes], FrameQuality],
) -> Callable[[bytes], FramingError]:
    """把可用帧判定包在检测器外面：坏帧 ⇒ ``found=False`` + 原因。

    **为什么走这个缝**：`fine_tune` 的调用点在 `patrol.orchestrator`（不在本票的
    文件边界内），而 `FramingHook.evaluate` 是部署侧造的——包一层就能让坏帧走
    "没找到目标就不挪"这条**既有**路径：帧照旧交差、不丢图，只是不拿它当定位依据
    （加湿期的雾会造出假的亮区质心，追着它挪只会把 trim 学歪）。

    代价是**多解一次码**（2880×1616 约 0.3–0.5 s/帧）。微调本身只在标定/排障时开
    （`--framing`），不是常规轮次的路径，所以不为它去改 orchestrator。
    """
    def evaluate_gated(pixels: bytes) -> FramingError:
        q = gate(pixels)
        if not q.ok:
            return FramingError(
                found=False,
                confidence=0.0,
                reason=(f"本帧不可用（{q.flag_string}）：均值 {q.mean:.1f} "
                        f"σ {q.std:.1f} p99 {q.p99:.0f}"),
            )
        return evaluate(pixels)

    return evaluate_gated


@dataclass(frozen=True)
class FramingError:
    """检测器对一帧的判断：目标中心相对画面中心偏了多少。

    ``dx_px > 0`` = 目标偏画面**右**侧，``dy_px > 0`` = 目标偏画面**下**侧
    （光栅图像的 y 轴向下）。``found=False`` 表示这一帧里根本没找到目标——
    调用方必须据此**不移动**（见模块顶部的安全约束 4）。
    """

    dx_px: float = 0.0
    dy_px: float = 0.0
    found: bool = True
    confidence: float = 1.0
    reason: str = ""

    def scaled(self, mm_per_px_y: float, mm_per_px_z: float) -> float:
        """把偏移折算成毫米当量（两轴尺度不同，不能直接比像素）。"""
        return math.hypot(self.dx_px * mm_per_px_y, self.dy_px * mm_per_px_z)


@dataclass(frozen=True)
class FramingRecipe:
    """微调的换算与边界。标定一次（棋盘格或"挪 10mm 看目标走几像素"），长期复用。

    ``sign_y`` / ``sign_z`` 描述图像方向与轴正方向的对应关系：默认
    "相机朝 Y 正方向看、图像 x 向右、y 向下"，即

    * 目标偏右 ⇒ 相机往 **Y 正**方向挪（把目标拉回中间）⇒ ``sign_y = +1``；
    * 目标偏下 ⇒ 相机往 **Z 负**方向挪（本机 `+Z` 向上，往下是 −）⇒ ``sign_z = −1``。

    ``sign_z`` 的默认值由 `default_sign_z()` 从机器约定推出来（2026-10-10 之后是 −1）。
    这两个符号是**最容易配错**的一项，配错的表现是"越挪越偏"——`fine_tune` 的
    不进步回退会把它挡住，但现场仍应先用单站验证一次方向再放开。
    """

    mm_per_px_y: float
    mm_per_px_z: float
    sign_y: float = 1.0
    sign_z: float = field(default_factory=default_sign_z)
    tol_px: float = DEFAULT_TOL_PX
    max_step_mm: float = DEFAULT_MAX_STEP_MM
    max_total_mm: float = DEFAULT_MAX_TOTAL_MM
    max_taps: int = DEFAULT_MAX_TAPS
    min_confidence: float = DEFAULT_MIN_CONFIDENCE
    min_gain: float = DEFAULT_MIN_GAIN

    def __post_init__(self) -> None:
        for name in ("mm_per_px_y", "mm_per_px_z"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} 必须为正（像素→mm 的换算）")
        for name in ("sign_y", "sign_z"):
            if getattr(self, name) not in (1.0, -1.0):
                raise ValueError(f"{name} 只能是 +1 或 -1")
        if self.max_taps < 0:
            raise ValueError("max_taps 不能为负")


@dataclass
class TapRecord:
    """一次"拍 + 评估"的取证记录（写进图像索引，供事后复盘为什么挪了/没挪）。"""

    tap: int
    pos: tuple[float, float]
    error: FramingError
    step: tuple[float, float] = (0.0, 0.0)

    def to_row(self) -> dict:
        return {
            "tap": self.tap,
            "pos": [round(self.pos[0], 3), round(self.pos[1], 3)],
            "dx_px": round(self.error.dx_px, 2),
            "dy_px": round(self.error.dy_px, 2),
            "found": self.error.found,
            "confidence": round(self.error.confidence, 3),
            "step_mm": [round(self.step[0], 3), round(self.step[1], 3)],
        }


@dataclass
class FramingOutcome:
    """闭环结果。``chosen`` 是**最好位置那张图**的载荷，直接当本站的成果用。"""

    chosen: object | None
    chosen_pos: tuple[float, float]
    nominal: tuple[float, float]
    converged: bool
    taps: int
    reason: str
    history: list[TapRecord] = field(default_factory=list)

    @property
    def trim(self) -> tuple[float, float]:
        """相对基准位置的净位移（写回站位表的候选值）。"""
        return (self.chosen_pos[0] - self.nominal[0], self.chosen_pos[1] - self.nominal[1])

    @property
    def reverted(self) -> bool:
        """是否退回过基准位置（一次都没挪，或挪完又退回来）。"""
        return abs(self.trim[0]) < 1e-9 and abs(self.trim[1]) < 1e-9

    def to_row(self) -> dict:
        return {
            "taps": self.taps,
            "converged": self.converged,
            "trim": [round(self.trim[0], 3), round(self.trim[1], 3)],
            "reason": self.reason,
            "history": [h.to_row() for h in self.history],
        }


def clamp_trim(trim: tuple[float, float], *, limit: float = DEFAULT_MAX_TRIM_MM) -> tuple[float, float]:
    """把 trim 夹在上限内——学歪了最多偏 ``limit`` mm，不会把站点拖到隔壁框。"""
    return (max(-limit, min(limit, trim[0])), max(-limit, min(limit, trim[1])))


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def fine_tune(
    *,
    nominal: tuple[float, float],
    recipe: FramingRecipe,
    limits: tuple[float, float, float, float],
    move: Callable[[float, float], None],
    shoot: Callable[[int], tuple[bytes, object]],
    evaluate: Callable[[bytes], FramingError],
    log: Callable[[str], None] = print,
) -> FramingOutcome:
    """在 ``nominal`` 附近闭环微调，返回**最好位置**及其图像。

    参数都是注入的，本函数不碰硬件也不碰图像库：

    * ``move(y, z)``：移动到绝对坐标（实现方负责到位确认与超时；本函数只保证
      给的坐标在 ``limits`` 内、且每步不超过 ``max_step_mm``）；
    * ``shoot(tap)``：在**当前**位置拍一帧，返回 ``(像素, 载荷)``——载荷对 patrol 不透明，
      原样带回给调用方（那里有 `object_name` / `cloud_url` 等）；
    * ``evaluate(pixels)``：像素 → `FramingError`。真实实现是检测器（`measure` 侧），
      测试里是假的。**可用帧判定也走这个缝**：部署侧把检测器包一层
      （见 `deploy.framing_wire`），坏帧返回 `found=False` + 原因，本循环的
      "没找到目标就不挪"路径原样接住——帧照旧交差，只是不拿它当定位依据。
    * ``limits``：``(y_min, y_max, z_min, z_max)``，机械行程。
    """
    y_lo, y_hi, z_lo, z_hi = limits
    pos = (float(nominal[0]), float(nominal[1]))
    used_mm = 0.0

    history: list[TapRecord] = []
    best: tuple[float, tuple[float, float], object, FramingError] | None = None
    last: tuple[tuple[float, float], object] | None = None
    prev_err_mm: float | None = None
    reason = "达到最大微调步数"
    converged = False

    for tap in range(recipe.max_taps + 1):
        if tap > 0:
            move(*pos)
        pixels, payload = shoot(tap)
        last = (pos, payload)
        err = evaluate(pixels)
        record = TapRecord(tap=tap, pos=pos, error=err)
        history.append(record)

        if not err.found:
            reason = f"这一帧没找到目标（{err.reason or '检测器未报原因'}）——保持基准位置"
            break
        if err.confidence < recipe.min_confidence:
            reason = (f"置信度 {err.confidence:.2f} 低于阈值 {recipe.min_confidence:.2f}"
                      "——保持基准位置")
            break

        err_mm = err.scaled(recipe.mm_per_px_y, recipe.mm_per_px_z)

        if abs(err.dx_px) <= recipe.tol_px and abs(err.dy_px) <= recipe.tol_px:
            # 已经在容差内：**当前位置就是答案**，把它记为最好再收工。
            if best is None or err_mm < best[0]:
                best = (err_mm, pos, payload, err)
            converged = True
            reason = f"已居中（偏移 {err.dx_px:.0f}, {err.dy_px:.0f} px 在 ±{recipe.tol_px:.0f} 内）"
            break

        # 改善不足就别当真：这一步的收益落在检测噪声里，既不该继续挪，也不该把它
        # 当成"新的最好"采纳下来（否则 trim 会随噪声慢慢漂）。基准那张图仍是最佳候选。
        if prev_err_mm is not None:
            gain = (prev_err_mm - err_mm) / prev_err_mm if prev_err_mm > 0 else 0.0
            if gain < recipe.min_gain:
                reason = (f"改善不足（{prev_err_mm:.1f} → {err_mm:.1f} mm，"
                          f"低于 {recipe.min_gain:.0%}）——停止并回退到最好位置")
                break
        prev_err_mm = err_mm

        if best is None or err_mm < best[0]:
            best = (err_mm, pos, payload, err)

        if tap == recipe.max_taps:
            break

        step_y = _clamp(err.dx_px * recipe.mm_per_px_y * recipe.sign_y,
                        -recipe.max_step_mm, recipe.max_step_mm)
        step_z = _clamp(err.dy_px * recipe.mm_per_px_z * recipe.sign_z,
                        -recipe.max_step_mm, recipe.max_step_mm)
        target = (_clamp(pos[0] + step_y, y_lo, y_hi), _clamp(pos[1] + step_z, z_lo, z_hi))
        if target == pos:
            reason = "已经贴到行程限位，挪不动了——保持当前位置"
            break

        moved = math.hypot(target[0] - pos[0], target[1] - pos[1])
        if used_mm + moved > recipe.max_total_mm:
            reason = (f"累计微调将超过 {recipe.max_total_mm:.0f}mm 上限"
                      "——停止，回退到最好位置")
            break

        record.step = (target[0] - pos[0], target[1] - pos[1])
        used_mm += moved
        pos = target

    if best is None:
        # 一帧都没能用于判断（没找到 / 置信度低）：位置不动，**仍把那一帧交出去**——
        # 调用方永远要拿到一张图和它的位置，否则这一站就白跑了。
        chosen_pos, payload = last if last is not None else (pos, None)
    else:
        _, best_pos, payload, _ = best
        if best_pos != pos:
            move(*best_pos)
            log(f"微调回退：{pos} → {best_pos}（后面没有更好，回退到最好位置）")
        chosen_pos = best_pos

    return FramingOutcome(
        chosen=payload,
        chosen_pos=chosen_pos,
        nominal=(float(nominal[0]), float(nominal[1])),
        converged=converged,
        taps=len(history) - 1 if history else 0,
        reason=reason,
        history=history,
    )

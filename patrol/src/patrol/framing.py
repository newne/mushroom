"""拍照位微调：用**拍到的图像**把相机挪到真正好的位置。

## 为什么需要它（这一步在"移动到站位"之后，不是替代它）

`S{层}{框:02d}` 的坐标是从行程**推导**出来的（12 框均分 4492mm、5 层均分 212mm）。
推导的前提是"框位等分、相机光轴正对站位中心"——两个前提现场都不严格成立：

* 货架是焊出来的，框距有装配误差；
* 相机斜拍 45°（`GRID_ANGLES=("top45",)`），光轴与滑块坐标之间本来就有一个固定偏置；
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
"微调"与"每轮都试"的区别：现场 60 站，每站多拍一张就是每轮多 5 分钟。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

# 目标在画面里的偏移（像素）→ mm 的换算与边界。
DEFAULT_TOL_PX = 24.0          # 画面 1/20 左右；再小就是在追检测噪声
DEFAULT_MAX_STEP_MM = 15.0     # 单步上限：够跨过装配误差，又不至于跨框
DEFAULT_MAX_TOTAL_MM = 40.0    # 累计上限：半格（框距 374mm 的 1/9）
DEFAULT_MAX_TRIM_MM = 20.0     # 写回站位表的 trim 上限
DEFAULT_MAX_TAPS = 2           # 60 站 × 2 张 ≈ 每轮多 10 分钟，上限必须小
DEFAULT_MIN_CONFIDENCE = 0.5
DEFAULT_MIN_GAIN = 0.15        # 新位置至少要比历史最好位置好 15%，否则算"没进步"


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
    * 目标偏下 ⇒ 相机往 **Z 负**方向挪（Z 向上为正）⇒ ``sign_z = -1``。

    这两个符号是**最容易配错**的一项，配错的表现是"越挪越偏"——`fine_tune` 的
    不进步回退会把它挡住，但现场仍应先用单站验证一次方向再放开。
    """

    mm_per_px_y: float
    mm_per_px_z: float
    sign_y: float = 1.0
    sign_z: float = -1.0
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
      测试里是假的。
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

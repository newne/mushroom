"""生产接线：一轮巡检的多帧 → `MeasurementRecord` 行（票 05 / 票 11）。

## 边界

本模块**只依赖** `measure.*` 与 numpy；取图（HTTP）与解码（Pillow）都由调用方注入：

- 现场由 `deploy` 侧传 `fetch`/`decode`（复用 `deploy.framing_wire` 已验证的
  MinIO 取字节 + Pillow 解码路径）；
- 测试与离线回放传 stub，不需要网络、不需要 Pillow。

`make_measure_fn()` 返回的对象就是 `PatrolDaemon(measure_fn=…)` 要的东西：吃一个
"本轮报告"（任何带 `results` 列表的对象），吐一串行（每行都带 `kind="measurement"`）。

## 四量在当前角度档（单档平视）下的字段级处理

用户 2026-10-13 裁定：角度档 A = 单档平视（`level0`），顶拍与 45° 侧斜都不做。

| 量 | 列 | 单位 | 当前处理 |
| --- | --- | --- | --- |
| 每天增长量 | 无自己的列 | mm/天 | **派生量**，本模块不落库 |
| 菌簇范围 | **无列** | mm / mm² | 可算：像素级区域度量在 `aggregate_box().mean_cluster_w_mm/…`；落库形态等环境官定（票 11 §1 只有 8+3 列） |
| 菌盖直径 | `mean_cap_mm` | mm | **缺测**：恒 NULL + `no_angle_profile` + `no_detection`，`notes` 写"无对应角度档"，**禁止平视近似** |
| 菌杆长度 | `mean_len_mm` / `p10_len` / `p90_len` | mm | **缺测**：恒 NULL，同上 |

`n` = 本行统计的**有效检出数**（实例数）。实例带 `low_confidence` 时 `n=0`：
`analysis.growth.MIN_N = 2` 会把这种行判为"不可比"，这是刻意的 fail-closed。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

import numpy as np

from measure import contract
from measure.baseline import BaselineConfig, BaselineDetector
from measure.pipeline import BoxStats, Detector, Detection, aggregate_box
from measure.quality import frame_quality
from measure.record import MeasurementRecord

#: 每一行都带的标记：当前全部产出都是未标定的基线值，且都是平视口径
CONST_FLAGS = ("baseline", "level_view")

#: 缺测原因（票 11 §9：`mean_cap_mm`/`mean_len_mm` 单档平视下明确缺测）
UNMEASURABLE_FLAGS = ("no_angle_profile",)
UNMEASURABLE_FLAGS_WITH_EMPTY = ("no_detection", "no_angle_profile")

#: 实例带这些标记 ⇒ 朵数不可信，n 归零
LOW_CONFIDENCE_FLAG = "low_confidence"

DEFAULT_MEASURE_KIND = "measurement"

#: **单轮增量门**（Chief 2026-10-14 改判）：**一轮 48 站的测量增量 ≤180 s**。
#: 历史：旧门是"在线 ≤1.1 s/帧"，那是在 **ROI + ds=2、无袋肩门** 的旧配置下定的数；
#: 218 帧实测中位 2.10 s/帧（48 站 ≈ +101 s/轮），**旧门作废**（票 15 §8.1）。
#: 不做能力裁剪（ds=4/裁带都会动近排质量，而近排是唯一交付的）。
MEASURE_ROUND_GATE_S: float = 180.0
#: 单帧超门阈值 = 轮门 / 48 站 ≈ 3.75 s（只用来**喊**，门以轮为准）。
MEASURE_TIME_GATE_S: float = MEASURE_ROUND_GATE_S / 48.0


def timing_summary(durations: Sequence[float],
                   gate_s: float = MEASURE_TIME_GATE_S,
                   round_gate_s: float = MEASURE_ROUND_GATE_S) -> str:
    """一行"测量耗时"摘要：帧数 / 总耗时 / 均值 / 中位 / 超门帧数 / 轮增量是否超门。

    纯函数，便于单测（不依赖真实计时）。超门帧数 >0 就是性能退化的信号，
    第一嫌疑永远是"有人把 L1/L3 退回全帧 / 没降采样"（票 15 §8.1 C1–C3）。
    """
    ds = [float(d) for d in durations]
    if not ds:
        return "测量：0 帧，无耗时样本"
    ordered = sorted(ds)
    n = len(ordered)
    median = (ordered[n // 2] if n % 2
              else 0.5 * (ordered[n // 2 - 1] + ordered[n // 2]))
    total = sum(ordered)
    over = sum(1 for d in ds if d > gate_s)
    round_verdict = ("轮增量超门" if total > round_gate_s else "轮增量在门内")
    return (f"测量：{n} 帧 共 {total:.2f} s（均 {total / n:.2f} s/帧，"
            f"中位 {median:.2f} s，超门(>{gate_s:.2f} s) {over} 帧）；"
            f"{round_verdict}（{total:.0f} s vs ≤{round_gate_s:.0f} s）")


@dataclass
class FrameInput:
    """一帧的输入（patrol 的 image_index 行里我们关心的那几个字段）。"""

    ts: str
    box_id: str
    cloud_url: str | None = None
    station_id: str | None = None
    angle_profile: str | None = None
    measure_kind: str | None = None
    #: 内容完整性标记（巡检手票 13，提交 `6a232e4`）：同一指纹出现在 ≥2 个不同站位 ⇒
    #: 这一帧不是"它自己那份"。带这个标记的帧**不进测量**（也不进质量统计/标定样本）。
    frame_dup: bool = False
    dup_group: str | None = None
    content_sha256: str | None = None
    #: 轮级标记：`suspect_duplicate`（该轮有跨站重复指纹）/ `content_check_error`
    #:（本轮**没查成**，按 fail-closed 处理：不当作"已确认无重复"）
    suspect_duplicate: bool = False
    content_check_error: bool = False


def frame_from_meta(meta: dict) -> FrameInput | None:
    """image_index 行 → `FrameInput`；缺 ts/box_id 的行返回 None（不是测量对象）。"""
    ts = meta.get("ts")
    box_id = meta.get("box_id")
    if not ts or not box_id:
        return None
    return FrameInput(
        ts=str(ts), box_id=str(box_id),
        cloud_url=meta.get("cloud_url"),
        station_id=meta.get("station_id"),
        angle_profile=meta.get("angle_profile"),
        measure_kind=meta.get("measure_kind"),
        frame_dup=bool(meta.get("frame_dup")),
        dup_group=meta.get("dup_group"),
        content_sha256=meta.get("content_sha256"),
        suspect_duplicate=bool(meta.get("suspect_duplicate")),
        content_check_error=bool(meta.get("content_check_error")),
    )


def content_integrity_reason(frame: FrameInput) -> str:
    """内容完整性问题的原因串（空串 = 没问题）。

    规则（票 13-round-content-integrity）：**跨站重复的帧不得进入测量与统计**。
    单帧 `frame_dup` 与整轮 `suspect_duplicate` 都算——后者是给"轮被整体污染但帧行
    没逐帧标注"的情况兜底。`content_check_error` **不算问题**（它只是"没查成"），
    但要记进日志：没查成的轮不能当成"已确认无重复"来做样本筛选（fail-closed 在
    **样本侧**生效，见 `measure/tools/calibrate_quality_thresholds.py`）。
    """
    if frame.frame_dup:
        return f"frame_dup({frame.dup_group or '?'})"
    if frame.suspect_duplicate:
        return "suspect_duplicate_round"
    return ""


def detection_flags(dets: Sequence[Detection]) -> set[str]:
    """汇总所有检出自带的标记。

    注意**不能**只信 `BoxStats.quality_flags`：`aggregate_box` 只收集
    `quality_ok=False` 的检出（spec §5.2 的口径），而 `low_confidence` 的实例是
    "检出了但不可信"，`quality_ok` 仍是 True。这里直接读检出，避免整框标记丢失。
    """
    return contract.parse_flags("+".join(d.flags for d in dets if d.flags))


def usable_instance_count(stats: BoxStats, *, flags: set[str] | None = None) -> int:
    """可信朵数：本行实例带 `low_confidence` 时记 0（fail-closed 给下游 `MIN_N`）。"""
    all_flags = set(flags or ()) | contract.parse_flags(stats.quality_flags)
    if LOW_CONFIDENCE_FLAG in all_flags:
        return 0
    return stats.n_used


def quality_for(*, flags: Iterable[str], detected: bool) -> str:
    """恒带标记 + 实例标记 + 缺测标记 → 规范 quality 串。"""
    out = set(CONST_FLAGS) | {f for f in flags if f}
    out.update(UNMEASURABLE_FLAGS if detected else UNMEASURABLE_FLAGS_WITH_EMPTY)
    return contract.format_flags(out)


def notes_for(detected: bool) -> str:
    """缺测/口径说明（审计用，不在线格式里）。"""
    base = "菌盖直径、菌杆长度：无对应角度档（单档平视 level0）"
    return base if detected else f"{base}；本帧无有效检出"


def row_from_stats(*, frame: FrameInput, room_id: str, stats: BoxStats,
                   quality: str, notes: str,
                   flags: Iterable[str] = (),
                   estimated_fields: object | None = None,
                   scale_source: str | None = None,
                   mm_per_px: float | None = None,
                   scale_ref_mm: float | None = None,
                   scale_ref_px: float | None = None) -> MeasurementRecord:
    """`BoxStats` → 线格式记录（字段名翻译的唯一钉点在 `MeasurementRecord`）。

    菌簇四列是平视单档下**唯一可用的长势量**（票 11 §1 的 2026-10-13 加列），必须真发；
    盖径/杆长仍恒 NULL。

    证据列（票 20）由调用方传入——L1–L3 接线层要填 `estimated_fields`（哪些列是估的）、
    `scale_source`（标尺怎么来的）、`mm_per_px`/`scale_ref_mm`/`scale_ref_px`（可交叉复算）。
    **没有就留 NULL**：缺标尺不是"值 0"。
    """
    return MeasurementRecord(
        ts=frame.ts,
        room_id=room_id,
        box_id=frame.box_id,
        station_id=frame.station_id,
        angle_profile=frame.angle_profile,
        n=usable_instance_count(stats, flags=set(flags)),
        # 菌盖直径 / 菌杆长度：单档平视下明确缺测 ⇒ 恒 NULL，禁止近似
        mean_len_mm=None,
        mean_cap_mm=None,
        mean_cluster_w_mm=_round3(stats.mean_cluster_w_mm),
        mean_cluster_h_mm=_round3(stats.mean_cluster_h_mm),
        mean_area_mm2=_round3(stats.mean_area_mm2),
        mean_projected_d_mm=_round3(stats.mean_projected_d_mm),
        p10_len=None,
        p90_len=None,
        quality=quality,
        estimated_fields=estimated_fields,
        scale_source=scale_source,
        mm_per_px=mm_per_px,
        scale_ref_mm=scale_ref_mm,
        scale_ref_px=scale_ref_px,
        notes=notes,
    )


def _round3(value: float | None) -> float | None:
    return None if value is None else round(float(value), 3)


def rows_from_frames(
    frames: Iterable[dict | FrameInput],
    *,
    room_id: str,
    mm_per_px: float,
    detector: Detector | None = None,
    fetch: Callable[[str], bytes] | None = None,
    decode: Callable[[bytes], np.ndarray] | None = None,
    quality_gate: Callable[[np.ndarray], tuple[bool, str]] = frame_quality,
    emit_empty: bool = False,
    log: Callable[[str], None] = print,
) -> list[dict]:
    """多帧 → 线格式行（`kind` 已带）。

    ``emit_empty=False``（默认）：取不到像素 / 帧质量不合格 / 无检出 ⇒ **不产出行**
    （票 11 §5 允许"整轮不写行"；`PatrolDaemon` 对空列表只是个 for 循环）。
    打开它则每帧一行、`n=0` + `no_detection`，用来证明"管线走过这一帧"。
    """
    det = detector or BaselineDetector(BaselineConfig())
    out: list[dict] = []
    durations: list[float] = []

    def one(raw) -> list[dict]:
        """处理一帧 → 该帧的行（0 或 1 行）。

        抽成内部函数只为给"每帧耗时"加计时（Chief 2026-10-14：超门要看得见），
        判定逻辑与以前逐字一致。
        """
        frame = raw if isinstance(raw, FrameInput) else frame_from_meta(raw)
        if frame is None:
            return []
        kind = frame.measure_kind or DEFAULT_MEASURE_KIND
        integrity = content_integrity_reason(frame)
        if frame.content_check_error:
            # "没查成" ≠ "确认无重复"：记日志（样本侧还会按 fail-closed 排除该轮）
            log(f"测量：{frame.box_id} 内容校验没查成（content_check_error）——按未确认处理")
        if integrity:
            # 跨站重复帧：不是"它自己那份"，不能进测量与统计（票 13-round-content-integrity）
            log(f"测量：{frame.box_id} {frame.ts} 内容可疑（{integrity}）——跳过，不产测量行")
            if not emit_empty:
                return []
            extra_dup: set[str] = {"no_detection", LOW_CONFIDENCE_FLAG}
            stats = aggregate_box([])
            quality = quality_for(flags=extra_dup, detected=False)
            rec = row_from_stats(frame=frame, room_id=room_id, stats=stats,
                                 quality=quality,
                                 notes=f"内容可疑：{integrity}", flags=extra_dup)
            row = rec.to_row()
            row["kind"] = kind
            return [row]
        dets: list[Detection] = []
        extra: set[str] = set()
        if fetch is None or decode is None or not frame.cloud_url:
            extra.add("no_detection")
            if not emit_empty:
                return []
        else:
            try:
                image = decode(fetch(frame.cloud_url))
            except Exception as e:  # noqa: BLE001 - 取不到这一帧不该毁掉整轮
                log(f"测量：{frame.box_id} 取图失败（{type(e).__name__}: {e}）——跳过")
                if not emit_empty:
                    return []
                extra.add("no_detection")
                image = None
            if image is not None:
                gray = image if image.ndim == 2 else image.mean(axis=-1)
                ok, qflags = quality_gate(gray)
                if not ok:
                    extra.update(contract.parse_flags(qflags))
                    extra.add("no_detection")
                    if not emit_empty:
                        return []
                else:
                    dets = det.detect(image, mm_per_px=mm_per_px, box_id=frame.box_id)
                    if not dets:
                        extra.add("no_detection")
                        if not emit_empty:
                            return []
        detected = bool(dets)
        flags = detection_flags(dets) | extra
        stats = aggregate_box(dets)
        quality = quality_for(flags=flags, detected=detected)
        rec = row_from_stats(frame=frame, room_id=room_id, stats=stats,
                             quality=quality, notes=notes_for(detected), flags=flags)
        row = rec.to_row()
        row["kind"] = kind
        return [row]

    for raw in frames:
        t0 = time.monotonic()
        produced = one(raw)
        elapsed = time.monotonic() - t0
        durations.append(elapsed)
        if elapsed > MEASURE_TIME_GATE_S:
            log(f"测量：单帧 {elapsed:.2f} s 超门（>{MEASURE_TIME_GATE_S:.2f} s/帧 = "
                f"{MEASURE_ROUND_GATE_S:.0f} s/48 站）"
                f"——先查是否退回全帧/未降采样路径（票 15 §8.1 C1–C3）")
        out.extend(produced)
    if durations:
        log(timing_summary(durations))
    return out


def make_measure_fn(*, room_id: str, mm_per_px: float,
                    detector: Detector | None = None,
                    fetch: Callable[[str], bytes] | None = None,
                    decode: Callable[[bytes], np.ndarray] | None = None,
                    quality_gate: Callable[[np.ndarray], tuple[bool, str]] = frame_quality,
                    emit_empty: bool = False,
                    log: Callable[[str], None] = print) -> Callable[[object], list[dict]]:
    """造 `PatrolDaemon(measure_fn=…)` 要的可调用对象。

    ``room_id`` 必填且**在装配时固定**：它来自 `room.yaml`（与巡检准入门禁同源），
    不该在每轮重新猜；调用方（deploy 侧）负责把它传进来。
    """
    if not room_id:
        raise ValueError("room_id 必填（票 11）：measurements 的幂等键 "
                         "(ts, room_id, box_id) 需要它，且不允许 analysis 从 box_id 反推库房")

    def measure_fn(report) -> list[dict]:
        frames = list(getattr(report, "results", []) or [])
        return rows_from_frames(frames, room_id=room_id, mm_per_px=mm_per_px,
                                detector=detector, fetch=fetch, decode=decode,
                                quality_gate=quality_gate, emit_empty=emit_empty,
                                log=log)

    return measure_fn

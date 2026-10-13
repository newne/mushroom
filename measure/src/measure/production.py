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
                   flags: Iterable[str] = ()) -> MeasurementRecord:
    """`BoxStats` → 线格式记录（字段名翻译的唯一钉点在 `MeasurementRecord`）。

    菌簇四列是平视单档下**唯一可用的长势量**（票 11 §1 的 2026-10-13 加列），必须真发；
    盖径/杆长仍恒 NULL。
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
    for raw in frames:
        frame = raw if isinstance(raw, FrameInput) else frame_from_meta(raw)
        if frame is None:
            continue
        kind = frame.measure_kind or DEFAULT_MEASURE_KIND
        integrity = content_integrity_reason(frame)
        if frame.content_check_error:
            # "没查成" ≠ "确认无重复"：记日志（样本侧还会按 fail-closed 排除该轮）
            log(f"测量：{frame.box_id} 内容校验没查成（content_check_error）——按未确认处理")
        if integrity:
            # 跨站重复帧：不是"它自己那份"，不能进测量与统计（票 13-round-content-integrity）
            log(f"测量：{frame.box_id} {frame.ts} 内容可疑（{integrity}）——跳过，不产测量行")
            if not emit_empty:
                continue
            extra_dup: set[str] = {"no_detection", LOW_CONFIDENCE_FLAG}
            stats = aggregate_box([])
            quality = quality_for(flags=extra_dup, detected=False)
            rec = row_from_stats(frame=frame, room_id=room_id, stats=stats,
                                 quality=quality,
                                 notes=f"内容可疑：{integrity}", flags=extra_dup)
            row = rec.to_row()
            row["kind"] = kind
            out.append(row)
            continue
        dets: list[Detection] = []
        extra: set[str] = set()
        if fetch is None or decode is None or not frame.cloud_url:
            extra.add("no_detection")
            if not emit_empty:
                continue
        else:
            try:
                image = decode(fetch(frame.cloud_url))
            except Exception as e:  # noqa: BLE001 - 取不到这一帧不该毁掉整轮
                log(f"测量：{frame.box_id} 取图失败（{type(e).__name__}: {e}）——跳过")
                if not emit_empty:
                    continue
                extra.add("no_detection")
                image = None
            if image is not None:
                gray = image if image.ndim == 2 else image.mean(axis=-1)
                ok, qflags = quality_gate(gray)
                if not ok:
                    extra.update(contract.parse_flags(qflags))
                    extra.add("no_detection")
                    if not emit_empty:
                        continue
                else:
                    dets = det.detect(image, mm_per_px=mm_per_px, box_id=frame.box_id)
                    if not dets:
                        extra.add("no_detection")
                        if not emit_empty:
                            continue
        detected = bool(dets)
        flags = detection_flags(dets) | extra
        stats = aggregate_box(dets)
        quality = quality_for(flags=flags, detected=detected)
        rec = row_from_stats(frame=frame, room_id=room_id, stats=stats,
                             quality=quality, notes=notes_for(detected), flags=flags)
        row = rec.to_row()
        row["kind"] = kind
        out.append(row)
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

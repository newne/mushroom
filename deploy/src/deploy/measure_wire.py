"""把 `--measure` 参数变成真的测量钩子（测量生产者接线的**部署侧**，票 12）。

`measure/**` 的生产者（`measure.production.make_measure_fn`）要三样东西：

1. **取图 + 解码**：复用 `deploy.framing_wire` 已验证的 `_fetch` / `_imread`
   （MinIO 回环取图 + Pillow 解码；缺 numpy/Pillow 明确拒绝）；
2. **`room_id`**：measurements 的幂等键是 `(ts, room_id, box_id)`（票 11 §1），
   缺库房的行会污染整张区间表 ⇒ 拿不到就**拒绝开启**；
3. **尺度**（可选）：`--mm-per-px` 是站位标定值，给了就是**权威换算**
   （`scale_source=calibration_target`，ADR-0024）。**不给不再拒绝开启**
   （v6 口径，票 15 §17.2）：一期主交付是**像素/估算档**，帧内标尺用
   **菌包标准尺寸**（袋长 240 mm × 高 140 mm ⇒ `scale_source=bag_standard`），
   实现在 `measure.l3`；缺标尺的帧照常产行但 mm 列 NULL + `no_scale`。

本模块只做"能不能开"的接线判定；产出与质量口径全在 `measure.production`（纯函数，有单测）。
**默认 `off`**：这是改生产行为，现场确认后由装配侧显式打开（票 12 开启清单）。

`room_id` 为 `"unset"`（`patrol.room` 在 room.yaml 缺 `room_id` 时的占位值）与空串同等对待
——那是"没配置"，不是库房号，拿它当幂等键等于给数据贴了一个假房间。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: 视为"没有库房号"的值：空串与 `patrol.room` 的占位值。
UNSET_ROOM_IDS = ("", "unset")


@dataclass
class MeasureWire:
    """装配结果：`fn` 为 `None` 就是不接测量（原因写在 `reason` 里）。"""

    fn: Callable[[object], list[object]] | None
    reason: str

    @property
    def enabled(self) -> bool:
        return self.fn is not None


class WireRow:
    """把 `measure` 的**线格式 dict** 包成 `patrol.daemon` 要的 `ToRow` 对象。

    两边的对外形状都不动：`measure.production.make_measure_fn` 吐 dict（带 `kind`，
    离线批量与它的单测都按 dict 用）；`patrol.daemon` 的缝是 `ToRow`（只调 `to_row()`）。
    2026-10-20 接 prod 时才发现"两边各自都有测试"也会拼不上——轮末抛
    `AttributeError: 'dict' object has no attribute 'to_row'`，整轮不落库、不 flush。
    这层包装就是那次事故的修复：**在装配处做形状转换**，不改任何一方的契约。
    """

    __slots__ = ("_row",)

    def __init__(self, row: dict) -> None:
        self._row = row

    def to_row(self) -> dict:
        return self._row


def room_id_is_usable(room_id: str | None) -> bool:
    """这个 `room_id` 能不能当幂等键用（空串与 `unset` 都不行）。"""
    return bool((room_id or "").strip()) and (room_id or "").strip().lower() not in UNSET_ROOM_IDS


def build(args, *, room_id: str | None = None,
          fetch: Callable[[str], bytes] | None = None,
          decode: Callable[[bytes], object] | None = None,
          log: Callable[[str], None] = print) -> MeasureWire:
    """按命令行参数造测量钩子。**不抛错**——测量起不来不该拦住巡检（票 12 验收）。"""
    if getattr(args, "measure", "off") == "off":
        return MeasureWire(None, "测量：关闭（--measure off）")
    if not room_id_is_usable(room_id):
        return MeasureWire(None, f"测量：拿不到可用的 room_id（room.yaml 给的是 {room_id!r}），"
                                 "拒绝开启（缺库房的测量行会污染区间表）")

    from deploy.framing_wire import _fetch, _imread  # 与微调复用同一条取图/解码路

    fetch = fetch or _fetch
    decode = decode or _imread
    try:
        decode(b"")
    except ImportError as e:
        # 缺什么报什么："No module named 'numpy'" 与 "'PIL'" 要装的东西不同。
        return MeasureWire(None, f"测量：解不了图（{e}）——测量需要 pillow 与 numpy，"
                                 "装上再开；本次仍只巡检采图")
    except Exception as e:  # noqa: BLE001 - 空字节必然解码失败，能到这儿说明 Pillow 在
        log(f"测量：解码器自检返回 {type(e).__name__}（正常，Pillow 已就位）")

    from measure.production import make_measure_fn
    from measure.quality import DEFAULT_ABS_LAPVAR_FLOOR, frame_quality

    # 模糊门阈值：**不给就用 `measure.quality` 的标定默认**（那是唯一尺度，Chief 裁定）。
    # `deploy` 侧不再自带一个常数——它曾经是 100.0，而 2026-10-13 用 218 张线上 ok 帧标定出
    # 的中位数是 71.3，旧常数会拒掉 78.4% 的正常帧（"不是没接线，是被门拒"）。
    blur_threshold = getattr(args, "measure_blur_threshold", None)
    raw_mm_per_px = getattr(args, "mm_per_px", None)
    mm_per_px = None if raw_mm_per_px is None else float(raw_mm_per_px)

    def quality_gate(gray):
        if blur_threshold is None:
            return frame_quality(gray)
        return frame_quality(gray, blur_threshold=float(blur_threshold))

    # 日志要打**实际生效值**：运维不能把"没设参数"读成"没有门"。
    if blur_threshold is None:
        blur_note = f"{float(DEFAULT_ABS_LAPVAR_FLOOR):g}（measure.quality 标定默认）"
    else:
        blur_note = f"{float(blur_threshold):g}（命令行指定）"
    # 尺度来源同样要打在**开启那一行**上：现场问"这批 mm 是怎么来的"时，答案在日志里。
    if mm_per_px is None:
        scale_note = ("未给 --mm-per-px ⇒ 菌包标准尺寸当帧内标尺"
                      "（scale_source=bag_standard；像素/估算档，不进 verified）")
    else:
        scale_note = f"{mm_per_px:.4f} mm/px（scale_source=calibration_target）"

    raw_fn = make_measure_fn(
        room_id=str(room_id).strip(),
        mm_per_px=mm_per_px,
        fetch=fetch,
        decode=decode,
        quality_gate=quality_gate,
        emit_empty=bool(getattr(args, "measure_emit_empty", False)),
        log=log,
    )

    def fn(report) -> list[object]:
        # `measure` 现在吐 dict；将来若改回 `ToRow` 对象也照收（装配处只保证"daemon 能 to_row()"）。
        return [row if hasattr(row, "to_row") else WireRow(row) for row in raw_fn(report)]
    return MeasureWire(
        fn,
        f"测量：开启（{scale_note}，房间 {room_id}，模糊门 {blur_note}"
        f"{'，失败帧也产行' if getattr(args, 'measure_emit_empty', False) else ''}）",
    )

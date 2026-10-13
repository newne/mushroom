"""把 `--measure` 参数变成真的测量钩子（测量生产者接线的**部署侧**，票 12）。

`measure/**` 的生产者（`measure.production.make_measure_fn`）要三样东西，
缺任何一样都**不接**，并在日志里说清缺的是什么：

1. **标定换算** `--mm-per-px`（像素 → mm）：没有它就量不出毫米；
2. **`room_id`**：measurements 的幂等键是 `(ts, room_id, box_id)`（票 11 §1），
   缺库房的行会污染整张区间表 ⇒ 拿不到就**拒绝开启**；
3. **取图 + 解码**：复用 `deploy.framing_wire` 已验证的 `_fetch` / `_imread`
   （MinIO 回环取图 + Pillow 解码；缺 numpy/Pillow 明确拒绝）。

本模块只做"能不能开"的接线判定；产出与质量口径全在 `measure.production`（纯函数，有单测）。
**默认 `off`**：这是改生产行为，现场确认标定与阈值后再显式打开。

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

    fn: Callable[[object], list[dict]] | None
    reason: str

    @property
    def enabled(self) -> bool:
        return self.fn is not None


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
    if not getattr(args, "mm_per_px", None):
        return MeasureWire(None, "测量：开了 --measure 但缺 --mm-per-px 标定换算，"
                                 "拒绝开启（没有换算就只能出假毫米）")
    if not room_id_is_usable(room_id):
        return MeasureWire(None, f"测量：拿不到可用的 room_id（room.yaml 给的是 {room_id!r}），"
                                 "拒绝开启（缺库房的测量行会污染区间表）")

    from deploy.framing_wire import _fetch, _imread      # 与微调复用同一条取图/解码路

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
    from measure.quality import frame_quality

    blur_threshold = float(getattr(args, "measure_blur_threshold", 100.0))
    mm_per_px = float(args.mm_per_px)

    def quality_gate(gray):
        return frame_quality(gray, blur_threshold=blur_threshold)

    fn = make_measure_fn(
        room_id=str(room_id).strip(),
        mm_per_px=mm_per_px,
        fetch=fetch,
        decode=decode,
        quality_gate=quality_gate,
        emit_empty=bool(getattr(args, "measure_emit_empty", False)),
        log=log,
    )
    return MeasureWire(
        fn,
        f"测量：开启（{mm_per_px:.4f} mm/px，房间 {room_id}，模糊门 {blur_threshold:g}"
        f"{'，失败帧也产行' if getattr(args, 'measure_emit_empty', False) else ''}）",
    )

"""把 `--framing` 参数变成真的微调钩子（图像微调的部署侧接线）。

四件事缺一不可，缺任何一件都**不开启**微调，并且在日志里说清楚缺的是什么：

1. **换算** `--mm-per-px`（像素 → mm）。这是标定量，没有现场值就只能瞎挪；
2. **取像素**：采图结果里只有 `object_name` / `cloud_url`，像素得再从 MinIO 取一次；
3. **解码** JPEG → 灰度数组。`numpy` 解不了 JPEG，所以用 Pillow——**没装就明确拒绝**，
   而不是让微调静默地不生效；
4. **解码必须真的接在检测器前面**：生产链路给 `evaluate` 的是 JPEG **字节**
   （`orchestrator._shoot` → `framing.pixels` → MinIO 原文），而
   `measure.framing.bright_region_offset` 要的是灰度**数组**。2026-10-13 发现这里以前
   漏了一层：字节直接喂进去，`np.asarray(b"…", dtype=float)` 抛
   `ValueError: could not convert string to float`，`--framing` 一开就会在第一个站位
   把整轮带崩。因为默认 `--framing off`，线上从未触发；复现与修复记录见
   `docs/patrol/imaging-quality/issues/01-camera-imaging-quality.md` 的 Comments。

本模块只做接线，判定逻辑全在 `measure.framing`（检测器）与 `patrol.framing`
（微调闭环 + 可用帧判定），两边都是纯函数、有单测。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

from patrol.framing import FramingRecipe
from patrol.orchestrator import FramingHook


@dataclass
class WireResult:
    hook: FramingHook | None
    reason: str

    @property
    def enabled(self) -> bool:
        return self.hook is not None


def _imread(data: bytes):
    """JPEG/PNG 字节 → 灰度 numpy 数组（Pillow 缺一不可）。"""
    from io import BytesIO

    import numpy as np
    from measure.framing import to_gray
    from PIL import Image

    img = Image.open(BytesIO(data))
    img.load()
    return to_gray(np.asarray(img.convert("RGB"), dtype=float))


def _fetch(url: str, timeout_s: float = 15.0) -> bytes:
    """从 MinIO 取回对象字节（现场是回环/内网地址，不走代理）。"""
    import httpx

    resp = httpx.get(url, timeout=timeout_s)
    resp.raise_for_status()
    return resp.content


def build(args, *, fetch: Callable[[bytes], bytes] | None = None,
          imread: Callable[[bytes], object] | None = None,
          log: Callable[[str], None] = print) -> WireResult:
    """按命令行参数造钩子。返回 `WireResult`（不抛错——微调起不来不该拦住巡检）。"""
    if args.framing == "off":
        return WireResult(None, "图像微调：关闭（只用推导坐标）")
    if not args.mm_per_px:
        return WireResult(None, "图像微调：开了 --framing 但缺 --mm-per-px 标定换算，"
                                "拒绝开启（没有换算就只能瞎挪）")

    fetch = fetch or _fetch
    imread = imread or _imread
    try:
        imread(b"")
    except ImportError as e:
        # 缺什么就报什么：现场看到 "No module named 'numpy'" 与 "'PIL'" 要装的东西不同。
        return WireResult(None, f"图像微调：解不了图（{e}）——微调需要 pillow 与 numpy，"
                                "装上再开；本次仍只走推导坐标")
    except Exception as e:  # noqa: BLE001 - 空字节必然解码失败，能到这儿说明 Pillow 在
        log(f"图像微调：解码器自检返回 {type(e).__name__}（正常，Pillow 已就位）")

    from measure.framing import bright_region_offset
    from measure.quality import frame_quality
    from patrol.framing import gated_evaluate

    recipe = FramingRecipe(
        mm_per_px_y=args.mm_per_px,
        mm_per_px_z=args.mm_per_px_z or args.mm_per_px,
        sign_y=args.framing_sign_y,
        sign_z=args.framing_sign_z,
    )

    def pixels(result) -> bytes:
        return fetch(result.cloud_url)

    def to_gray(pixels):
        """像素 → 灰度数组（模块 docstring 第 4 条：这一层以前漏了）。

        已是数组就原样返回——`deploy` 的既有用例直接喂灰度数组，那是**测试口径**，
        与生产契约（字节）相反。兼容分支只为让那个用例继续绿；票据 04 把它改成正交
        契约后，这个 `isinstance` 就可以删掉。

        **刻度**：返回 0–255（`measure.framing.to_gray` 的口径）。`measure.quality` 的
        清晰度阈值按 0–255 定标（票 13），喂 0–1 会小 65025 倍、绝对下限失效。
        """
        return imread(pixels) if isinstance(pixels, (bytes, bytearray)) else pixels

    def detect(pixels):
        return bright_region_offset(to_gray(pixels))

    def assess(pixels):
        """这一帧能不能用——**唯一尺度在 `measure.quality`**（Chief 2026-10-13 裁定）。

        阈值不在这里、也不在 `patrol.framing`：票 13 用 218 张线上真机帧定标
        （清晰度 45.0 / 近黑 p99<0.12 / 过曝 5%）。要改阈值写票据给测量师。
        """
        return frame_quality(to_gray(pixels))

    # 可用帧判定（imaging-quality 票 01 第 2 项）：默认开——坏帧不该驱动微调。
    evaluate = detect
    gate_note = "可用帧判定：关（PATROL_FRAME_QUALITY=0）"
    if os.environ.get("PATROL_FRAME_QUALITY", "1").strip().lower() not in ("0", "false", "no", "off"):
        evaluate = gated_evaluate(detect, assess)
        gate_note = "可用帧判定：开（尺度取 measure.quality：清晰度/近黑/过曝，票 13 定标）"
    log(gate_note)

    return WireResult(
        FramingHook(recipe=recipe, evaluate=evaluate, pixels=pixels),
        f"图像微调：开启（{args.framing}，{args.mm_per_px:.4f} mm/px，"
        f"符号 Y={args.framing_sign_y:+.0f} Z={args.framing_sign_z:+.0f}；{gate_note}）",
    )


def make_apply_trim(stations: list, stations_path: str,
                    log: Callable[[str], None] = print) -> Callable[[dict], None]:
    """回报"学到的 trim 怎么落盘"：更新内存里的站位表 + 写回 YAML。

    只改**内存里的同一批对象**（`patrol-teach` 与巡检读的是同一份表），并用
    `save_stations` 整体重写——一次落盘比每站写一次安全得多（不会出现半套 trim）。
    """
    from datetime import datetime

    from patrol.stations import save_stations

    def apply(learned: dict[str, tuple[float, float]]) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        for station in stations:
            trim = learned.get(station.id)
            if trim is None:
                continue
            station.trim_y, station.trim_z = trim
            station.trim_note = f"framing {stamp}"
        save_stations(stations, stations_path)
        log(f"站位表已更新：{len(learned)} 个站位的微调偏移写入 {stations_path}")

    return apply

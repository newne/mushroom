"""把 `--framing` 参数变成真的微调钩子（图像微调的部署侧接线）。

三件事缺一不可，缺任何一件都**不开启**微调，并且在日志里说清楚缺的是什么：

1. **换算** `--mm-per-px`（像素 → mm）。这是标定量，没有现场值就只能瞎挪；
2. **取像素**：采图结果里只有 `object_name` / `cloud_url`，像素得再从 MinIO 取一次；
3. **解码** JPEG → 灰度数组。`numpy` 解不了 JPEG，所以用 Pillow——**没装就明确拒绝**，
   而不是让微调静默地不生效。

本模块只做接线，判定逻辑全在 `measure.framing`（纯函数，有单测）。
"""

from __future__ import annotations

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


def build(args, *, fetch: Callable[[str], bytes] | None = None,
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

    recipe = FramingRecipe(
        mm_per_px_y=args.mm_per_px,
        mm_per_px_z=args.mm_per_px_z or args.mm_per_px,
        sign_y=args.framing_sign_y,
        sign_z=args.framing_sign_z,
    )

    def pixels(result) -> bytes:
        return fetch(result.cloud_url)

    return WireResult(
        FramingHook(recipe=recipe, evaluate=bright_region_offset, pixels=pixels),
        f"图像微调：开启（{args.framing}，{args.mm_per_px:.4f} mm/px，"
        f"符号 Y={args.framing_sign_y:+.0f} Z={args.framing_sign_z:+.0f}）",
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

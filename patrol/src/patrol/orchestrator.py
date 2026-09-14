"""单站位采图闭环（票 02）：运动 → 补光 → 截图服务采图 → 灭灯 → 元数据。

**两步定位**（2026-09-13 现场追加）：站位坐标是从行程**推导**的（12 框均分 4492mm、
5 层均分 212mm），推导值只保证"目标进视野"，不保证"目标在画面中央"。所以：

1. **第一步**：走到「推导坐标 + 学到的 trim」，拍一张——这张图一定存在，
   后面无论发生什么，本站都有成果（这是 `patrol.framing` 的兜底前提）；
2. **第二步**（可选的 `framing` 钩子）：用这一张图量出偏移、挪一点、再拍，
   直到居中（细节与安全边界见 `patrol.framing`）。

采到的**每一张**都会存进 MinIO：微调过程中的试探帧带 `-probe{n}` 后缀，
只有最终采用的那张进图像索引（否则历史里会混进一堆"挪之前"的废片）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from patrol.capture_client import CaptureClient, CaptureError
from patrol.fmc import Fmc4030
from patrol.framing import FramingError, FramingRecipe, fine_tune
from patrol.motion_profile import M1
from patrol.stations import Station, image_object_name


@dataclass
class FramingHook:
    """把"图像 → 偏移"和"采图结果 → 像素"两件事注入进来。

    `patrol` 不依赖任何图像库（也读不到 MinIO），所以这两步必须由部署侧提供：
    `evaluate` 是检测器实现（`measure` 侧），`pixels` 负责把采图结果换成像素。
    两个都缺 ⇒ 不传这个钩子 ⇒ 退回"只走第一步"的老行为。
    """

    recipe: FramingRecipe
    evaluate: Callable[[bytes], FramingError]
    pixels: Callable[[object], bytes]


class StationCapture:
    def __init__(self, fmc: Fmc4030, capture: CaptureClient, *,
                 decay_s: float = M1.decay_s,
                 settle_s: float = M1.lamp_settle_s,
                 retries: int = 1,
                 lamp_io: int = 0,
                 framing: FramingHook | None = None,
                 framing_limits: tuple[float, float, float, float] | None = None,
                 log: Callable[[str], None] = print,
                 sleep=time.sleep):
        self.fmc = fmc
        self.capture = capture
        self.decay_s = decay_s
        self.settle_s = settle_s
        self.retries = retries
        self.lamp_io = lamp_io
        self.framing = framing
        self.framing_limits = framing_limits or (
            M1.y.travel_min, M1.y.travel_max, M1.z.travel_min, M1.z.travel_max
        )
        self.log = log
        self._sleep = sleep

    # ---------- 采图 ----------

    def _capture_once(self, station: Station, ts: datetime, *, suffix: str = "") -> object:
        """拍一张并重试；重试时错开对象名，避免覆盖上一张。"""
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            object_name = image_object_name(station, ts + timedelta(seconds=attempt)) + suffix
            try:
                return self.capture.capture(
                    ip=station.camera_ip,
                    user=station.camera_user,
                    pwd=station.camera_pwd,
                    filename=object_name,
                )
            except CaptureError as e:
                last_exc = e
        raise last_exc  # type: ignore[misc]

    def _shoot(self, station: Station, ts: datetime):
        """给 `fine_tune` 用的 `shoot(tap)`：拍一张并取回它的像素。"""

        def shoot(tap: int) -> tuple[bytes, object]:
            suffix = "" if tap == 0 else f"-probe{tap}"
            result = self._capture_once(station, ts, suffix=suffix)
            try:
                return self.framing.pixels(result), result  # type: ignore[union-attr]
            except Exception as e:  # noqa: BLE001 - 取不到像素只是"没法微调"，不是拍失败
                self.log(f"取不到 {station.id} 这一帧的像素，放弃微调：{e}")
                return b"", result

        return shoot

    def _evaluate(self, pixels: bytes) -> FramingError:
        if not pixels:
            return FramingError(found=False, reason="这一帧的像素取不到（微调跳过）")
        return self.framing.evaluate(pixels)  # type: ignore[union-attr]

    def run(self, station: Station, *, ts: datetime | None = None) -> dict:
        """执行一个站位的完整采图时序，返回元数据；失败抛 CaptureError/FmcError。

        灯光窗口保证在 finally 中关闭；重试时重新生成对象名避免覆盖。
        返回的 ``yz`` 是**实际拍下那张图时的位置**（含 trim 与微调位移），
        不是站位表里的推导坐标——回溯照片时这两者必须能区分开。
        """
        ts = ts or datetime.now()
        t0 = time.monotonic()
        # trim 是"上次微调学到的小偏移"，叠加在推导坐标上。它是站位表的一部分，
        # 所以手动示教过、或从没微调过的站位（trim=0）行为与从前完全一致。
        base = (station.y + station.trim_y, station.z + station.trim_z)
        self.fmc.goto(*base)  # 到位确认由 goto 负责（超时抛 MotionTimeoutError 等）
        self._sleep(self.decay_s)

        outcome = None
        try:
            self.fmc.lamp(True, io=self.lamp_io)
            self._sleep(self.settle_s)
            if self.framing is None:
                result = self._capture_once(station, ts)
                shot_at = base
            else:
                outcome = fine_tune(
                    nominal=base,
                    recipe=self.framing.recipe,
                    limits=self.framing_limits,
                    move=self.fmc.goto,
                    shoot=self._shoot(station, ts),
                    evaluate=self._evaluate,
                    log=self.log,
                )
                shot_at = outcome.chosen_pos
                result = outcome.chosen
                if result is None:      # 兜底：理论上 fine_tune 一定会带回一帧
                    result = self._capture_once(station, ts)
        finally:
            self.fmc.lamp(False, io=self.lamp_io)

        meta = {
            "ts": ts.isoformat(timespec="seconds"),
            "ok": True,
            "box_id": station.box_id,
            "station_id": station.id,
            "yz": [round(shot_at[0], 3), round(shot_at[1], 3)],
            "angle_profile": station.angle_profile,
            "camera_ip": station.camera_ip,
            "object_name": result.object_name,
            "cloud_url": result.cloud_url,
            # 单站耗时是整轮时长的唯一可分解口径（采图实测 5.2 s 是大头）
            "elapsed_s": round(time.monotonic() - t0, 2),
        }
        if outcome is not None:
            row = outcome.to_row()
            trim_y, trim_z = outcome.trim
            # 累计 trim：下一轮直接从这儿起步 ⇒ 常规轮次零额外耗时（见 framing 模块顶部）
            row["suggested_trim"] = [round(station.trim_y + trim_y, 3),
                                     round(station.trim_z + trim_z, 3)]
            row["base_yz"] = [round(base[0], 3), round(base[1], 3)]
            meta["framing"] = row
        return meta

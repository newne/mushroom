"""测量记录——测量记录的唯一命名契约（架构评审 #3）。

字段名 = spec §6 `measurements` 表契约，是生产者（measure）、传输（outbox 行）、
存储（analysis/db）三方共同引用的唯一钉子。
BoxStats 是分析域的统计对象（带单位后缀的字段名）；本 record 负责到线路格式的
唯一一次名字翻译。

## 票 11 契约补记（2026-10-13，环境官）

- `room_id` **必填**：`align.py` 按小时单键聚合环境，缺库房维度会把四个库房混进
  同一个分箱，区间表算出来是**错的**而不是空的。生产者手上有 round/room 上下文，
  必须自己填——**不允许 analysis 从 `box_id` 反推库房**（没有权威映射源）。
- `station_id` / `angle_profile` 可选但建议带上：`images` 表主键是
  `(ts, station_id, angle_profile)`，将来 join 要用。
- `ts` = 采图/测量时刻的本地 ISO8601（与 `rounds`/`images` 同口径），**不做预聚合**：
  小时聚合是 analysis 的活（`hourly_rates` 已经 `floor('h')`）。
- 无值一律 NULL（见 `measure.contract`），不写 0。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from measure.pipeline import BoxStats

#: spec §6 + 票 11 的 measurements 线格式字段。
#: **钉点**：这一份与 analysis 侧 `MEASUREMENT_FIELDS` 必须一字不差（票 11 §1 铁律）。
#: 2026-10-13 环境官落了菌簇四列（`mean_cluster_w_mm` / `mean_cluster_h_mm` /
#: `mean_area_mm2` / `mean_projected_d_mm`）：平视单档下盖径/杆长缺测，**长势只能从
#: 这几个量算**，所以它们必须真发；顺序也照 analysis 侧（`db.py` 的
#: `MEASUREMENT_FIELDS`），加字段先改票、两侧同时改。
RECORD_FIELDS = (
    "ts",
    "room_id",
    "box_id",
    "station_id",
    "angle_profile",
    "n",
    "mean_len_mm",
    "mean_cap_mm",
    "mean_cluster_w_mm",
    "mean_cluster_h_mm",
    "mean_area_mm2",
    "mean_projected_d_mm",
    "p10_len",
    "p90_len",
    "quality",
)


@dataclass
class MeasurementRecord:
    ts: str
    room_id: str
    box_id: str
    station_id: str | None = None
    angle_profile: str | None = None
    n: int | None = None
    mean_len_mm: float | None = None
    mean_cap_mm: float | None = None
    mean_cluster_w_mm: float | None = None
    mean_cluster_h_mm: float | None = None
    mean_area_mm2: float | None = None
    mean_projected_d_mm: float | None = None
    p10_len: float | None = None
    p90_len: float | None = None
    quality: str | None = None
    #: 缺测原因/口径说明（如"菌盖直径、菌杆长度：无对应角度档"）。**不在线格式里**：
    #: 票 11 §1 规定 field 清单两侧一字不差，加字段要先改票；先放这里供离线诊断与
    #: 票据证据用。环境官若决定落 notes 列，再从 `RECORD_FIELDS` 走契约流程。
    notes: str | None = None

    def __post_init__(self) -> None:
        if not self.room_id:
            raise ValueError("room_id 必填（票 11）：缺库房维度的测量行会污染整张区间表")

    @classmethod
    def from_box_stats(cls, stats: BoxStats, ts: str, *, room_id: str,
                       station_id: str | None = None,
                       angle_profile: str | None = None,
                       notes: str | None = None) -> MeasurementRecord:
        """BoxStats → 线路记录：唯一一次字段名翻译（*_mm → 线格式名）。"""
        return cls(
            ts=ts,
            room_id=room_id,
            box_id=stats.box_id,
            station_id=station_id,
            angle_profile=angle_profile,
            n=stats.n_total,
            mean_len_mm=stats.mean_len_mm,
            mean_cap_mm=stats.mean_cap_mm,
            mean_cluster_w_mm=stats.mean_cluster_w_mm,
            mean_cluster_h_mm=stats.mean_cluster_h_mm,
            mean_area_mm2=stats.mean_area_mm2,
            mean_projected_d_mm=stats.mean_projected_d_mm,
            p10_len=stats.p10_len_mm,
            p90_len=stats.p90_len_mm,
            quality=stats.quality_flags,
            notes=notes,
        )

    def to_row(self) -> dict:
        """outbox / ingest 行（字段名与 RECORD_FIELDS 严格一致）。"""
        return {f: getattr(self, f) for f in RECORD_FIELDS}

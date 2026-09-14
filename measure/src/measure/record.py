"""MeasurementRecord——测量记录的唯一命名契约（架构评审 #3）。

字段名 = spec §6 `measurements` 表契约，是生产者（measure）、传输（outbox 行）、
存储（analysis/db）三方共同引用的唯一钉子。
BoxStats 是分析域的统计对象（带单位后缀的字段名）；本 record 负责到线路格式的
唯一一次名字翻译。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from measure.pipeline import BoxStats

# spec §6：measurements 表字段（线格式）
RECORD_FIELDS = (
    "ts",
    "box_id",
    "n",
    "mean_len_mm",
    "mean_cap_mm",
    "p10_len",
    "p90_len",
    "quality",
)


@dataclass
class MeasurementRecord:
    ts: str
    box_id: str
    n: int | None = None
    mean_len_mm: float | None = None
    mean_cap_mm: float | None = None
    p10_len: float | None = None
    p90_len: float | None = None
    quality: str | None = None

    @classmethod
    def from_box_stats(cls, stats: BoxStats, ts: str) -> MeasurementRecord:
        """BoxStats → 线路记录：唯一一次字段名翻译（*_mm → 线格式名）。"""
        return cls(
            ts=ts,
            box_id=stats.box_id,
            n=stats.n_total,
            mean_len_mm=stats.mean_len_mm,
            mean_cap_mm=stats.mean_cap_mm,
            p10_len=stats.p10_len_mm,
            p90_len=stats.p90_len_mm,
            quality=stats.quality_flags,
        )

    def to_row(self) -> dict:
        """outbox / ingest 行（字段名与 RECORD_FIELDS 严格一致）。"""
        return {f: getattr(self, f) for f in RECORD_FIELDS}

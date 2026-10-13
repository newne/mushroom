"""轮/帧内容完整性（巡检手票 13）：**哪些数据不许进聚合统计**。

## 事实

2026-10-09 18:20–18:27 那一轮，48 帧里 **35 帧字节完全相同**（同一张陈旧近黑帧
被复制到 S211→S401），而轮行写着 `ok=1 / 48 帧 / 0 失败`；全池 3147 帧只有这一轮可疑。
这种帧如果进了区间表或生长曲线，会变成"这段时间没长"或"长得很好"的假结论 ——
比缺数据更坏，因为它看起来正常。

## 判据（索引行自带，随 `/ingest` 原样进来）

| 来源 | 标记 | 处置 |
| --- | --- | --- |
| 帧行 | `images.frame_dup = 1`（+ `dup_group` 同指纹组） | 该帧不许进统计 |
| 轮行 | `rounds.suspect_duplicate = 1`（+ `dup_groups`） | **整轮**的帧不许进统计 |
| 轮行 | `rounds.content_check_error` 非空 | **"没查成"不是"已确认无重复"** ⇒ 同样排除，并说明原因 |

## 为什么按"轮窗口"排除

测量行只有 `(ts, room_id, box_id)`，**没有 `round_id`**（票 11 的线格式里没有），
所以没法逐帧精确对到测量行。于是取该库房**可疑轮的窗口**：
`[round.ts, 同库房下一条 round.ts)`；测量时刻落在窗口内即排除。
没有下一条轮次时，窗口上界取 `round.ts + SUSPECT_MAX_SPAN_H`（默认 1 小时）——
**不让一个可疑轮把该库房之后的所有数据永久排除掉**。

## 口径

- 排除是**保守**方向：宁可少算一段，也不把复制帧当长势。
- 被排除的数量要能查（返回 `Dropped` 清单），**不静默丢**。
- 老数据没有这些标记（列是 NULL）⇒ 一律照常参与统计。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

#: 可疑轮没有后继轮次时的窗口上界（小时）。轮次本身通常 8–15 分钟，1 小时够宽也够安全。
SUSPECT_MAX_SPAN_H = 1.0

REASON_SUSPECT_ROUND = "suspect_duplicate_round"
REASON_CHECK_ERROR = "content_check_error"
REASON_FRAME_DUP = "frame_dup"


@dataclass(frozen=True)
class Interval:
    """一个可疑窗口：`[start, end)`，`end=None` 表示"到下一个轮次为止"。"""

    start: str
    end: str | None
    reason: str
    bounds: str | None = None      # 触发该窗口的轮次时间，便于追溯


@dataclass(frozen=True)
class Dropped:
    """一条被排除的数据 + 为什么。"""

    ts: str
    zone: str
    reason: str
    window: str


def _truthy(value) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in ("0", "false", "none", "")


def _plus_hours(ts: str, hours: float) -> str | None:
    try:
        return (datetime.fromisoformat(ts) + timedelta(hours=hours)).isoformat()
    except (TypeError, ValueError):
        return None


def suspect_intervals(rounds: list[dict]) -> dict[str, list[Interval]]:
    """按库房列出可疑窗口（升序）。

    `rounds` 是 `/rounds` 那样的行字典（需要 `ts`、`room_id`、
    `suspect_duplicate`、`content_check_error`）。缺少这些列的旧行一律不算可疑。
    """
    by_room: dict[str, list[tuple[str, str]]] = {}
    for row in rounds:
        zone = str(row.get("room_id") or "")
        ts = str(row.get("ts") or "")
        if not zone or not ts:
            continue
        reason = None
        if _truthy(row.get("suspect_duplicate")):
            reason = REASON_SUSPECT_ROUND
        elif str(row.get("content_check_error") or "").strip():
            # **没查成**也排除：不能把"没查"当"没有重复"（巡检手票 13 的原话）
            reason = REASON_CHECK_ERROR
        if reason:
            by_room.setdefault(zone, []).append((ts, reason))

    out: dict[str, list[Interval]] = {}
    for zone, marks in by_room.items():
        times = sorted(str(row.get("ts")) for row in rounds
                       if str(row.get("room_id") or "") == zone and row.get("ts"))
        intervals: list[Interval] = []
        for ts, reason in sorted(marks):
            later = [t for t in times if t > ts]
            end = later[0] if later else _plus_hours(ts, SUSPECT_MAX_SPAN_H)
            intervals.append(Interval(start=ts, end=end, reason=reason, bounds=ts))
        out[zone] = intervals
    return out


def exclusion_reason(intervals: dict[str, list[Interval]], zone: str, ts: str) -> str | None:
    """`ts` 落在该库房某个可疑窗口里就给出原因，否则 None（含"列还是 NULL 的老数据"）。"""
    for item in intervals.get(str(zone or ""), []):
        if item.start <= ts and (item.end is None or ts < item.end):
            return item.reason
    return None


def partition(
    rows: list[dict],
    rounds: list[dict],
    *,
    zone_key: str = "room_id",
    ts_key: str = "ts",
) -> tuple[list[dict], list[Dropped]]:
    """把数据行分成"可进统计"与"被排除"两堆（返回 kept 与 dropped 明细）。

    调用方应当把 `dropped` 的数量记进响应或日志 —— **不静默丢**。
    """
    intervals = suspect_intervals(rounds)
    kept: list[dict] = []
    dropped: list[Dropped] = []
    for row in rows:
        ts = str(row.get(ts_key) or "")
        zone = str(row.get(zone_key) or "")
        reason = exclusion_reason(intervals, zone, ts) if ts else None
        if reason is None:
            kept.append(row)
        else:
            window = next((f"{i.start}~{i.end or '…'}" for i in intervals.get(zone, [])
                           if i.start <= ts and (i.end is None or ts < i.end)), "")
            dropped.append(Dropped(ts=ts, zone=zone, reason=reason, window=window))
    return kept, dropped


def dup_boxes(images: list[dict], *, zone: str) -> set[str]:
    """该库房里带 `frame_dup` 的帧所属的框（帧级排除用，供逐框判定参考）。"""
    return {
        str(row.get("box_id"))
        for row in images
        if str(row.get("room_id") or "") == str(zone)
        and _truthy(row.get("frame_dup"))
        and row.get("box_id")
    }

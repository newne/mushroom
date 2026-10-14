"""轮/帧内容完整性（巡检手票 13）：**哪些数据不许进聚合统计**。

## 事实

2026-10-09 18:20–18:27 那一轮，48 帧里 **35 帧字节完全相同**（同一张陈旧近黑帧
被复制到 S211→S401），而轮行写着 `ok=1 / 48 帧 / 0 失败`；全池 3147 帧只有这一轮可疑。
这种帧如果进了区间表或生长曲线，会变成"这段时间没长"或"长得很好"的假结论 ——
比缺数据更坏，因为它看起来正常。

## 判据（索引行自带，随 `/ingest` 原样进来）

| 来源 | 标记 | 处置 |
| --- | --- | --- |
| 帧行 | `images.frame_dup`（生产者发布尔 `True` ⇒ 库里 0/1）+ `dup_group`（sha12） | 该帧不许进统计 |
| 轮行 | `rounds.suspect_duplicate`（生产者发布尔 `True`）+ `dup_groups`（**对象数组**：`[{"sha256_12","n","stations"}, …]`，无重复时 NULL 或 `[]`） | **整轮**的帧不许进统计 |
| 轮行 | `rounds.content_check_error` 非空 | **"没查成"不是"已确认无重复"** ⇒ 同样排除，并说明原因 |

**别拿 `duplicate_streak` 当排除判据**（Chief 2026-10-14，界面师真机实测）：它的**正常值就是 1**
（"连续 1 张"＝它自己），`> 0` 会把每一帧都判成不可信；它只作展示/取证，要用必须 `> 1`。
本模块的排除判定只读 `frame_dup`/`suspect_duplicate`/`content_check_error` 三个标量。

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
REASON_LEGACY_EXCLUDED = "legacy_excluded"


@dataclass(frozen=True)
class Interval:
    """一个可疑窗口：`[start, end)`，`end=None` 表示"到下一个轮次为止"。"""

    start: str
    end: str | None
    reason: str
    bounds: str | None = None      # 触发该窗口的轮次时间，便于追溯
    detail: str | None = None      # 人话原因（显式排除清单里那条全文）


@dataclass(frozen=True)
class Dropped:
    """一条被排除的数据 + 为什么。"""

    ts: str
    zone: str
    reason: str
    window: str
    detail: str | None = None      # **排除必须能说出为什么**，不静默消失


@dataclass(frozen=True)
class LegacyExclusion:
    """一条**显式**排除：给"当时还没有标记列"的 legacy 轮用。

    背景（2026-10-09 那轮）：`suspect_duplicate`/`frame_dup` 这些列当时并不存在，
    轮行上全是 NULL —— **靠标记永远排不掉它**；现在能"排除"只是因为那 35 帧太黑会被
    质量门顺手丢掉，那是运气不是口径。所以点名把它写进名单。

    两种键，二选一（都填则都要满足）：
    - `round_id`：有轮次 id 的行 ⇒ 按该轮的时间窗排（与标记口径一致）；
    - `(room_id, start, end)`：**legacy 行没有 round_id**（实测：2026-10-09 及更早全为 NULL，
      `docs/patrol/prod-deploy/frame-sample-extract.py:38`）⇒ 用实测窗口。
      窗口两端必须是**证据**（帧时间戳），不许为了"凑一条"编一个。

    `verified=False` 的条目**不生效**（见 `active_exclusions`）：防止一个还没现场核准的
    room_id/窗口被悄悄写进名单、把好数据排掉。
    """

    reason: str
    round_id: str | None = None
    room_id: str | None = None
    start: str | None = None
    end: str | None = None
    approved_by: str | None = None
    verified: bool = False


#: 显式排除清单（票 17 §2.5）。**只增不改**；每条都要能说出"为什么、谁批的"。
LEGACY_EXCLUSIONS: tuple[LegacyExclusion, ...] = (
    LegacyExclusion(
        reason=(
            "2026-10-09 那一轮：48 帧里 35 帧字节完全相同（同一份陈旧近黑帧 sha256 41685c09…、"
            "92364 B、p99=2.0，S211→S401，18:20:05–18:27:16），而轮行看起来完全正常："
            "`ok=1 / n_results=48 / n_failures=0 / aborted=0`。"
            "该轮是 **legacy 行**：`suspect_duplicate`/`dup_groups`/`content_check_error` 全为 NULL，"
            "**靠标记排不掉**（现在能过滤掉只是因为那 35 帧太黑被质量门顺手丢，那是运气不是口径）。"
        ),
        # 轮行的 round_id **不是 NULL**（`.scratch/deploy-acceptance/round-accident-12038e7a.json` 原文）；
        # 而**帧行**那一代可能没有 round_id —— 所以两个键都留着，见 suspect_intervals 的优先级。
        # ⚠️ 窗口两端（2026-10-14 更正，发版官实测 + 边界复核）：
        #   左端 = **该轮开始时刻** 18:20:02（`docs/patrol/prod-deploy/issues/02`：巡检轮次 18:20:02 → 18:27:34；
        #          最早帧 ts 18:20:05，比它晚 3 秒）；
        #   右端 = **轮行 ts 向上取整到整分** 18:27:34 → 18:28:00 —— 匹配是半开区间 `[start, end)`，
        #          右端若取 18:27:34，**轮行自己会落在窗口外**，凡按轮行/帧行 ts 走的路径就排不掉这一轮
        #          （发版官 2026-10-14 指出）；取整分同时让窗口不必依赖"帧早于轮行结束"这个巧合。
        #   右端与测量侧兜底名单一致（`measure/src/measure/sample_hygiene.py:22` 写 18:22:00–18:28:00）：
        #          **同一个坏轮，两个域不许用两个窗口**。
        round_id="12038e7ac0334a8bbc1e178793ed5c09",
        room_id="611",
        start="2026-10-09T18:20:02",
        end="2026-10-09T18:28:00",
        approved_by="Chief 2026-10-14",
        verified=True,
    ),
)


def active_exclusions(
    exclusions: tuple[LegacyExclusion, ...] | None = None,
) -> tuple[LegacyExclusion, ...]:
    """只有 `verified=True` 的条目才生效 —— 未核准的窗口不许悄悄排数据。"""
    items = LEGACY_EXCLUSIONS if exclusions is None else exclusions
    return tuple(item for item in items if item.verified)


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


def suspect_intervals(
    rounds: list[dict],
    *,
    exclusions: tuple[LegacyExclusion, ...] | None = None,
) -> dict[str, list[Interval]]:
    """按库房列出可疑窗口（升序）：**标记列**（`suspect_duplicate`/`content_check_error`）
    与**显式排除清单**（`LEGACY_EXCLUSIONS`，只吃已核准条目）合并。

    `rounds` 是 `/rounds` 那样的行字典（需要 `ts`、`room_id`、`round_id`、
    `suspect_duplicate`、`content_check_error`）。缺少这些列的旧行一律**不算可疑**
    —— legacy 轮靠清单点名，不靠给它编标记。
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

    # 显式排除清单（legacy 轮没有标记列，只能点名排）——只吃已核准条目。
    # **两个键**：`round_id` 优先命中；`round_id` 为空（或没查到这个轮）时按时间窗命中。
    # 命中的行**带原因**：哪条规则命中 + 谁批的 + 为什么。
    for item in active_exclusions(exclusions):
        rule: str | None = None
        zone = item.room_id
        start = item.start
        end = item.end

        if item.round_id:
            matched = next((r for r in rounds
                            if str(r.get("round_id") or "") == item.round_id), None)
            if matched is not None:
                rule = "round_id"
                zone = zone or str(matched.get("room_id") or "") or None
            if not (start and zone) and matched is not None:
                # 没有显式窗口（或没有库房）时，退回"该轮行 → 同库房下一轮"的窗口
                ts = str(matched.get("ts") or "")
                row_zone = zone or str(matched.get("room_id") or "")
                if ts and row_zone:
                    times = sorted(str(r.get("ts")) for r in rounds
                                   if str(r.get("room_id") or "") == row_zone and r.get("ts"))
                    later = [t for t in times if t > ts]
                    start = start or ts
                    end = end or (later[0] if later else _plus_hours(ts, SUSPECT_MAX_SPAN_H))
                    zone = row_zone

        if not (zone and start):
            # 归属不了就不排：宁可少排，也不冤枉别的库房
            continue
        if rule is None:
            rule = "window" if item.start else "round_id"

        who = item.approved_by or "未记录批准人"
        detail = (f"规则：{'round_id 命中' if rule == 'round_id' else '时间窗命中'}"
                  f"；批准：{who}；{item.reason}")
        out.setdefault(str(zone), []).append(Interval(
            start=start, end=end, reason=REASON_LEGACY_EXCLUDED,
            bounds=item.round_id, detail=detail,
        ))

    for zone in out:
        out[zone].sort(key=lambda i: i.start)
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
    exclusions: tuple[LegacyExclusion, ...] | None = None,
) -> tuple[list[dict], list[Dropped]]:
    """把数据行分成"可进统计"与"被排除"两堆（返回 kept 与 dropped 明细）。

    调用方应当把 `dropped` 的数量记进响应或日志 —— **不静默丢**：
    每条 `Dropped` 都带 `reason` 与 `detail`（人话原因），能回答"为什么少了这一段"。
    """
    intervals = suspect_intervals(rounds, exclusions=exclusions)
    kept: list[dict] = []
    dropped: list[Dropped] = []
    for row in rows:
        ts = str(row.get(ts_key) or "")
        zone = str(row.get(zone_key) or "")
        hit = next((i for i in intervals.get(zone, [])
                    if ts and i.start <= ts and (i.end is None or ts < i.end)), None)
        if hit is None:
            kept.append(row)
        else:
            dropped.append(Dropped(
                ts=ts, zone=zone, reason=hit.reason,
                window=f"{hit.start}~{hit.end or '…'}", detail=hit.detail,
            ))
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

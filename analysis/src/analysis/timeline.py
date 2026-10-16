"""覆盖轨迹（票 02）：`rounds` 的逐轮 × `images` 的帧数，摊成「本批每一轮一格」。

**这里只有纯函数**（没有数据访问、没有 SQL）：取数在 `analysis.store`，装配在 `analysis.api`。
放这一层的理由与 `analysis.growth` / `analysis.environment` 一样——"格点长什么样"是可单测的
形状逻辑，混进 API 就只能对着 HTTP 测。

规格（docs/patrol/growth-timeline/spec.md）的三条硬规则里，落在**数据形状**上的是这两条：

1. **x 永远是真实时间戳**：格点带 `ts`，`day`（生长天数）由 `entry_date` 派生为第二刻度；
   端点**不产出**任何"第几个点"的序号——页面想按数组下标画都无从下手。
2. **`null ≠ 0`**：这一轮这个站位没有帧 ⇒ `has_frame=False` 且 `n_frames=None`（**不是 0**）。
   "没拍到"与"拍到 0 张"不是一件事：前者是缺口（页面断开、画灰），后者根本不存在。
"""

from __future__ import annotations

from datetime import date


def day_of(entry_date, ts) -> int | None:
    """生长天数：**入库当天 = 0**（与 `/setpoints/daily` 的 `growth_day` 同一口径）。

    认不出来的日期（缺 `entry_date`、`ts` 不是 ISO 日期前缀）⇒ `None`：
    **不猜**一个第几天摆上去，页面对 `None` 不画天数刻度。
    """
    start = _as_date(entry_date)
    when = _as_date(ts)
    if start is None or when is None:
        return None
    return (when - start).days


def _as_date(value) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def coverage_points(rows, *, entry_date) -> list[dict]:
    """逐轮格点，按真实时间升序。

    只收**能归属到轮次**的行（`round_id` 非空）：没有 `round_id` 的旧轮次，
    帧根本挂不到它头上，"这一轮没有帧"会是句假话——那种轮次由调用方计入 `notes`。
    """
    points: list[dict] = []
    for row in rows or []:
        round_id = row.get("round_id")
        ts = row.get("ts")
        if not round_id or not ts:
            continue
        n_frames = row.get("n_frames")
        has_frame = bool(n_frames)
        points.append({
            "ts": ts,
            "round_id": round_id,
            "day": day_of(entry_date, ts),
            "has_frame": has_frame,
            # 缺即缺：没有帧就是 None，**不许**落成 0（spec 硬规则 2）
            "n_frames": int(n_frames) if has_frame else None,
        })
    points.sort(key=lambda p: str(p["ts"]))
    return points


def axis_of(points) -> dict:
    """时间轴 = 网格里第一轮 → 最后一轮的**真实时间戳**（页面按它把 `ts` 映射成 x）。

    空网格 ⇒ `t0`/`t1` 都是 `None`：没有轮次就没有窗口，页面据此说"这批还没有轮次"，
    而不是拿今天凑一个窗口出来。
    """
    stamps = [str(p["ts"]) for p in points or [] if p.get("ts")]
    return {
        "t0": min(stamps) if stamps else None,
        "t1": max(stamps) if stamps else None,
        "n_rounds": len(stamps),
    }

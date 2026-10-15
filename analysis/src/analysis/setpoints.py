"""每日设定末态（票 09）：给定批次/日期窗，回答"那天运行在什么设定上"。

## 为什么读 `/operations` 而不是 `device_setpoint_changes`

Chief 2026-10-13 裁定（ADR-0022）：变更历史的**唯一事件源是 `operations`**（逐条、不聚合），
`device_setpoint_changes` 只作交叉验证。所以本模块只有一条输入：`operations` 行。

## 三个状态必须分得开（票 09 的验收原话）

| 状态 | 含义 | 数据 |
| --- | --- | --- |
| `changed` | **当天有过变更** ⇒ 末态 = 当天最后一条的 `value` | 有 |
| `carried` | 当天没改 ⇒ **沿用**更早那条的值（`since` 指出是哪天的） | 有 |
| `unknown` | **从没见过**这个点位的变更 ⇒ 值写 **NULL** | 无 |

把 `carried` 和 `unknown` 混成一个"没有值"就是把"一直在用某个设定"报成"不知道"——
方向刚好反了，下游会以为这段时间没有任何设定。所以窗口**起点之前**还要取一份基线
（`fetch_latest_operations_before`），否则第一天全是 `unknown`。

## 参数集从哪来

票 09 原文要读 `src/configs/monitoring_points_config.json`；**A′ 实测失败**
（镜像里没有 `utils`/`src`，见票 13 §3.7）⇒ analysis 容器**拿不到那个文件**。
所以 v1 的键集合就用**数据里实际出现过的 `(device_code, point_code)`**
（`operations` 自己就是登记表）——**不另立一份参数清单**（那会变成第二套口径，
和"没扫到的点位看起来像不存在"一个毛病）。等 A′/只读出口落实再对齐配置。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

#: 单次查询最多铺多少天 × 点位（响应体积护栏；超了就报错，不静默截断）
MAX_DAYS = 180

STATE_CHANGED = "changed"
STATE_CARRIED = "carried"
STATE_UNKNOWN = "unknown"


class SetpointError(ValueError):
    """日期/窗口不合法 —— 宁可报错，也不给一份"看起来对"的末态表。"""


def _as_day(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError) as exc:
        raise SetpointError(f"日期不合法：{value!r}（要 YYYY-MM-DD）") from exc


def day_range(start: str, end: str) -> list[str]:
    """`[start, end]` 的日期列表（含两端）；顺序/跨度不合法即报错。"""
    first, last = _as_day(start), _as_day(end)
    if last < first:
        raise SetpointError(f"结束日 {end} 早于起始日 {start}")
    days = (last - first).days + 1
    if days > MAX_DAYS:
        raise SetpointError(f"窗口 {days} 天超过上限 {MAX_DAYS} 天")
    return [(first + timedelta(days=i)).isoformat() for i in range(days)]


def _key(row: dict) -> tuple[str, str]:
    return (str(row.get("device_code") or ""), str(row.get("point_code") or ""))


def daily_states(
    events: list[dict],
    *,
    start: str,
    end: str,
    baseline: list[dict] | None = None,
    entry_date: str | None = None,
) -> dict:
    """事件流 → （天 × 设备 × 点位 → 当日末态）。

    - `events`：`[start, end]` 内的 `operations` 行（顺序无关）。
    - `baseline`：`start` **之前**每个键的最后一条（`fetch_latest_operations_before`）。
    - `entry_date`：批次入库日（ADR-0020 的批次身份）。给了就并列两个生长天数：
      `growth_day`（入库当天 = 0，我们的口径）与 `growth_day_prod`（入库当天 = `01`，生产口径），
      另有 `first_day`（`day == entry_date`）——票 19：**三值并列、绝不取平均、也不合并**。
    - **原始一条不删、不改**：这里只做派生（票 19：首日剔除只发生在统计/学习侧）。
    """
    days = day_range(start, end)
    entry = _as_day(entry_date) if entry_date else None

    # 键集合 = 窗口内的键 ∪ 基线里的键（基线里的点位当天没改也要出现在表里）
    by_key: dict[tuple[str, str], list[dict]] = {}
    meta: dict[tuple[str, str], dict] = {}
    for row in list(baseline or []) + list(events):
        key = _key(row)
        if not key[0] or not key[1]:
            continue
        meta.setdefault(key, {
            "device_code": key[0], "device_name": str(row.get("device_name") or ""),
            "point_code": key[1], "point_name": str(row.get("point_name") or ""),
        })

    for row in events:
        key = _key(row)
        if key in meta:
            by_key.setdefault(key, []).append(row)
    for rows in by_key.values():
        rows.sort(key=lambda r: str(r.get("ts") or ""))

    # 基线：start 之前每个键的最后一个值（"沿用"的来源）；没有基线就是真的未知
    carried: dict[tuple[str, str], dict] = {}
    for row in baseline or []:
        key = _key(row)
        if not key[0] or not key[1]:
            continue
        if key not in carried or str(row.get("ts") or "") >= str(carried[key].get("ts") or ""):
            carried[key] = row

    out: list[dict] = []
    day_index: dict[tuple[str, str], dict] = {}
    for day in days:
        for key in sorted(meta):
            todays = [r for r in by_key.get(key, []) if str(r.get("ts") or "")[:10] == day]
            row: dict = {
                **meta[key],
                "day": day,
                "growth_day": (date.fromisoformat(day) - entry).days if entry else None,
                "growth_day_prod": (
                    f"{(date.fromisoformat(day) - entry).days + 1:02d}" if entry else None
                ),
                "first_day": bool(entry and date.fromisoformat(day) == entry),
                "state": STATE_UNKNOWN,
                "value": None,
                "since": None,
                "since_day": None,
                "operator": None,
                "n_changes_today": len(todays),
            }
            if todays:
                last = todays[-1]
                row.update({
                    "state": STATE_CHANGED,
                    "value": last.get("value"),
                    "since": str(last.get("ts") or ""),
                    "since_day": str(last.get("ts") or "")[:10],
                    "operator": last.get("operator"),
                })
                carried[key] = last
            elif key in carried:
                prev = carried[key]
                row.update({
                    "state": STATE_CARRIED,
                    "value": prev.get("value"),
                    "since": str(prev.get("ts") or ""),
                    "since_day": str(prev.get("ts") or "")[:10],
                    "operator": prev.get("operator"),
                })
            # else: 保持 unknown + value None（**不写 0、不填默认**）
            out.append(row)
            day_index[(key, day)] = row

    summary = {
        "n_rows": len(out),
        "n_changed": sum(1 for r in out if r["state"] == STATE_CHANGED),
        "n_carried": sum(1 for r in out if r["state"] == STATE_CARRIED),
        "n_unknown": sum(1 for r in out if r["state"] == STATE_UNKNOWN),
        "n_days": len(days),
        "n_points": len(meta),
        "n_events": len(events),
        "n_baseline": len(baseline or []),
        "first_day_rows": sum(1 for r in out if r["first_day"]),
    }
    return {"days": days, "rows": out, "summary": summary}


def daily_states_from_db(
    conn, *, zone: str, start: str, end: str, entry_date: str | None = None
) -> dict:
    """从库里读事件 + 基线，跑 `daily_states`（API 与批处理共用这一条路）。

    读经存储层 `analysis.store`（票 28）：这里只认连接，不关心连接是怎么建的。
    """
    from analysis.store import fetch_latest_operations_before, fetch_operations

    events = fetch_operations(conn, zone=zone, start=f"{start}T00:00:00", end=f"{end}T23:59:59")
    baseline = fetch_latest_operations_before(conn, zone=zone, before=f"{start}T00:00:00")
    return daily_states(events, start=start, end=end, baseline=baseline, entry_date=entry_date)

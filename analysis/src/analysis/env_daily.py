"""日分布派生表的重算（票 24 / ADR-0027）。

**表是派生的**：唯一生产者是本模块的 `refresh`，唯一数据源是 `environment` 小时行。
页面只读（`GET /environment/daily`），不在前端重算一遍——一份口径只算一次。

三件事在这里对齐，缺一件页面就画不对：

1. **五数**：由 `analysis.environment.daily_distribution` 纯计算（小时口径 + 采样点级极值）；
2. **批次身份**：`entry_date` / `batch_no` 来自**批次台账**（ADR-0020 的权威身份），
   不是从数据断档里猜的——环境是连续采集的，换批不会让传感器停；
3. **生长天数**：`day - entry_date` 的天数（**入库当天 = 0**，内部口径；界面另并列生产口径）。

台账解不出的库房编号 ⇒ 那几天的 `entry_date` / `growth_day` 一律 NULL，**不猜**。

挂每日任务（`DAILY_AT`，与 `RANGES_AT` / `BATCH_AT` 同一套 `IngestJob` 装配）。
**本批滚动、已结束批次封版**：已结束批次的日行由台账窗口闭合保证不再变——
窗口右端是下一批的入库日，新的环境行落不进旧窗口。
"""

from __future__ import annotations

import argparse
from datetime import date, datetime

from analysis import store
from analysis.batches import dedupe_by_identity
from analysis.environment import daily_distribution
from analysis.rooms import canonical_room, load_room_alias, resolve_rooms
from analysis.schema import (
    DAILY_DISTRIBUTION_PARAMS,
    DAILY_STAT_SUFFIXES,
    DAILY_STATE_NEVER_COMPUTED,
    DAILY_STATE_NO_ENVIRONMENT,
    DAILY_STATE_OK,
)


def _batch_windows(rows: list[dict]) -> dict[str, list[dict]]:
    """台账 → `{规范库房号: [{entry_date, batch_no}, …]（按入库日升序）}`。

    跨域归一与去重复用 `/environment/batches` 那两条（`resolve_rooms` +
    `dedupe_by_identity`）：同一批挂在两种编号下时，按一条算而不是两条。
    """
    ledger, _ = resolve_rooms(rows, label="/environment/daily")
    ledger, _ = dedupe_by_identity(ledger)
    out: dict[str, list[dict]] = {}
    for row in ledger:
        room = row.get("room_id_canonical")
        entry = str(row.get("entry_date") or "")
        if not room or not entry:
            continue
        out.setdefault(room, []).append(
            {"entry_date": entry, "batch_no": row.get("batch_no")}
        )
    for windows in out.values():
        windows.sort(key=lambda w: w["entry_date"])
    return out


def _with_batch(day_row: dict, windows: list[dict]) -> dict:
    """把某一天挂到它所属的批次窗口上（`entry_date <= day < 下一批入库日`）。

    `windows` 按入库日升序；找不到更早的批次 ⇒ 三列全 NULL（"批次前的数据"是
    真实存在的状态，不许拿最近一批去套）。
    """
    day = str(day_row.get("day") or "")
    hit: dict | None = None
    for window in windows:
        if day < window["entry_date"]:
            break
        hit = window
    if hit is None:
        day_row.update({"entry_date": None, "batch_no": None, "growth_day": None})
        return day_row
    day_row["entry_date"] = hit["entry_date"]
    day_row["batch_no"] = hit.get("batch_no")
    day_row["growth_day"] = (
        date.fromisoformat(day) - date.fromisoformat(hit["entry_date"])
    ).days
    return day_row


def refresh(
    conn,
    *,
    computed_at: str | None = None,
    zones: list[str] | None = None,
) -> int:
    """重算 `environment_daily`，返回写入的天数。

    `zones` 是**必须重算的库房**（哪怕这次一天都没算出来也要清掉旧行）；
    不给就按环境表里实际出现的库房。`computed_at` 由调用方给，测试用它钉住时间。
    """
    stamp = computed_at or datetime.now().replace(microsecond=0).isoformat()
    windows = _batch_windows(store.fetch_batches(conn))
    daily = daily_distribution(store.fetch_environment(conn))

    # 台账的键是**规范库房号**，而 `environment.zone` 是入库时的原始编号。两个字符串
    # 直接比，一旦入库用了别名（"A611"）就会**整批静默变成 growth_day=NULL**——
    # 那正是口径 2 要防的"糊"。这里先归一，解不出的**计数打出来**再写 NULL。
    alias = load_room_alias()
    by_zone: dict[str, list[dict]] = {}
    unresolved: dict[str, int] = {}
    for row in daily:
        raw = str(row.get("zone") or "")
        canon = canonical_room(raw, alias=alias)
        if canon is None:
            unresolved[raw] = unresolved.get(raw, 0) + 1
        # 行里的 `zone` 也归一到规范号：否则 DELETE 按规范号删、INSERT 写原始号，
        # 两边对不上就成了每天多一份的重复行
        row["zone"] = canon or raw
        by_zone.setdefault(row["zone"], []).append(row)
    if unresolved:
        print(f"[env_daily] 库房编号解不出（growth_day 写 NULL）：{unresolved}")

    targets = sorted(set(by_zone) | {z for z in (zones or ()) if z})
    total = 0
    for zone in targets:
        days = [_with_batch(dict(row), windows.get(zone, [])) for row in by_zone.get(zone, [])]
        store.replace_environment_daily(conn, days, stamp, zone=zone)
        total += len(days)
    return total


#: 光照一维在日分布行上只带这五个数（制度末态 + 点亮分钟合计），不做箱线。
_LAMP_KEYS = ("lamp_on_min_total", "lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m")

#: 日分布陈旧阈值（小时）。每日任务 24h 跑一次 ⇒ 26h 留 2h 余量（与 `batches_state` 同口径）。
STALE_AFTER_H = 26.0


def state_of(
    rows: list[dict], *, has_environment: bool, now: datetime | None = None
) -> dict:
    """日分布的三态 + `stale` 标注。

    **不许把三个空态合并成「暂无数据」**：
    - `never_computed`：库里**有**小时行，但派生表空 ⇒ 每日任务没跑过（接线问题）；
    - `no_environment`：这段窗口连小时行都没有 ⇒ 数据问题；
    - `stale`：有行、但最近一次重算早于阈值 ⇒ 图能画，但**末几天可能缺**，要说出来。
    """
    if not rows:
        if has_environment:
            return {
                "state": DAILY_STATE_NEVER_COMPUTED,
                "state_text": "日分布还没有重算过（每日任务未接线或从未成功跑过）",
                "flags": [],
                "computed_at": None,
            }
        return {
            "state": DAILY_STATE_NO_ENVIRONMENT,
            "state_text": "这段窗口里没有环境数据（入库作业未跑，或数据还没到）",
            "flags": [],
            "computed_at": None,
        }
    stamps = [str(r.get("computed_at") or "") for r in rows if r.get("computed_at")]
    computed_at = max(stamps) if stamps else None
    flags: list[str] = []
    text = "日分布已重算"
    if computed_at:
        moment = now or datetime.now()
        try:
            age_h = (moment - datetime.fromisoformat(computed_at)).total_seconds() / 3600
        except ValueError:
            age_h = 0.0
        if age_h > STALE_AFTER_H:
            flags.append("stale")
            text = f"日分布最近一次重算在 {computed_at[:16]}（已 {age_h:.0f} 小时），末几天可能缺"
    return {"state": DAILY_STATE_OK, "state_text": text, "flags": flags,
            "computed_at": computed_at}


def api_rows(rows: list[dict]) -> list[dict]:
    """库行 → 接口行：把二十几个平铺列收成 `params` 字典，省得页面拼列名。

    `growth_day_prod`（入库当天 = 01）与 `growth_day`（= 0）**并列给出**，
    与 `/setpoints/daily` 同一套口径（票 19：两套口径并列、不平均、不合并）。
    """
    out: list[dict] = []
    for row in rows:
        growth_day = row.get("growth_day")
        params: dict[str, dict] = {}
        for col in DAILY_DISTRIBUTION_PARAMS:
            item = {suffix: row.get(f"{col}_{suffix}") for suffix in DAILY_STAT_SUFFIXES}
            item["n"] = row.get(f"{col}_n")
            params[col] = item
        out.append({
            "day": row.get("day"),
            "zone": row.get("zone"),
            "entry_date": row.get("entry_date"),
            "batch_no": row.get("batch_no"),
            "growth_day": growth_day,
            "growth_day_prod": None if growth_day is None else growth_day + 1,
            "hours": row.get("hours"),
            "samples": row.get("samples"),
            "params": params,
            "lamp": {key: row.get(key) for key in _LAMP_KEYS},
            "computed_at": row.get("computed_at"),
        })
    return out


def main(argv: list[str] | None = None) -> None:
    """一次性补数入口（容器的 `daily-refresh` 角色）：重算一遍就退出。

    派生表从没算过时（`state=never_computed`）用它，不必等隔天的每日任务。
    写的是**派生表**：按库房整删整写，反复跑结果一致。缺省重算环境表里出现的
    **全部**库房（补本批次时也就顺带把 611 补了）；`--zone` 是**额外**强制清空
    重算的库房（哪怕它这会儿一天数据都没有），语义与 `refresh(zones=…)` 一致。
    """
    ap = argparse.ArgumentParser(description="日分布派生表重算（票 24 / ADR-0027）")
    ap.add_argument("--dsn", help=store.CLI_DSN_HELP)
    ap.add_argument("--schema", help=store.CLI_SCHEMA_HELP)
    ap.add_argument(
        "--zone", action="append", dest="zones",
        help="额外强制清空重算的库房（可重复）；缺省按环境表里出现的库房",
    )
    ap.add_argument("--computed-at", help="钉住写入的 computed_at（补历史数据时对齐时间用）")
    args = ap.parse_args(argv)

    conn = store.connect(dsn=args.dsn, schema=args.schema)
    n = refresh(conn, computed_at=args.computed_at, zones=args.zones)
    print(f"[env_daily] 重算完成，{n} 个日分布行（schema {conn.schema}）")


if __name__ == "__main__":
    main()

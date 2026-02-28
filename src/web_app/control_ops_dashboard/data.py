from __future__ import annotations

from datetime import date, datetime

import pandas as pd
from sqlalchemy import desc, func
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
from utils.create_table import DeviceSetpointChange, MushroomBatchYield

Session = sessionmaker(bind=pgsql_engine)


def load_room_list_from_yield() -> list[str]:
    with Session() as session:
        rooms = (
            session.query(MushroomBatchYield.room_id)
            .filter(MushroomBatchYield.room_id.isnot(None))
            .distinct()
            .order_by(MushroomBatchYield.room_id)
            .all()
        )
    return [room_id for (room_id,) in rooms]


def load_batch_windows_from_yield(room_ids: list[str] | None = None) -> pd.DataFrame:
    with Session() as session:
        query = session.query(
            MushroomBatchYield.room_id,
            MushroomBatchYield.in_date,
            func.min(MushroomBatchYield.stat_date).label("min_date"),
            func.max(MushroomBatchYield.stat_date).label("max_date"),
        ).filter(MushroomBatchYield.room_id.isnot(None))

        if room_ids:
            query = query.filter(MushroomBatchYield.room_id.in_(room_ids))

        query = query.group_by(MushroomBatchYield.room_id, MushroomBatchYield.in_date)
        df = pd.read_sql(query.statement, session.bind)

    if df.empty:
        return df

    df["in_date"] = pd.to_datetime(df["in_date"], errors="coerce").dt.date
    df["min_date"] = pd.to_datetime(df["min_date"], errors="coerce").dt.date
    df["max_date"] = pd.to_datetime(df["max_date"], errors="coerce").dt.date
    df["min_date"] = df["min_date"].fillna(df["in_date"])
    df["max_date"] = df["max_date"].fillna(df["in_date"])
    df["start_time"] = pd.to_datetime(
        df["min_date"].astype(str) + " 00:00:00", errors="coerce"
    )
    df["end_time"] = pd.to_datetime(
        df["max_date"].astype(str) + " 23:59:59", errors="coerce"
    )
    df["batch_key"] = df["room_id"].astype(str) + "|" + df["in_date"].astype(str)
    return df.sort_values(["room_id", "in_date"], ascending=[True, False])


def load_device_setpoint_changes(
    room_ids: list[str],
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    in_dates: list[date] | None = None,
) -> pd.DataFrame:
    if not room_ids:
        return pd.DataFrame()
    with Session() as session:
        query = session.query(
            DeviceSetpointChange.id,
            DeviceSetpointChange.room_id,
            DeviceSetpointChange.device_type,
            DeviceSetpointChange.device_name,
            DeviceSetpointChange.point_name,
            DeviceSetpointChange.point_description,
            DeviceSetpointChange.change_time,
            DeviceSetpointChange.previous_value,
            DeviceSetpointChange.current_value,
            DeviceSetpointChange.change_type,
            DeviceSetpointChange.in_date,
            DeviceSetpointChange.growth_day,
            DeviceSetpointChange.in_num,
            DeviceSetpointChange.batch_id,
        ).filter(DeviceSetpointChange.room_id.in_(room_ids))
        if in_dates:
            query = query.filter(DeviceSetpointChange.in_date.in_(in_dates))
        if start_time:
            query = query.filter(DeviceSetpointChange.change_time >= start_time)
        if end_time:
            query = query.filter(DeviceSetpointChange.change_time <= end_time)
        query = query.order_by(desc(DeviceSetpointChange.change_time))
        return pd.read_sql(query.statement, session.bind)


def infer_point_group(
    device_type: str | None, point_name: str | None, point_desc: str | None
) -> str:
    base = (point_desc or point_name or "").strip()
    if base:
        return base
    if device_type and point_name:
        return f"{device_type}/{point_name}"
    return point_name or device_type or "unknown"


def attach_batch_and_growth_day(
    changes: pd.DataFrame,
    windows: pd.DataFrame,
) -> pd.DataFrame:
    if changes is None or changes.empty or windows is None or windows.empty:
        return pd.DataFrame()

    df = changes.copy()
    df["change_time"] = pd.to_datetime(df["change_time"], errors="coerce")
    df = df.dropna(subset=["change_time"])
    if "in_date" in df.columns:
        df["in_date"] = pd.to_datetime(df["in_date"], errors="coerce").dt.date

    w = windows[["room_id", "in_date", "start_time", "end_time", "batch_key"]].copy()
    w["in_date"] = pd.to_datetime(w["in_date"], errors="coerce").dt.date
    w["start_time"] = pd.to_datetime(w["start_time"], errors="coerce")
    w["end_time"] = pd.to_datetime(w["end_time"], errors="coerce")

    has_in_date = "in_date" in df.columns and df["in_date"].notna().any()
    if has_in_date:
        merged = df.merge(w, on=["room_id", "in_date"], how="left")
    else:
        merged = df.merge(w, on="room_id", how="inner")

    merged = merged[
        merged["start_time"].isna()
        | merged["end_time"].isna()
        | (
            (merged["change_time"] >= merged["start_time"])
            & (merged["change_time"] <= merged["end_time"])
        )
    ]

    if "in_date" not in merged.columns:
        merged["in_date"] = merged.get("in_date_x")
        merged["in_date"] = merged["in_date"].fillna(merged.get("in_date_y"))
    elif merged["in_date"].isna().all():
        merged["in_date"] = merged["in_date"].fillna(merged.get("in_date_y"))
    in_date_ts = pd.to_datetime(merged.get("in_date"), errors="coerce")
    change_time_ts = pd.to_datetime(merged.get("change_time"), errors="coerce")
    inferred_growth_day = (
        change_time_ts.dt.normalize() - in_date_ts.dt.normalize()
    ).dt.days + 1

    if "growth_day" not in merged.columns:
        merged["growth_day"] = inferred_growth_day
    else:
        merged["growth_day"] = pd.to_numeric(merged["growth_day"], errors="coerce")
        merged["growth_day"] = merged["growth_day"].fillna(inferred_growth_day)

    merged = merged[merged["growth_day"].notna()].copy()
    merged["growth_day"] = merged["growth_day"].astype(int)
    merged["batch_key"] = (
        merged["room_id"].astype(str) + "|" + merged["in_date"].astype(str)
    )

    merged["point_group"] = merged.apply(
        lambda r: infer_point_group(
            r.get("device_type"), r.get("point_name"), r.get("point_description")
        ),
        axis=1,
    )
    return merged


def load_control_history_with_growth_day(
    room_ids: list[str] | None = None,
    batch_keys: list[str] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> pd.DataFrame:
    """从数据库读取历史调控数据，并与批次、生长天数建立稳定关联。"""
    rooms = room_ids or load_room_list_from_yield()
    if not rooms:
        return pd.DataFrame()

    windows = load_batch_windows_from_yield(rooms)
    if windows is None or windows.empty:
        return pd.DataFrame()

    if batch_keys:
        windows = windows[windows["batch_key"].isin(batch_keys)].copy()
        if windows.empty:
            return pd.DataFrame()

    in_dates = (
        pd.to_datetime(windows["in_date"], errors="coerce")
        .dt.date.dropna()
        .drop_duplicates()
        .tolist()
    )

    raw_changes = load_device_setpoint_changes(
        room_ids=sorted(windows["room_id"].dropna().astype(str).unique().tolist()),
        start_time=start_time,
        end_time=end_time,
        in_dates=in_dates,
    )
    if raw_changes is None or raw_changes.empty:
        return pd.DataFrame()

    merged = attach_batch_and_growth_day(raw_changes, windows)
    if merged.empty:
        return pd.DataFrame()

    merged["growth_stage"] = pd.cut(
        pd.to_numeric(merged["growth_day"], errors="coerce"),
        bins=[-float("inf"), 7, 14, 21, 28, float("inf")],
        labels=["D1-D7", "D8-D14", "D15-D21", "D22-D28", "D29+"],
    )

    return merged.sort_values(
        ["room_id", "batch_key", "growth_day", "change_time"],
        ascending=[True, True, True, True],
    ).reset_index(drop=True)

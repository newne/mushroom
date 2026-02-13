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

    w = windows[["room_id", "in_date", "start_time", "end_time", "batch_key"]].copy()
    w["start_time"] = pd.to_datetime(w["start_time"], errors="coerce")
    w["end_time"] = pd.to_datetime(w["end_time"], errors="coerce")

    has_in_date = "in_date" in df.columns and df["in_date"].notna().any()
    if has_in_date:
        merged = df.merge(w, on=["room_id", "in_date"], how="left")
    else:
        merged = df.merge(w, on="room_id", how="inner")
        merged = merged[
            (merged["change_time"] >= merged["start_time"])
            & (merged["change_time"] <= merged["end_time"])
        ]

    if "in_date" not in merged.columns:
        merged["in_date"] = merged.get("in_date_x")
        merged["in_date"] = merged["in_date"].fillna(merged.get("in_date_y"))
    elif merged["in_date"].isna().all():
        merged["in_date"] = merged["in_date"].fillna(merged.get("in_date_y"))
    if "growth_day" not in merged.columns or merged["growth_day"].isna().all():
        merged["growth_day"] = (
            merged["change_time"].dt.date - merged["in_date"]
        ).apply(lambda d: d.days + 1)
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

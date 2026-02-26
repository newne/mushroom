#!/usr/bin/env python3
"""按时间窗回填设定点变更，并在入库前去重。"""

from __future__ import annotations

import argparse
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
from monitoring.tasks import (
    get_static_configs_from_database,
    group_configs_by_room,
    monitor_room_with_static_configs,
    store_setpoint_changes_to_database,
)
from utils.loguru_setting import logger
from utils.create_table import DeviceSetpointChange


_KEY_COLUMNS = [
    "room_id",
    "device_name",
    "point_name",
    "change_time",
    "previous_value",
    "current_value",
]


def _normalize_for_key(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        return out
    out["change_time"] = pd.to_datetime(out["change_time"], errors="coerce")
    for col in ["previous_value", "current_value"]:
        out[col] = pd.to_numeric(out[col], errors="coerce").round(6)
    for col in ["room_id", "device_name", "point_name"]:
        out[col] = out[col].astype(str)
    return out


def _build_key_set(df: pd.DataFrame) -> set[tuple[Any, ...]]:
    if df.empty:
        return set()
    key_df = _normalize_for_key(df)[_KEY_COLUMNS].dropna(subset=["change_time"])
    return set(key_df.itertuples(index=False, name=None))


def _query_existing_changes(
    start_time: datetime,
    end_time: datetime,
    room_ids: list[str],
) -> pd.DataFrame:
    Session = sessionmaker(bind=pgsql_engine)
    with Session() as session:
        query = session.query(
            DeviceSetpointChange.room_id,
            DeviceSetpointChange.device_name,
            DeviceSetpointChange.point_name,
            DeviceSetpointChange.change_time,
            DeviceSetpointChange.previous_value,
            DeviceSetpointChange.current_value,
        ).filter(
            DeviceSetpointChange.change_time >= start_time,
            DeviceSetpointChange.change_time <= end_time,
        )

        if room_ids:
            query = query.filter(DeviceSetpointChange.room_id.in_(room_ids))

        return pd.read_sql(query.statement, session.bind)


def backfill_setpoint_changes(
    start_time: datetime,
    end_time: datetime,
    room_ids: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    room_ids = room_ids or []

    static_configs = get_static_configs_from_database()
    configs_by_room = group_configs_by_room(static_configs)

    if room_ids:
        room_id_set = set(room_ids)
        configs_by_room = {
            room: cfg for room, cfg in configs_by_room.items() if room in room_id_set
        }

    total_detected = 0
    all_changes: list[dict[str, Any]] = []

    for room_id, room_configs in configs_by_room.items():
        logger.info(
            f"[BACKFILL] 检测库房 {room_id}，时间范围 {start_time} ~ {end_time}"
        )
        room_changes = monitor_room_with_static_configs(
            room_id=room_id,
            room_configs=room_configs,
            start_time=start_time,
            end_time=end_time,
        )
        total_detected += len(room_changes)
        all_changes.extend(room_changes)

    if not all_changes:
        return {
            "total_rooms": len(configs_by_room),
            "total_detected": 0,
            "already_exists": 0,
            "to_insert": 0,
            "inserted": 0,
            "dry_run": dry_run,
        }

    new_df = pd.DataFrame(all_changes)
    new_df = _normalize_for_key(new_df)

    existing_df = _query_existing_changes(
        start_time=start_time,
        end_time=end_time,
        room_ids=list(configs_by_room.keys()),
    )
    existing_keys = _build_key_set(existing_df)

    new_df = new_df.dropna(subset=["change_time"])
    new_df["_dedup_key"] = list(new_df[_KEY_COLUMNS].itertuples(index=False, name=None))
    new_df = new_df.drop_duplicates(subset=["_dedup_key"], keep="first")

    is_new_mask = ~new_df["_dedup_key"].isin(existing_keys)
    to_insert_df = new_df[is_new_mask].drop(columns=["_dedup_key"])

    if to_insert_df.empty:
        return {
            "total_rooms": len(configs_by_room),
            "total_detected": int(total_detected),
            "already_exists": int(len(new_df)),
            "to_insert": 0,
            "inserted": 0,
            "dry_run": dry_run,
        }

    inserted = 0
    if not dry_run:
        inserted = store_setpoint_changes_to_database(to_insert_df.to_dict("records"))

    return {
        "total_rooms": len(configs_by_room),
        "total_detected": int(total_detected),
        "already_exists": int(len(new_df) - len(to_insert_df)),
        "to_insert": int(len(to_insert_df)),
        "inserted": int(inserted),
        "dry_run": dry_run,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回填设定点变更（含去重）")
    parser.add_argument(
        "--start-time",
        required=True,
        help="开始时间，格式: YYYY-MM-DD HH:MM:SS",
    )
    parser.add_argument(
        "--end-time",
        default=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        help="结束时间，格式: YYYY-MM-DD HH:MM:SS，默认当前时间",
    )
    parser.add_argument(
        "--rooms",
        nargs="*",
        default=[],
        help="可选：仅回填指定库房，例如 --rooms 607 608",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅计算不入库",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    start_time = datetime.strptime(args.start_time, "%Y-%m-%d %H:%M:%S")
    end_time = datetime.strptime(args.end_time, "%Y-%m-%d %H:%M:%S")

    if start_time >= end_time:
        raise ValueError("start_time 必须早于 end_time")

    result = backfill_setpoint_changes(
        start_time=start_time,
        end_time=end_time,
        room_ids=args.rooms,
        dry_run=args.dry_run,
    )
    logger.info(f"[BACKFILL] 完成: {result}")
    print(result)


if __name__ == "__main__":
    main()

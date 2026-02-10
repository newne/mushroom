#!/usr/bin/env python3
"""Backfill missing mushroom_env_daily_stats records."""

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd
from sqlalchemy import text

src_dir = Path(__file__).resolve().parents[2]
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from global_const.global_const import BASE_DIR, ensure_src_path, pgsql_engine

ensure_src_path()

from environment.processor import (
    calculate_env_statistics,
    fill_in_day_num_sequence,
    get_room_env_data,
    get_room_mushroom_info,
    store_env_statistics,
)
from global_const.const_config import MUSHROOM_ROOM_IDS
from utils.loguru_setting import logger


def _date_range(start_date: date, end_date: date) -> List[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=i) for i in range(days + 1)]


def _ensure_remark_column(table_name: str) -> None:
    with pgsql_engine.connect() as conn:
        conn.execute(
            text(
                f"""
                ALTER TABLE {table_name}
                ADD COLUMN IF NOT EXISTS remark TEXT
                """
            )
        )
        conn.commit()


def _prepare_backup_table(backup_table: str) -> None:
    with pgsql_engine.connect() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {backup_table}
                (LIKE mushroom_env_daily_stats INCLUDING ALL)
                """
            )
        )
        conn.commit()

    _ensure_remark_column(backup_table)


def _backup_existing_data(backup_table: str, start_date: date, end_date: date) -> int:
    with pgsql_engine.connect() as conn:
        result = conn.execute(
            text(
                f"""
                INSERT INTO {backup_table}
                SELECT * FROM mushroom_env_daily_stats
                WHERE stat_date BETWEEN :start_date AND :end_date
                AND NOT EXISTS (
                    SELECT 1 FROM {backup_table} b
                    WHERE b.id = mushroom_env_daily_stats.id
                )
                """
            ),
            {"start_date": start_date, "end_date": end_date},
        )
        conn.commit()
    return result.rowcount or 0


def _fetch_existing_stats(
    room_id: str, start_date: date, end_date: date
) -> pd.DataFrame:
    query = text(
        """
    SELECT
        room_id,
        stat_date,
        temp_median,
        temp_min,
        temp_max,
        temp_q25,
        temp_q75,
        temp_count,
        humidity_median,
        humidity_min,
        humidity_max,
        humidity_q25,
        humidity_q75,
        humidity_count,
        co2_median,
        co2_min,
        co2_max,
        co2_q25,
        co2_q75,
        co2_count,
        in_day_num,
        is_growth_phase,
        remark
    FROM mushroom_env_daily_stats
    WHERE room_id = :room_id
    AND stat_date BETWEEN :start_date AND :end_date
    ORDER BY stat_date
    """
    )
    return pd.read_sql(
        query,
        pgsql_engine,
        params={"room_id": room_id, "start_date": start_date, "end_date": end_date},
    )


def _interpolate_series(series: pd.Series, all_dates: List[date]) -> pd.Series:
    series = series.reindex(all_dates)
    numeric = pd.to_numeric(series, errors="coerce")
    interpolated = numeric.interpolate(method="linear", limit_direction="both")

    if interpolated.isna().any():
        seasonal_mean = numeric.groupby(numeric.index.strftime("%m-%d")).transform(
            "mean"
        )
        interpolated = interpolated.fillna(seasonal_mean)

    if interpolated.isna().any():
        interpolated = interpolated.fillna(numeric.mean())

    return interpolated


def _summarize_table(
    room_ids: List[str], start_date: date, end_date: date
) -> Dict[str, float]:
    query = """
    SELECT
        COUNT(*) AS total_rows,
        AVG(temp_median) AS avg_temp,
        AVG(humidity_median) AS avg_humidity,
        AVG(co2_median) AS avg_co2
    FROM mushroom_env_daily_stats
    WHERE room_id = ANY(:rooms)
    AND stat_date BETWEEN :start_date AND :end_date
    """
    with pgsql_engine.connect() as conn:
        result = conn.execute(
            text(query),
            {"rooms": room_ids, "start_date": start_date, "end_date": end_date},
        ).fetchone()
    if not result:
        return {"total_rows": 0, "avg_temp": 0, "avg_humidity": 0, "avg_co2": 0}
    return {
        "total_rows": int(result[0] or 0),
        "avg_temp": float(result[1] or 0),
        "avg_humidity": float(result[2] or 0),
        "avg_co2": float(result[3] or 0),
    }


def backfill_env_daily_stats(start_date: date, end_date: date) -> Dict[str, object]:
    report: Dict[str, object] = {
        "start_date": str(start_date),
        "end_date": str(end_date),
        "rooms": MUSHROOM_ROOM_IDS,
        "expected_missing": 0,
        "inserted": 0,
        "in_day_num_anomalies": [],
        "missing_dates": [],
        "raw_dates": [],
        "interpolated_dates": [],
    }

    _ensure_remark_column("mushroom_env_daily_stats")
    backup_table = f"mushroom_env_daily_stats_bak_{datetime.now().strftime('%Y%m%d')}"
    _prepare_backup_table(backup_table)
    backup_rows = _backup_existing_data(backup_table, start_date, end_date)
    report["backup_rows"] = backup_rows

    before_summary = _summarize_table(MUSHROOM_ROOM_IDS, start_date, end_date)

    all_dates = _date_range(start_date, end_date)

    for room_id in MUSHROOM_ROOM_IDS:
        existing_df = _fetch_existing_stats(room_id, start_date, end_date)
        existing_dates = (
            set(existing_df["stat_date"].tolist()) if not existing_df.empty else set()
        )
        missing_dates = [d for d in all_dates if d not in existing_dates]
        report["expected_missing"] += len(missing_dates)

        if not missing_dates:
            continue

        computed_records: List[Dict[str, object]] = []
        interpolated_targets: List[date] = []

        for stat_date in missing_dates:
            env_data = get_room_env_data(room_id, stat_date)
            if env_data.empty:
                interpolated_targets.append(stat_date)
                continue

            info = get_room_mushroom_info(room_id, stat_date)
            stats = calculate_env_statistics(
                env_data, in_day_num=info.get("in_day_num")
            )
            stats["remark"] = None
            computed_records.append({"stat_date": stat_date, **stats})
            report["raw_dates"].append(str(stat_date))

        if computed_records:
            computed_df = pd.DataFrame(computed_records)
            computed_df["room_id"] = room_id
            existing_df = pd.concat([existing_df, computed_df], ignore_index=True)
            existing_df = existing_df.drop_duplicates(
                subset=["room_id", "stat_date"], keep="last"
            )

        if interpolated_targets:
            report["interpolated_dates"].extend([str(d) for d in interpolated_targets])

        existing_df = (
            existing_df.set_index("stat_date")
            if not existing_df.empty
            else pd.DataFrame(index=all_dates)
        )

        in_day_values = [
            int(val) if pd.notna(val) else None
            for val in existing_df.reindex(all_dates)
            .get("in_day_num", pd.Series(index=all_dates))
            .tolist()
        ]
        filled_in_day, anomalies = fill_in_day_num_sequence(all_dates, in_day_values)
        report["in_day_num_anomalies"].extend(
            [
                {
                    "room_id": room_id,
                    "stat_date": str(item["stat_date"]),
                    "expected": item["expected"],
                    "actual": item["actual"],
                }
                for item in anomalies
            ]
        )

        metric_columns = [
            "temp_median",
            "temp_min",
            "temp_max",
            "temp_q25",
            "temp_q75",
            "temp_count",
            "humidity_median",
            "humidity_min",
            "humidity_max",
            "humidity_q25",
            "humidity_q75",
            "humidity_count",
            "co2_median",
            "co2_min",
            "co2_max",
            "co2_q25",
            "co2_q75",
            "co2_count",
        ]

        interpolated_series: Dict[str, pd.Series] = {}
        for col in metric_columns:
            if col not in existing_df.columns:
                continue
            interpolated_series[col] = _interpolate_series(existing_df[col], all_dates)

        for stat_date in interpolated_targets:
            idx = all_dates.index(stat_date)
            stats: Dict[str, object] = {
                "in_day_num": filled_in_day[idx],
                "remark": "interpolated",
            }
            for col, series in interpolated_series.items():
                value = series.iloc[idx]
                if col.endswith("_count") and pd.notna(value):
                    stats[col] = int(round(value))
                else:
                    stats[col] = float(value) if pd.notna(value) else None

            if stats.get("in_day_num") is None:
                stats["is_growth_phase"] = True
            else:
                stats["is_growth_phase"] = bool(1 <= int(stats["in_day_num"]) <= 27)

            record_count = store_env_statistics(room_id, stat_date, stats)
            report["inserted"] += record_count

        for record in computed_records:
            idx = all_dates.index(record["stat_date"])
            if record.get("in_day_num") is None:
                record["in_day_num"] = filled_in_day[idx]
            if record.get("is_growth_phase") is None:
                record["is_growth_phase"] = bool(
                    record["in_day_num"] and 1 <= int(record["in_day_num"]) <= 27
                )
            record_count = store_env_statistics(room_id, record["stat_date"], record)
            report["inserted"] += record_count

        if interpolated_targets:
            report["missing_dates"].extend([str(d) for d in interpolated_targets])

    after_summary = _summarize_table(MUSHROOM_ROOM_IDS, start_date, end_date)
    report["before_summary"] = before_summary
    report["after_summary"] = after_summary

    output_dir = BASE_DIR / "reports" / "maintenance"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = (
        output_dir
        / f"env_daily_stats_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2))
    logger.info(f"Backfill report saved to {report_path}")

    return report


def main() -> None:
    start_date = date(2025, 12, 22)
    end_date = date.today() - timedelta(days=1)

    if end_date < start_date:
        logger.info("No backfill needed: end_date before start_date")
        return

    report = backfill_env_daily_stats(start_date, end_date)
    logger.info(
        "Backfill done. expected_missing=%s inserted=%s anomalies=%s",
        report.get("expected_missing"),
        report.get("inserted"),
        len(report.get("in_day_num_anomalies", [])),
    )


if __name__ == "__main__":
    main()

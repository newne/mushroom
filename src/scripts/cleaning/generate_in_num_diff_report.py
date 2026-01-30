"""Generate diff report for in_num=0 records."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
from loguru import logger
from sqlalchemy import func
from sqlalchemy.orm import sessionmaker

current_file = Path(__file__).resolve()
src_dir = current_file.parent.parent.parent
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from global_const.global_const import BASE_DIR, pgsql_engine
from utils.create_table import MushroomImageEmbedding
from utils.data_preprocessing import query_data_by_batch_time
from utils.dataframe_utils import get_all_device_configs


def parse_env_sensor_status(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def load_historical_mode(session) -> Dict[Tuple[str, datetime.date], int]:
    query = (
        session.query(
            MushroomImageEmbedding.room_id,
            MushroomImageEmbedding.in_date,
            MushroomImageEmbedding.in_num,
        )
        .filter(MushroomImageEmbedding.in_num.isnot(None))
        .filter(MushroomImageEmbedding.in_num > 0)
    )
    df = pd.read_sql(query.statement, session.bind)
    if df.empty:
        return {}
    grouped = (
        df.groupby(["room_id", "in_date"])["in_num"]
        .agg(lambda s: s.value_counts().idxmax())
    )
    return {(row[0], row[1]): int(row[2]) for row in grouped.reset_index().itertuples(index=False)}


def fetch_history_in_day_num(cache: Dict[Tuple[str, datetime.date], Optional[int]], room_id: str, in_date) -> Optional[int]:
    cache_key = (room_id, in_date)
    if cache_key in cache:
        return cache[cache_key]

    try:
        configs = get_all_device_configs(room_id)
        all_query_df = pd.concat(configs.values(), ignore_index=True) if configs else pd.DataFrame()
    except Exception as exc:
        logger.warning(f"history config failed room={room_id}: {exc}")
        cache[cache_key] = None
        return None

    if all_query_df.empty:
        cache[cache_key] = None
        return None

    if "point_alias" in all_query_df.columns:
        query_df = all_query_df[all_query_df["point_alias"] == "in_day_num"].copy()
    else:
        query_df = all_query_df[all_query_df["point_name"].isin(["InDayNum", "in_day_num"])].copy()

    if query_df.empty:
        cache[cache_key] = None
        return None

    start_time = datetime.combine(in_date, datetime.min.time())
    end_time = start_time + timedelta(days=1)

    frames = []
    for device_alias in query_df["device_alias"].unique():
        device_config_df = query_df[query_df["device_alias"] == device_alias].copy()
        try:
            data = query_data_by_batch_time(device_config_df, start_time, end_time)
            if not data.empty:
                frames.append(data)
        except Exception as exc:
            logger.warning(f"history query failed room={room_id} device={device_alias}: {exc}")
            continue

    if not frames:
        cache[cache_key] = None
        return None

    history_df = pd.concat(frames, axis=0, ignore_index=True)
    if "value" not in history_df.columns:
        cache[cache_key] = None
        return None

    values = pd.to_numeric(history_df["value"], errors="coerce").dropna()
    values = values[values > 0]
    if values.empty:
        cache[cache_key] = None
        return None

    mode_value = int(values.value_counts().idxmax())
    cache[cache_key] = mode_value
    return mode_value


def main():
    output_dir = BASE_DIR / "reports" / "cleaning" / datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / "in_num_zero_diff.csv"

    Session = sessionmaker(bind=pgsql_engine)
    history_cache: Dict[Tuple[str, datetime.date], Optional[int]] = {}

    with Session() as session:
        history_mode = load_historical_mode(session)
        records = (
            session.query(MushroomImageEmbedding)
            .filter(MushroomImageEmbedding.in_num == 0)
            .order_by(MushroomImageEmbedding.room_id, MushroomImageEmbedding.in_date)
            .all()
        )

        rows = []
        for record in records:
            env = parse_env_sensor_status(record.env_sensor_status)
            env_in_day_num = env.get("in_day_num")

            hist_mode = history_mode.get((record.room_id, record.in_date))
            actual_mode = None
            if record.room_id and record.in_date:
                actual_mode = fetch_history_in_day_num(history_cache, record.room_id, record.in_date)

            match = actual_mode is not None and hist_mode is not None and actual_mode == hist_mode
            rows.append(
                {
                    "id": str(record.id),
                    "room_id": record.room_id,
                    "in_date": record.in_date,
                    "in_num": record.in_num,
                    "history_mode": hist_mode,
                    "history_actual": actual_mode,
                    "env_in_day_num": env_in_day_num,
                    "match": match,
                    "image_path": record.image_path,
                }
            )

    df = pd.DataFrame(rows)
    df.to_csv(output_file, index=False)
    logger.info(f"Diff report saved to {output_file}")


if __name__ == "__main__":
    main()

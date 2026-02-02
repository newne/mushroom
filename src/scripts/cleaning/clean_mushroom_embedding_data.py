"""
MushroomImageEmbedding 数据清洗与标准化脚本
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


IMAGE_PATH_PATTERN = re.compile(
    r"^(?P<room>[^_]+)_(?P<ip>[^_]+)_(?P<date>\d{6,8})_(?P<time>\d{14})",
    re.IGNORECASE,
)


class MushroomEmbeddingDataCleaner:
    def __init__(self, db_engine, batch_size: int = 1000, dry_run: bool = False):
        self.db_engine = db_engine
        self.Session = sessionmaker(bind=db_engine)
        self.batch_size = batch_size
        self.dry_run = dry_run

        self.stats = defaultdict(int)
        self.field_fix_counts = defaultdict(int)
        self.error_records: List[Dict[str, Any]] = []
        self.fix_samples: List[Dict[str, Any]] = []
        self.null_in_num_records: List[Dict[str, Any]] = []
        self.null_growth_day_records: List[Dict[str, Any]] = []
        self.zero_in_num_fixed_records: List[Dict[str, Any]] = []
        self.history_in_day_num_cache: Dict[Tuple[str, datetime.date], Optional[int]] = {}

    def _normalize_collection_date(self, collection_date: str, detailed_time: str) -> str:
        """
        标准化采集日期格式，使用详细时间进行推断验证
        """
        try:
            ref_dt = datetime.strptime(detailed_time, "%Y%m%d%H%M%S")
        except ValueError as exc:
            raise ValueError(f"参考时间格式无效: {detailed_time}") from exc

        date_len = len(collection_date)
        candidates: List[datetime] = []

        if date_len == 8:
            try:
                dt = datetime.strptime(collection_date, "%Y%m%d")
                candidates.append(dt)
            except ValueError:
                pass
        else:
            if date_len not in [6, 7]:
                raise ValueError(f"不支持的日期长度: {date_len}")

            year_str = collection_date[:4]
            remainder = collection_date[4:]
            potential_dates = []

            if date_len == 6:
                potential_dates.append(f"{year_str}-{remainder[0]}-{remainder[1]}")
            elif date_len == 7:
                potential_dates.append(f"{year_str}-{remainder[:1]}-{remainder[1:]}")
                potential_dates.append(f"{year_str}-{remainder[:2]}-{remainder[2:]}")

            for date_str in potential_dates:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    candidates.append(dt)
                except ValueError:
                    continue

        valid_candidates = []
        for dt in candidates:
            if dt.date() > ref_dt.date():
                continue
            if dt.date() < (ref_dt - timedelta(days=30)).date():
                continue
            valid_candidates.append(dt)

        if not valid_candidates:
            if candidates:
                raise ValueError(
                    f"日期 {collection_date} 与采集时间 {detailed_time} 不一致"
                )
            raise ValueError(f"无法解析日期格式: {collection_date}")

        best_candidate = max(valid_candidates, key=lambda x: x)
        return best_candidate.strftime("%Y%m%d")

    def _extract_date_from_path(self, image_path: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        if not image_path:
            return None, None, None

        file_name = Path(image_path).name
        match = IMAGE_PATH_PATTERN.search(file_name)
        if not match:
            return None, None, None

        return match.group("date"), match.group("time"), match.group("ip")

    def _parse_env_sensor_status(self, env_sensor_status: Any) -> Dict[str, Any]:
        if env_sensor_status is None:
            return {}
        if isinstance(env_sensor_status, dict):
            return env_sensor_status
        if isinstance(env_sensor_status, str):
            try:
                return json.loads(env_sensor_status)
            except json.JSONDecodeError:
                return {}
        return {}

    def _record_error(self, record_id: str, error_type: str, reason: str, image_path: str):
        self.error_records.append(
            {
                "id": record_id,
                "error_type": error_type,
                "reason": reason,
                "image_path": image_path,
            }
        )
        self.stats[f"error_{error_type}"] += 1

    def _load_in_num_stats(self, session) -> Dict[str, Tuple[float, float]]:
        query = (
            session.query(MushroomImageEmbedding.room_id, MushroomImageEmbedding.in_num)
            .filter(MushroomImageEmbedding.in_num.isnot(None))
            .filter(MushroomImageEmbedding.in_num > 0)
        )
        df = pd.read_sql(query.statement, session.bind)
        if df.empty:
            return {}
        stats = (
            df.groupby("room_id")["in_num"]
            .agg(["mean", "std"])
            .fillna(0)
        )
        return {row[0]: (row[1], row[2]) for row in stats.reset_index().itertuples(index=False)}

    def _load_historical_in_num(self, session) -> Dict[Tuple[str, datetime.date], int]:
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

    def _fetch_history_in_day_num(self, room_id: str, in_date) -> Optional[int]:
        cache_key = (room_id, in_date)
        if cache_key in self.history_in_day_num_cache:
            return self.history_in_day_num_cache[cache_key]

        try:
            configs = get_all_device_configs(room_id)
            all_query_df = pd.concat(configs.values(), ignore_index=True) if configs else pd.DataFrame()
        except Exception as exc:
            self._record_error("", "history_query_failed", f"设备配置获取失败: {exc}", "")
            self.history_in_day_num_cache[cache_key] = None
            return None

        if all_query_df.empty:
            self.history_in_day_num_cache[cache_key] = None
            return None

        if "point_alias" in all_query_df.columns:
            query_df = all_query_df[all_query_df["point_alias"] == "in_day_num"].copy()
        else:
            query_df = all_query_df[all_query_df["point_name"].isin(["InDayNum", "in_day_num"])].copy()

        if query_df.empty:
            self.history_in_day_num_cache[cache_key] = None
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
                self._record_error("", "history_query_failed", f"历史数据接口失败: {exc}", "")
                continue

        if not frames:
            self.history_in_day_num_cache[cache_key] = None
            return None

        history_df = pd.concat(frames, axis=0, ignore_index=True)
        if "value" not in history_df.columns:
            self.history_in_day_num_cache[cache_key] = None
            return None

        values = pd.to_numeric(history_df["value"], errors="coerce").dropna()
        values = values[values > 0]
        if values.empty:
            self.history_in_day_num_cache[cache_key] = None
            return None

        mode_value = int(values.value_counts().idxmax())
        self.history_in_day_num_cache[cache_key] = mode_value
        return mode_value

    def clean_in_date_field(self, record, updates: Dict[str, Any]):
        collection_date, detailed_time, collection_ip = self._extract_date_from_path(record.image_path)
        if not collection_date or not detailed_time:
            self._record_error(
                str(record.id),
                "path_parse",
                "image_path格式不符合规范",
                record.image_path,
            )
            return

        try:
            normalized = self._normalize_collection_date(collection_date, detailed_time)
            normalized_date = datetime.strptime(normalized, "%Y%m%d").date()
        except ValueError as exc:
            self._record_error(
                str(record.id),
                "date_parse",
                str(exc),
                record.image_path,
            )
            return

        if record.in_date != normalized_date:
            updates["in_date"] = normalized_date
            self.field_fix_counts["in_date"] += 1

        if collection_ip and record.collection_ip != collection_ip:
            updates["collection_ip"] = collection_ip
            self.field_fix_counts["collection_ip"] += 1
        if not collection_ip:
            self._record_error(
                str(record.id),
                "collection_ip_invalid",
                "image_path中采集IP解析失败",
                record.image_path,
            )

    def clean_in_num_and_growth_day(
        self,
        record,
        env_info: Dict[str, Any],
        stats_by_room: Dict[str, Tuple[float, float]],
        historical_in_num: Dict[Tuple[str, datetime.date], int],
        updates: Dict[str, Any],
    ):
        in_num = record.in_num
        if in_num is None:
            self._record_error(
                str(record.id),
                "in_num_null",
                "in_num为空，保持NULL",
                record.image_path,
            )
            self.null_in_num_records.append(
                {"id": str(record.id), "image_path": record.image_path}
            )
        else:
            try:
                normalized_in_num = int(in_num)
            except (ValueError, TypeError):
                normalized_in_num = None
            if normalized_in_num is None:
                self._record_error(
                    str(record.id),
                    "in_num_invalid",
                    "in_num无法转换为整数",
                    record.image_path,
                )
            else:
                if normalized_in_num == 0:
                    hist_key = (record.room_id, record.in_date)
                    history_mode = historical_in_num.get(hist_key)
                    actual_mode = None
                    if record.in_date is not None and record.room_id is not None:
                        actual_mode = self._fetch_history_in_day_num(record.room_id, record.in_date)

                    if history_mode is None:
                        self._record_error(
                            str(record.id),
                            "in_num_zero_no_history",
                            "in_num为0且无同库房同日期众数值",
                            record.image_path,
                        )
                    elif actual_mode is None:
                        self._record_error(
                            str(record.id),
                            "history_in_day_num_missing",
                            "历史数据接口未返回有效in_day_num",
                            record.image_path,
                        )
                    elif actual_mode == history_mode:
                        updates["in_num"] = actual_mode
                        self.field_fix_counts["in_num"] += 1
                        self.zero_in_num_fixed_records.append(
                            {
                                "id": str(record.id),
                                "room_id": record.room_id,
                                "in_date": record.in_date,
                                "old_in_num": normalized_in_num,
                                "new_in_num": actual_mode,
                                "image_path": record.image_path,
                            }
                        )
                        in_num = actual_mode
                    else:
                        self._record_error(
                            str(record.id),
                            "history_in_day_num_mismatch",
                            f"历史接口值与众数不一致({actual_mode} vs {history_mode})",
                            record.image_path,
                        )
                elif normalized_in_num < 0:
                    self._record_error(
                        str(record.id),
                        "in_num_invalid",
                        "in_num为非正数",
                        record.image_path,
                    )
                elif normalized_in_num != in_num:
                    updates["in_num"] = normalized_in_num
                    self.field_fix_counts["in_num"] += 1
                    in_num = normalized_in_num
                elif normalized_in_num > 0:
                    in_num = normalized_in_num

        env_in_day_num = env_info.get("in_day_num")
        if in_num is not None and env_in_day_num is not None:
            try:
                env_in_day_num_val = int(env_in_day_num)
            except (ValueError, TypeError):
                env_in_day_num_val = None
            if env_in_day_num_val is not None and env_in_day_num_val != in_num:
                self._record_error(
                    str(record.id),
                    "in_num_mismatch",
                    f"in_num与env_sensor_status.in_day_num不一致({in_num} vs {env_in_day_num_val})",
                    record.image_path,
                )

        if in_num is not None and in_num <= 0:
            self._record_error(
                str(record.id),
                "in_num_invalid",
                "in_num为非正数",
                record.image_path,
            )

        if record.room_id in stats_by_room and in_num:
            mean_val, std_val = stats_by_room.get(record.room_id, (0, 0))
            if std_val and in_num > mean_val + 3 * std_val:
                self._record_error(
                    str(record.id),
                    "in_num_outlier",
                    f"in_num超出3倍标准差(均值={mean_val:.2f}, std={std_val:.2f})",
                    record.image_path,
                )

        if record.growth_day is None:
            self._record_error(
                str(record.id),
                "growth_day_null",
                "growth_day为空，保持NULL",
                record.image_path,
            )
            self.null_growth_day_records.append(
                {"id": str(record.id), "image_path": record.image_path}
            )

        if record.in_date and record.collection_datetime:
            expected_growth = (record.collection_datetime.date() - record.in_date).days
            if expected_growth < 0:
                self._record_error(
                    str(record.id),
                    "growth_day_negative",
                    "in_date晚于collection_datetime",
                    record.image_path,
                )
            elif record.growth_day is not None and record.growth_day != expected_growth:
                updates["growth_day"] = expected_growth
                self.field_fix_counts["growth_day"] += 1
        else:
            self._record_error(
                str(record.id),
                "growth_day_missing",
                "in_date或collection_datetime缺失",
                record.image_path,
            )

    def identify_and_fix_errors(self, record, env_info: Dict[str, Any], updates: Dict[str, Any]):
        # env date consistency
        in_year = env_info.get("in_year")
        in_month = env_info.get("in_month")
        in_day = env_info.get("in_day")
        if in_year and in_month and in_day and record.in_date:
            try:
                env_date = datetime(int(in_year), int(in_month), int(in_day)).date()
                if record.in_date != env_date:
                    self._record_error(
                        str(record.id),
                        "in_date_mismatch",
                        "in_date与env_sensor_status日期不一致",
                        record.image_path,
                    )
            except (ValueError, TypeError):
                self._record_error(
                    str(record.id),
                    "env_date_invalid",
                    "env_sensor_status日期字段无效",
                    record.image_path,
                )

        quality_score = env_info.get("image_quality_score")
        if quality_score is not None:
            try:
                quality_val = float(quality_score)
            except (TypeError, ValueError):
                quality_val = None
            if quality_val is not None and not (0 <= quality_val <= 100):
                self._record_error(
                    str(record.id),
                    "image_quality_invalid",
                    "image_quality_score超出0-100范围",
                    record.image_path,
                )

        if not record.room_id:
            self._record_error(
                str(record.id),
                "required_missing",
                "room_id为空",
                record.image_path,
            )
        if not record.collection_datetime:
            self._record_error(
                str(record.id),
                "required_missing",
                "collection_datetime为空",
                record.image_path,
            )
        if not record.image_path:
            self._record_error(
                str(record.id),
                "required_missing",
                "image_path为空",
                record.image_path,
            )
        if record.growth_day is None or record.growth_day < 0:
            self._record_error(
                str(record.id),
                "required_missing",
                "growth_day为空或为负数",
                record.image_path,
            )

    def validate_data_quality(self, session) -> Dict[str, Any]:
        query = session.query(
            MushroomImageEmbedding.room_id,
            MushroomImageEmbedding.in_date,
            MushroomImageEmbedding.in_num,
            MushroomImageEmbedding.growth_day,
            MushroomImageEmbedding.collection_datetime,
            MushroomImageEmbedding.image_path,
        )
        df = pd.read_sql(query.statement, session.bind)
        if df.empty:
            return {}

        metrics = {
            "total_records": int(len(df)),
            "null_room_id_pct": float(df["room_id"].isna().mean() * 100),
            "null_in_date_pct": float(df["in_date"].isna().mean() * 100),
            "null_growth_day_pct": float(df["growth_day"].isna().mean() * 100),
            "null_image_path_pct": float(df["image_path"].isna().mean() * 100),
            "null_in_num_pct": float(df["in_num"].isna().mean() * 100),
        }
        return metrics

    def generate_report(self, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)

        error_df = pd.DataFrame(self.error_records)
        fixes_df = pd.DataFrame(self.fix_samples)
        null_in_num_df = pd.DataFrame(self.null_in_num_records)
        null_growth_day_df = pd.DataFrame(self.null_growth_day_records)
        zero_in_num_fixed_df = pd.DataFrame(self.zero_in_num_fixed_records)

        error_file = output_dir / "errors.csv"
        fixes_file = output_dir / "fix_samples.csv"
        null_in_num_file = output_dir / "null_in_num_records.csv"
        null_growth_day_file = output_dir / "null_growth_day_records.csv"
        zero_in_num_fixed_file = output_dir / "zero_in_num_fixed.csv"

        error_df.to_csv(error_file, index=False)
        fixes_df.to_csv(fixes_file, index=False)
        null_in_num_df.to_csv(null_in_num_file, index=False)
        null_growth_day_df.to_csv(null_growth_day_file, index=False)
        zero_in_num_fixed_df.to_csv(zero_in_num_fixed_file, index=False)

        summary = {
            "total_records": self.stats.get("total_records", 0),
            "processed_records": self.stats.get("processed_records", 0),
            "updated_records": self.stats.get("updated_records", 0),
            "failed_records": self.stats.get("failed_records", 0),
            "field_fix_counts": dict(self.field_fix_counts),
            "null_in_num_count": len(self.null_in_num_records),
            "null_growth_day_count": len(self.null_growth_day_records),
            "zero_in_num_fixed_count": len(self.zero_in_num_fixed_records),
            "error_type_counts": {k.replace("error_", ""): v for k, v in self.stats.items() if k.startswith("error_")},
        }
        summary_file = output_dir / "summary.json"
        summary_file.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        logger.info(f"[Cleaner] Summary saved to {summary_file}")
        logger.info(f"[Cleaner] Errors saved to {error_file}")
        logger.info(f"[Cleaner] Fix samples saved to {fixes_file}")
        logger.info(f"[Cleaner] NULL in_num saved to {null_in_num_file}")
        logger.info(f"[Cleaner] NULL growth_day saved to {null_growth_day_file}")
        logger.info(f"[Cleaner] zero in_num fixed saved to {zero_in_num_fixed_file}")

    def run(self):
        logger.info("[Cleaner] Start cleaning mushroom_image_embedding data")

        with self.Session() as session:
            stats_by_room = self._load_in_num_stats(session)
            historical_in_num = self._load_historical_in_num(session)
            total_records = session.query(func.count(MushroomImageEmbedding.id)).scalar() or 0
            self.stats["total_records"] = int(total_records)

            offset = 0
            while offset < total_records:
                batch_records = (
                    session.query(MushroomImageEmbedding)
                    .order_by(MushroomImageEmbedding.id)
                    .offset(offset)
                    .limit(self.batch_size)
                    .all()
                )
                if not batch_records:
                    break

                updates: List[Dict[str, Any]] = []
                for record in batch_records:
                    update_payload: Dict[str, Any] = {"id": record.id}
                    env_info = self._parse_env_sensor_status(record.env_sensor_status)

                    self.clean_in_date_field(record, update_payload)
                    self.clean_in_num_and_growth_day(
                        record,
                        env_info,
                        stats_by_room,
                        historical_in_num,
                        update_payload,
                    )
                    self.identify_and_fix_errors(record, env_info, update_payload)

                    if len(update_payload) > 1:
                        updates.append(update_payload)
                        self.stats["updated_records"] += 1

                        if len(self.fix_samples) < 200:
                            sample = {"id": str(record.id)}
                            for k, v in update_payload.items():
                                if k != "id":
                                    sample[k] = v
                            self.fix_samples.append(sample)

                    self.stats["processed_records"] += 1

                if updates:
                    self._flush_updates(session, updates)

                offset += len(batch_records)

            quality_metrics = self.validate_data_quality(session)
            if quality_metrics:
                logger.info(f"[Cleaner] Data quality metrics: {quality_metrics}")

        output_dir = BASE_DIR / "reports" / "cleaning" / datetime.now().strftime("%Y%m%d_%H%M%S")
        self.generate_report(output_dir)

    def _flush_updates(self, session, updates: List[Dict[str, Any]]):
        if self.dry_run:
            logger.info(f"[Cleaner] Dry run: {len(updates)} updates prepared")
            return
        session.bulk_update_mappings(MushroomImageEmbedding, updates)
        session.commit()


def setup_logging(log_dir: Path):
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "clean_mushroom_embedding_data.log"
    logger.remove()
    logger.add(log_file, rotation="50 MB", retention="7 days", level="INFO", encoding="utf-8")
    logger.add(lambda msg: print(msg, end=""), level="INFO")


def parse_args():
    parser = argparse.ArgumentParser(description="Clean MushroomImageEmbedding data")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    log_dir = BASE_DIR / "logs" / "cleaning"
    setup_logging(log_dir)

    cleaner = MushroomEmbeddingDataCleaner(
        db_engine=pgsql_engine,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )
    cleaner.run()


if __name__ == "__main__":
    main()

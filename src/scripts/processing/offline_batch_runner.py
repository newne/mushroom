from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from loguru import logger


class OfflineBatchProcessor(Protocol):
    def process_daily_batch(
        self,
        limit_per_room_day: int = 2,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> None: ...


def create_offline_processor() -> OfflineBatchProcessor:
    from vision.offline.processor import OfflineProcessor

    return OfflineProcessor()


def run_offline_batch(
    *,
    limit_per_room_day: int = 2,
    start_date: str | None = None,
    end_date: str | None = None,
    log_file: str | None = None,
    processor_factory: Callable[[], OfflineBatchProcessor] = create_offline_processor,
) -> int:
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(log_file)

    logger.info(
        "启动离线图像分析任务: limit_per_room_day={}, start_date={}, end_date={}",
        limit_per_room_day,
        start_date,
        end_date,
    )

    try:
        processor = processor_factory()
        processor.process_daily_batch(
            limit_per_room_day=limit_per_room_day,
            start_date=start_date,
            end_date=end_date,
        )
        logger.info("离线图像分析任务完成")
        return 0
    except Exception as exc:
        logger.error("离线图像分析任务失败: {}", exc)
        return 1
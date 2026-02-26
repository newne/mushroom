"""批次产量初始化任务。"""

from datetime import date

from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
from utils.batch_yield_service import init_batch_yield_records
from utils.loguru_setting import logger

SessionLocal = sessionmaker(bind=pgsql_engine, autoflush=False, autocommit=False)


def safe_daily_batch_yield_init() -> None:
    """每日初始化批次产量基础记录。"""
    from utils.task_common import check_database_connection

    if not check_database_connection():
        error_msg = "[BATCH_YIELD_TASK] 数据库不可达，任务终止（按配置不启用容错）"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    stat_date = date.today()
    db = SessionLocal()
    try:
        logger.info("[BATCH_YIELD_TASK] 开始初始化批次产量记录")
        created = init_batch_yield_records(db, stat_date=stat_date)
        db.commit()
        logger.info("[BATCH_YIELD_TASK] 初始化完成，新增: %s", created)
    except Exception as exc:
        logger.error(f"[BATCH_YIELD_TASK] 初始化失败: {exc}", exc_info=True)
        db.rollback()
    finally:
        db.close()

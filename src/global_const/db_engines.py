"""数据库连接基础设施。"""

import os
from importlib import import_module
from urllib.parse import quote_plus

import sqlalchemy
from loguru import logger

settings = import_module("global_const.config_loader").settings


def _get_int_env(name: str, default: int) -> int:
    """读取整型环境变量，非法值时回退默认值。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        logger.warning(f"环境变量 {name}={raw} 非法，使用默认值 {default}")
        return default


DB_CONNECT_TIMEOUT = _get_int_env("DB_CONNECT_TIMEOUT", 30)
DB_POOL_TIMEOUT = _get_int_env("DB_POOL_TIMEOUT", 60)

logger.info(
    f"[DB-CONFIG] connect_timeout={DB_CONNECT_TIMEOUT}s, pool_timeout={DB_POOL_TIMEOUT}s"
)

# 2026-10-14 用户裁定：`tools_mysql` 是业务数据库，**算法侧禁止使用**；取数一律走 pgsql。
# `mysql_engine` 与它的 `engine_url` 已删除——全仓零调用者（只有 `global_const.py` 的
# 导出与 `__all__`），`settings.mysql` 除本文件外也无人读。要恢复请走票据，别再从
# `src/**` 里引 mysql。

pg_engine_url = (
    f"{settings.pgsql.database_type}+{settings.pgsql.driver}://"
    f"{settings.pgsql.username}:{quote_plus(settings.pgsql.password)}@"
    f"{settings.pgsql.host}:{settings.pgsql.port}/{settings.pgsql.database_name}"
)

pgsql_engine = sqlalchemy.create_engine(
    pg_engine_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=5,
    max_overflow=10,
    pool_timeout=DB_POOL_TIMEOUT,
    connect_args={
        "connect_timeout": DB_CONNECT_TIMEOUT,
        "options": "-c statement_timeout=300000 -c client_encoding=UTF8",
        "client_encoding": "utf8",
    },
    echo=False,
    future=True,
)

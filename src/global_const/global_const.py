"""
@Project ：load_prediction
@File    ：global_const.py
@IDE     ：PyCharm
@Author  ：niucg1@lenovo.com
@Date    ：2024/10/22 19:35
@Desc     :
"""

import os
from pathlib import Path
from urllib.parse import quote_plus

import redis
import sqlalchemy
from dynaconf import Dynaconf
from loguru import logger

BASE_DIR = Path(__file__).absolute().parent.parent


def _str_to_bool(value: str) -> bool:
    """Convert string to boolean, treating 'true' (case insensitive) as True, everything else as False"""
    return str(value).lower() == "true"


def get_environment() -> str:
    """获取当前环境"""
    return "production" if _str_to_bool(os.environ.get("prod", "false")) else "development"


# Convert prod env var to boolean, default to False if not set
env = get_environment()

config_dir_path = BASE_DIR / "configs"
logger.info(f"[9.9.1] 已加载配置文件目录：str(BASE_DIR / {config_dir_path})")

settings = Dynaconf(
    root_path=str(BASE_DIR),
    envvar_prefix="wuhan_load_scheduling",
    environments=True,
    env=env,
    merge_enabled=True,
    settings_files=[
        str(config_dir_path / "settings.toml"),
        str(config_dir_path / ".secrets.toml"),
    ],
)

static_settings = Dynaconf(
    root_path=str(BASE_DIR),
    envvar_prefix="wuhan_load_scheduling",
    settings_files=[str(config_dir_path / "static_config.json")],
)

# redis config
pool = redis.ConnectionPool(
    host=settings.redis.host,
    port=settings.redis.port,
    password=settings.redis.password,
    decode_responses=True,
)
conn = redis.Redis(
    connection_pool=pool,
)

# 数据查询服务接口 - 延迟初始化，避免循环导入
def create_get_data():
    """创建GetData实例，避免循环导入"""
    from utils.get_data import GetData
    return GetData(
        urls=settings.data_source_url,
        host=settings.host.host,
        port=settings.host.port
    )

table_name = dict(ep_history_agg="ep_history_agg")
redis_key = dict(
    low_load_makeup="load_scheduling:low_load_makeup:{device_alias}",
    return_water_compensation="load_scheduling:return_water_compensation:{device_alias}",
    indoor_makeup="load_scheduling:indoor_makeup:{device_alias}",
)

# Redis key定义，用于存储不同类型的配置
mushroom_redis_key = dict(
    air_cooler_query_df="mushroom:air_cooler_query_df",
    static_config="mushroom:static_config:{device_type}",
    all_device_configs="mushroom:all_device_configs"
)
add_reduction_chiller_key = dict(
    high_current_ratio="add_reduction_chiller:high_current_ratio:phase_{phase}",
    low_current_ratio="add_reduction_chiller:low_current_ratio:phase_{phase}",
    ready_to_stop="add_reduction_chiller:ready_to_stop:phase_{phase}",
    ready_to_start="add_reduction_chiller:ready_to_start:phase_{phase}",
)
# 假设settings.mysql是一个包含数据库配置的对象
engine_url = f"{settings.mysql.database_type}+{settings.mysql.driver}://{settings.mysql.username}:{quote_plus(settings.mysql.password)}@{settings.mysql.host}:{settings.mysql.port}/{settings.mysql.database_name}"

# MySQL引擎配置 - 针对Docker网络环境优化
mysql_engine = sqlalchemy.create_engine(
    engine_url,
    pool_pre_ping=True,          # 连接前检查连接是否有效
    pool_recycle=1800,            # 连接回收时间（30分钟）
    pool_size=5,                  # 连接池大小
    max_overflow=10,              # 最大溢出连接数
    pool_timeout=30,              # 获取连接的超时时间（秒）
    connect_args={
        "connect_timeout": 10     # TCP连接超时（秒）- 适应Docker网络
    },
    echo=False                    # 不输出SQL日志
)
pg_engine_url = f"{settings.pgsql.database_type}+{settings.pgsql.driver}://{settings.pgsql.username}:{quote_plus(settings.pgsql.password)}@{settings.pgsql.host}:{settings.pgsql.port}/{settings.pgsql.database_name}"

# PostgreSQL引擎配置 - 针对Docker网络环境优化
pgsql_engine = sqlalchemy.create_engine(
    pg_engine_url,
    pool_pre_ping=True,          # 连接前检查连接是否有效
    pool_recycle=1800,            # 连接回收时间（30分钟）
    pool_size=5,                  # 连接池大小
    max_overflow=10,              # 最大溢出连接数
    pool_timeout=30,              # 获取连接的超时时间（秒）
    connect_args={
        "connect_timeout": 10,    # TCP连接超时（秒）- 适应Docker网络
        "options": "-c statement_timeout=300000"  # SQL语句超时（5分钟）
    },
    echo=False,                   # 不输出SQL日志
    future=True                   # 使用SQLAlchemy 2.0风格
)


IMAGE_DIR = BASE_DIR.parent / 'data'

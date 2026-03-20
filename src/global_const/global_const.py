"""全局常量与资源导出（兼容层）。"""

from importlib import import_module

_paths = import_module("global_const.paths")
_environment = import_module("global_const.environment")
_config_loader = import_module("global_const.config_loader")
_db_engines = import_module("global_const.db_engines")
_cache_client = import_module("global_const.cache_client")

BASE_DIR = _paths.BASE_DIR
IMAGE_DIR = _paths.IMAGE_DIR
ensure_src_path = _paths.ensure_src_path

str_to_bool = _environment.str_to_bool
get_environment = _environment.get_environment
env = _environment.env

settings = _config_loader.settings
static_settings = _config_loader.static_settings

DB_CONNECT_TIMEOUT = _db_engines.DB_CONNECT_TIMEOUT
DB_POOL_TIMEOUT = _db_engines.DB_POOL_TIMEOUT
engine_url = _db_engines.engine_url
mysql_engine = _db_engines.mysql_engine
pg_engine_url = _db_engines.pg_engine_url
pgsql_engine = _db_engines.pgsql_engine

pool = _cache_client.pool
conn = _cache_client.conn


def _str_to_bool(value: str) -> bool:
    """向后兼容函数别名。"""
    return str_to_bool(value)


# 数据查询服务接口 - 延迟初始化，避免循环导入
def create_get_data():
    """创建GetData实例，避免循环导入"""
    from utils.get_data import GetData

    return GetData(
        urls=settings.data_source_url, host=settings.host.host, port=settings.host.port
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
    all_device_configs="mushroom:all_device_configs",
)
add_reduction_chiller_key = dict(
    high_current_ratio="add_reduction_chiller:high_current_ratio:phase_{phase}",
    low_current_ratio="add_reduction_chiller:low_current_ratio:phase_{phase}",
    ready_to_stop="add_reduction_chiller:ready_to_stop:phase_{phase}",
    ready_to_start="add_reduction_chiller:ready_to_start:phase_{phase}",
)
__all__ = [
    "BASE_DIR",
    "IMAGE_DIR",
    "ensure_src_path",
    "_str_to_bool",
    "get_environment",
    "env",
    "DB_CONNECT_TIMEOUT",
    "DB_POOL_TIMEOUT",
    "settings",
    "static_settings",
    "pool",
    "conn",
    "create_get_data",
    "table_name",
    "redis_key",
    "mushroom_redis_key",
    "add_reduction_chiller_key",
    "engine_url",
    "mysql_engine",
    "pg_engine_url",
    "pgsql_engine",
]

"""缓存与外部连接基础设施。"""

import os
from importlib import import_module

import redis
from loguru import logger

settings = import_module("global_const.config_loader").settings

pool = None
conn = None

try:
    pool = redis.ConnectionPool(
        host=settings.redis.host,
        port=settings.redis.port,
        password=settings.redis.password,
        decode_responses=True,
        socket_connect_timeout=10,
        socket_timeout=10,
        retry_on_timeout=True,
        health_check_interval=30,
    )
    conn = redis.Redis(connection_pool=pool)
    logger.info(f"Redis连接池创建成功: {settings.redis.host}:{settings.redis.port}")
except AttributeError as e:
    logger.warning(f"Redis配置访问失败: {e}")
    redis_host = os.environ.get("REDIS_HOST", "172.17.0.1")
    redis_port = int(os.environ.get("REDIS_PORT", "26379"))
    redis_password = "Pl5SpB72sllM8DsT"

    pool = redis.ConnectionPool(
        host=redis_host,
        port=redis_port,
        password=redis_password,
        decode_responses=True,
        socket_connect_timeout=10,
        socket_timeout=10,
        retry_on_timeout=True,
        health_check_interval=30,
    )
    conn = redis.Redis(connection_pool=pool)
    logger.info(f"使用环境变量Redis配置: {redis_host}:{redis_port}")
except Exception as e:
    logger.error(f"Redis连接池创建失败: {e}")
    conn = None

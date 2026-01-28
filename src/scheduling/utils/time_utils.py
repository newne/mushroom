"""
时间工具模块
"""
import os
import pytz
from datetime import datetime, timezone
from utils.loguru_setting import logger

def get_local_timezone() -> timezone:
    """
    获取本地时区
    
    Returns:
        timezone: 本地时区对象
    """
    tz_str = os.environ.get('TZ')
    if tz_str:
        try:
            return pytz.timezone(tz_str)
        except pytz.UnknownTimeZoneError:
            logger.warning(f"Unknown timezone: {tz_str}, using system timezone")
    
    try:
        return datetime.now().astimezone().tzinfo or timezone.utc
    except Exception as e:
        logger.warning(f"Failed to get system timezone: {e}, using UTC")
        return timezone.utc

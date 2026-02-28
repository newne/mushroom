import sys
from pathlib import Path
import streamlit as st
from loguru import logger

from global_const.global_const import BASE_DIR
from utils.loguru_setting import loguru_setting


@st.cache_resource
def start_system_scheduler():
    """Deprecated: Streamlit 内不再启动调度器，调度器由独立进程运行。"""
    loguru_setting()
    logger.warning(
        "[Streamlit] Scheduler startup from Streamlit is disabled. "
        "Please run scheduler as a standalone process."
    )
    return None

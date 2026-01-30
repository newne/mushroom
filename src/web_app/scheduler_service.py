import sys
from pathlib import Path
import streamlit as st
from loguru import logger

from global_const.global_const import BASE_DIR
from utils.loguru_setting import loguru_setting
from scheduling.core.scheduler import OptimizedScheduler

@st.cache_resource
def start_system_scheduler():
    """Start the backend scheduler in a background thread (Singleton)"""
    try:
        # Initialize logging
        loguru_setting()
        logger.info("[Streamlit] Initializing Backend Scheduler...")
        
        # Create and start scheduler
        scheduler_mgr = OptimizedScheduler()
        # Ensure database and tables are ready
        scheduler_mgr.initialize()
        
        # Start the scheduler
        scheduler_mgr.start()
        logger.info("[Streamlit] Scheduler is running in background.")
        
        return scheduler_mgr
    except Exception as e:
        logger.critical(f"[Streamlit] Scheduler Start Failed: {e}")
        return None

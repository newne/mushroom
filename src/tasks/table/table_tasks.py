"""
数据库表管理任务模块

负责数据库表的创建、维护和管理相关任务。
"""

from .table_executor import get_table_creation_status, safe_create_tables

__all__ = ["safe_create_tables", "get_table_creation_status"]

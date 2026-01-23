"""
数据库操作工具类

提供数据库连接、查询、事务管理等通用功能。
"""

import time
from contextlib import contextmanager
from typing import Dict, Any, List, Optional, Union
from datetime import datetime

import pandas as pd
from sqlalchemy import text, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from utils.loguru_setting import logger
from global_const.global_const import pgsql_engine


class DatabaseManager:
    """数据库管理器"""
    
    def __init__(self, engine=None):
        """
        初始化数据库管理器
        
        Args:
            engine: 数据库引擎，默认使用全局引擎
        """
        self.engine = engine or pgsql_engine
        self.Session = sessionmaker(bind=self.engine)
    
    @contextmanager
    def get_connection(self):
        """
        获取数据库连接上下文管理器
        
        Yields:
            connection: 数据库连接
        """
        conn = None
        try:
            conn = self.engine.connect()
            yield conn
        except Exception as e:
            logger.error(f"[DB_MANAGER] 数据库连接错误: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    @contextmanager
    def get_session(self):
        """
        获取数据库会话上下文管理器
        
        Yields:
            session: 数据库会话
        """
        session = None
        try:
            session = self.Session()
            yield session
            session.commit()
        except Exception as e:
            if session:
                session.rollback()
            logger.error(f"[DB_MANAGER] 数据库会话错误: {e}")
            raise
        finally:
            if session:
                session.close()
    
    def execute_query(
        self, 
        query: str, 
        params: Dict[str, Any] = None,
        fetch_all: bool = True
    ) -> Union[List[tuple], tuple, None]:
        """
        执行查询语句
        
        Args:
            query: SQL查询语句
            params: 查询参数
            fetch_all: 是否获取所有结果
            
        Returns:
            查询结果
        """
        try:
            with self.get_connection() as conn:
                result = conn.execute(text(query), params or {})
                
                if fetch_all:
                    return result.fetchall()
                else:
                    return result.fetchone()
                    
        except Exception as e:
            logger.error(f"[DB_MANAGER] 查询执行失败: {e}")
            logger.error(f"[DB_MANAGER] 查询语句: {query}")
            logger.error(f"[DB_MANAGER] 查询参数: {params}")
            raise
    
    def execute_insert(
        self, 
        table_name: str, 
        data: Union[Dict[str, Any], List[Dict[str, Any]]],
        on_conflict: str = None
    ) -> int:
        """
        执行插入操作
        
        Args:
            table_name: 表名
            data: 插入数据
            on_conflict: 冲突处理策略
            
        Returns:
            插入的记录数
        """
        try:
            if isinstance(data, dict):
                data = [data]
            
            if not data:
                return 0
            
            # 构建插入语句
            columns = list(data[0].keys())
            placeholders = ', '.join([f':{col}' for col in columns])
            columns_str = ', '.join(columns)
            
            query = f"INSERT INTO {table_name} ({columns_str}) VALUES ({placeholders})"
            
            if on_conflict:
                query += f" {on_conflict}"
            
            with self.get_connection() as conn:
                result = conn.execute(text(query), data)
                conn.commit()
                return result.rowcount
                
        except Exception as e:
            logger.error(f"[DB_MANAGER] 插入操作失败: {e}")
            logger.error(f"[DB_MANAGER] 表名: {table_name}")
            logger.error(f"[DB_MANAGER] 数据样例: {data[0] if data else 'Empty'}")
            raise
    
    def execute_update(
        self, 
        table_name: str, 
        data: Dict[str, Any],
        where_clause: str,
        where_params: Dict[str, Any] = None
    ) -> int:
        """
        执行更新操作
        
        Args:
            table_name: 表名
            data: 更新数据
            where_clause: WHERE条件
            where_params: WHERE参数
            
        Returns:
            更新的记录数
        """
        try:
            # 构建更新语句
            set_clause = ', '.join([f"{col} = :{col}" for col in data.keys()])
            query = f"UPDATE {table_name} SET {set_clause} WHERE {where_clause}"
            
            # 合并参数
            params = {**data, **(where_params or {})}
            
            with self.get_connection() as conn:
                result = conn.execute(text(query), params)
                conn.commit()
                return result.rowcount
                
        except Exception as e:
            logger.error(f"[DB_MANAGER] 更新操作失败: {e}")
            logger.error(f"[DB_MANAGER] 表名: {table_name}")
            logger.error(f"[DB_MANAGER] WHERE条件: {where_clause}")
            raise
    
    def execute_delete(
        self, 
        table_name: str, 
        where_clause: str,
        where_params: Dict[str, Any] = None
    ) -> int:
        """
        执行删除操作
        
        Args:
            table_name: 表名
            where_clause: WHERE条件
            where_params: WHERE参数
            
        Returns:
            删除的记录数
        """
        try:
            query = f"DELETE FROM {table_name} WHERE {where_clause}"
            
            with self.get_connection() as conn:
                result = conn.execute(text(query), where_params or {})
                conn.commit()
                return result.rowcount
                
        except Exception as e:
            logger.error(f"[DB_MANAGER] 删除操作失败: {e}")
            logger.error(f"[DB_MANAGER] 表名: {table_name}")
            logger.error(f"[DB_MANAGER] WHERE条件: {where_clause}")
            raise
    
    def bulk_insert_dataframe(
        self, 
        df: pd.DataFrame, 
        table_name: str,
        if_exists: str = 'append',
        chunksize: int = 1000
    ) -> int:
        """
        批量插入DataFrame数据
        
        Args:
            df: DataFrame数据
            table_name: 表名
            if_exists: 存在时的处理方式
            chunksize: 批次大小
            
        Returns:
            插入的记录数
        """
        try:
            if df.empty:
                return 0
            
            rows_inserted = df.to_sql(
                table_name,
                con=self.engine,
                if_exists=if_exists,
                index=False,
                method='multi',
                chunksize=chunksize
            )
            
            logger.info(f"[DB_MANAGER] 成功批量插入 {len(df)} 条记录到表 {table_name}")
            return len(df)
            
        except Exception as e:
            logger.error(f"[DB_MANAGER] 批量插入失败: {e}")
            logger.error(f"[DB_MANAGER] 表名: {table_name}")
            logger.error(f"[DB_MANAGER] 数据形状: {df.shape}")
            raise
    
    def check_connection(self) -> bool:
        """
        检查数据库连接状态
        
        Returns:
            bool: 连接是否正常
        """
        try:
            with self.get_connection() as conn:
                conn.execute(text("SELECT 1"))
            
            logger.debug("[DB_MANAGER] 数据库连接检查通过")
            return True
            
        except Exception as e:
            logger.error(f"[DB_MANAGER] 数据库连接检查失败: {e}")
            return False
    
    def get_table_info(self, table_name: str) -> Dict[str, Any]:
        """
        获取表信息
        
        Args:
            table_name: 表名
            
        Returns:
            Dict[str, Any]: 表信息
        """
        try:
            # 检查表是否存在
            exists_query = """
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = :table_name
                )
            """
            
            exists = self.execute_query(exists_query, {"table_name": table_name}, fetch_all=False)[0]
            
            if not exists:
                return {
                    'exists': False,
                    'table_name': table_name
                }
            
            # 获取表结构信息
            columns_query = """
                SELECT 
                    column_name,
                    data_type,
                    is_nullable,
                    column_default
                FROM information_schema.columns 
                WHERE table_name = :table_name
                ORDER BY ordinal_position
            """
            
            columns = self.execute_query(columns_query, {"table_name": table_name})
            
            # 获取记录数
            count_query = f"SELECT COUNT(*) FROM {table_name}"
            record_count = self.execute_query(count_query, fetch_all=False)[0]
            
            return {
                'exists': True,
                'table_name': table_name,
                'columns': [
                    {
                        'name': col[0],
                        'type': col[1],
                        'nullable': col[2] == 'YES',
                        'default': col[3]
                    }
                    for col in columns
                ],
                'record_count': record_count,
                'column_count': len(columns)
            }
            
        except Exception as e:
            logger.error(f"[DB_MANAGER] 获取表信息失败: {e}")
            return {
                'exists': False,
                'table_name': table_name,
                'error': str(e)
            }


class DatabaseRetryManager:
    """数据库重试管理器"""
    
    def __init__(self, max_retries: int = 3, retry_delay: int = 1):
        """
        初始化重试管理器
        
        Args:
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
        """
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.connection_error_keywords = [
            'timeout', 'connection', 'connect', 'database', 'server'
        ]
    
    def execute_with_retry(self, func, *args, **kwargs):
        """
        带重试机制执行数据库操作
        
        Args:
            func: 要执行的函数
            *args: 函数参数
            **kwargs: 函数关键字参数
            
        Returns:
            函数执行结果
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                return func(*args, **kwargs)
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"[DB_RETRY] 数据库操作失败 (尝试 {attempt}/{self.max_retries}): {error_msg}")
                
                # 检查是否是连接错误
                is_connection_error = any(
                    keyword in error_msg.lower() 
                    for keyword in self.connection_error_keywords
                )
                
                if is_connection_error and attempt < self.max_retries:
                    logger.warning(f"[DB_RETRY] 检测到连接错误，{self.retry_delay}秒后重试...")
                    time.sleep(self.retry_delay)
                elif attempt >= self.max_retries:
                    logger.error(f"[DB_RETRY] 数据库操作失败，已达到最大重试次数 ({self.max_retries})")
                    raise
                else:
                    logger.error(f"[DB_RETRY] 数据库操作遇到非连接错误，不再重试")
                    raise
        
        raise Exception("数据库操作重试失败")


# 创建全局实例
db_manager = DatabaseManager()
db_retry_manager = DatabaseRetryManager()


def get_database_manager() -> DatabaseManager:
    """
    获取数据库管理器实例
    
    Returns:
        DatabaseManager: 数据库管理器
    """
    return db_manager


def execute_with_retry(func, *args, **kwargs):
    """
    带重试机制执行数据库操作（便捷函数）
    
    Args:
        func: 要执行的函数
        *args: 函数参数
        **kwargs: 函数关键字参数
        
    Returns:
        函数执行结果
    """
    return db_retry_manager.execute_with_retry(func, *args, **kwargs)


def check_database_health() -> Dict[str, Any]:
    """
    检查数据库健康状态
    
    Returns:
        Dict[str, Any]: 健康状态信息
    """
    try:
        start_time = datetime.now()
        
        # 检查连接
        connection_ok = db_manager.check_connection()
        
        # 检查主要表
        important_tables = [
            'mushroom_embedding',
            'mushroom_env_daily_stats',
            'device_setpoint_changes',
            'decision_analysis_static_config',
            'decision_analysis_dynamic_result'
        ]
        
        table_status = {}
        for table_name in important_tables:
            table_info = db_manager.get_table_info(table_name)
            table_status[table_name] = {
                'exists': table_info['exists'],
                'record_count': table_info.get('record_count', 0)
            }
        
        response_time = (datetime.now() - start_time).total_seconds()
        
        return {
            'healthy': connection_ok,
            'response_time': response_time,
            'connection_status': 'OK' if connection_ok else 'FAILED',
            'table_status': table_status,
            'check_time': datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"[DB_HEALTH] 数据库健康检查失败: {e}")
        return {
            'healthy': False,
            'error': str(e),
            'check_time': datetime.now().isoformat()
        }
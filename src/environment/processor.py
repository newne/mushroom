"""
环境数据处理器
负责环境数据的统计、分析和处理。
"""
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Dict, Any

from utils.loguru_setting import logger
from global_const.global_const import pgsql_engine

class EnvDataProcessor:
    """环境数据处理器类"""
    
    def __init__(self):
        """初始化环境数据处理器"""
        self.engine = pgsql_engine
        logger.debug("环境数据处理器初始化完成")
    
    def process_daily_stats(self, room_id: str, stat_date: date) -> Dict[str, Any]:
        """处理每日环境统计"""
        return process_daily_env_stats(room_id, stat_date)
    
    def get_room_data(self, room_id: str, stat_date: date) -> pd.DataFrame:
        """获取库房环境数据"""
        return get_room_env_data(room_id, stat_date)
    
    def get_environment_data(self, room_id: str, collection_time: datetime, image_path: str, time_window_minutes: int = 30) -> Dict[str, Any]:
        """
        获取指定时间点的环境数据
        
        Args:
            room_id: 库房编号
            collection_time: 采集时间
            image_path: 图像路径
            time_window_minutes: 时间窗口（分钟）
            
        Returns:
            环境数据字典
        """
        try:
            from utils.dataframe_utils import get_all_device_configs
            from utils.data_preprocessing import query_data_by_batch_time
            
            # 1. 初始化结果
            env_data = {
                'room_id': room_id,
                'in_date': collection_time.date(),
                'semantic_description': f"Mushroom Room {room_id}, Time: {collection_time}",
                'env_sensor_status': '{}'
            }
            
            # 2. 获取环境传感器配置
            device_configs = get_all_device_configs(room_id=room_id)
            
            # 3. 获取环境传感器数据 (temperature, humidity, co2)
            if device_configs and 'mushroom_env_status' in device_configs:
                env_config = device_configs['mushroom_env_status']
                start_time = collection_time - timedelta(minutes=time_window_minutes)
                end_time = collection_time + timedelta(minutes=time_window_minutes)
                
                # 查询数据
                df = env_config.groupby("device_alias").apply(
                    query_data_by_batch_time, 
                    start_time, 
                    end_time
                ).reset_index(drop=True)
                
                if not df.empty:
                    # 数据透视
                    pivot_col = 'point_alias' if 'point_alias' in df.columns else 'point_name'
                    # 确保 pivot_col 存在
                    if pivot_col not in df.columns:
                         # 尝试使用 point_name
                         pivot_col = 'point_name'

                    pivot_df = df.pivot_table(
                        index='time', 
                        columns=pivot_col, 
                        values='value'
                    ).reset_index()
                    
                    # 计算平均值并更新 env_data
                    desc_parts = []
                    
                    if 'temperature' in pivot_df.columns:
                        temp = pivot_df['temperature'].mean()
                        env_data['temperature'] = temp
                        desc_parts.append(f"Temperature: {temp:.1f}C")
                        
                    if 'humidity' in pivot_df.columns:
                        hum = pivot_df['humidity'].mean()
                        env_data['humidity'] = hum
                        desc_parts.append(f"Humidity: {hum:.1f}%")
                        
                    if 'co2' in pivot_df.columns:
                        co2 = pivot_df['co2'].mean()
                        env_data['co2'] = co2
                        desc_parts.append(f"CO2: {co2:.0f}ppm")
                        
                    if desc_parts:
                        env_data['semantic_description'] += ". Environment: " + ", ".join(desc_parts) + "."
                        
                    # 序列化传感器状态 (简化)
                    env_data['env_sensor_status'] = df.head(1).to_json() 

            return env_data
            
        except Exception as e:
            logger.error(f"[ENV_PROCESSOR] 获取环境数据失败: {e}")
            return None

    def calculate_statistics(self, env_data: pd.DataFrame) -> Dict[str, Any]:
        """计算环境统计"""
        return calculate_env_statistics(env_data)

def create_env_data_processor() -> EnvDataProcessor:
    """
    创建环境数据处理器实例
    Returns:
        EnvDataProcessor: 环境数据处理器实例
    """
    return EnvDataProcessor()

def process_daily_env_stats(room_id: str, stat_date: date) -> Dict[str, Any]:
    """
    处理单个库房的每日环境统计
    Args:
        room_id: 库房编号
        stat_date: 统计日期
    Returns:
        Dict[str, Any]: 处理结果
    """
    try:
        logger.info(f"[ENV_PROCESSOR] 处理库房 {room_id} 的环境统计，日期: {stat_date}")
        
        # 获取环境数据
        env_data = get_room_env_data(room_id, stat_date)
        
        if env_data.empty:
            logger.warning(f"[ENV_PROCESSOR] 库房 {room_id} 在 {stat_date} 无环境数据")
            return {
                'success': True,
                'records_count': 0,
                'message': 'No data available'
            }
        
        # 计算统计指标
        stats = calculate_env_statistics(env_data)
        
        # 存储统计结果
        record_count = store_env_statistics(room_id, stat_date, stats)
        
        logger.info(f"[ENV_PROCESSOR] 库房 {room_id} 环境统计完成，生成 {record_count} 条记录")
        
        return {
            'success': True,
            'records_count': record_count,
            'stats_summary': stats
        }
        
    except Exception as e:
        logger.error(f"[ENV_PROCESSOR] 库房 {room_id} 环境统计失败: {e}")
        return {
            'success': False,
            'error': str(e),
            'records_count': 0
        }

def get_room_env_data(room_id: str, stat_date: date) -> pd.DataFrame:
    """
    获取库房的环境数据
    Args:
        room_id: 库房编号
        stat_date: 统计日期
    Returns:
        pd.DataFrame: 环境数据 (包含 time, temperature, humidity, co2 列)
    """
    try:
        from utils.dataframe_utils import get_all_device_configs
        from utils.data_preprocessing import query_data_by_batch_time
        
        # 1. 获取环境传感器配置
        device_configs = get_all_device_configs(room_id=room_id)
        if not device_configs or 'mushroom_env_status' not in device_configs:
            logger.warning(f"[ENV_PROCESSOR] 未找到库房 {room_id} 的环境传感器配置")
            return pd.DataFrame()
            
        env_config = device_configs['mushroom_env_status']
        
        # 2. 构建查询时间范围
        start_time = datetime.combine(stat_date, datetime.min.time())
        end_time = start_time + timedelta(days=1)
        
        # 3. 查询数据
        # 使用 groupby().apply() 模式来查询数据
        df = env_config.groupby("device_alias").apply(
            query_data_by_batch_time, 
            start_time, 
            end_time
        ).reset_index(drop=True)
        
        if df.empty:
            logger.debug(f"[ENV_PROCESSOR] 库房 {room_id} 在 {stat_date} 无原始环境数据")
            return pd.DataFrame()
            
        # 4. 数据透视 (Pivot)
        # 将 point_name (temperature, humidity, co2) 转为列
        # 注意：point_name 可能与别名不同，这里假设配置中的 point_alias 是标准名称
        
        # 检查是否有 point_alias 列，如果没有则使用 point_name
        pivot_col = 'point_alias' if 'point_alias' in df.columns else 'point_name'
        
        # 如果 df 中没有 point_alias，尝试从 env_config 合并
        if 'point_alias' not in df.columns:
             # 这里简化处理，通常 query_data_by_batch_time 返回的结果可能不包含 point_alias
             # 但它包含 point_name。我们需要确认 point_name 是否就是 temperature 等
             pass

        pivot_df = df.pivot_table(
            index='time', 
            columns='point_name', 
            values='value'
        ).reset_index()
        
        # 5. 标准化列名
        # 确保包含所需的列，缺失的用 NaN 填充
        required_cols = ['temperature', 'humidity', 'co2']
        for col in required_cols:
            if col not in pivot_df.columns:
                pivot_df[col] = None
                
        # 6. 排序
        pivot_df = pivot_df.sort_values('time')
        
        logger.debug(f"[ENV_PROCESSOR] 获取到 {len(pivot_df)} 条环境数据记录")
        return pivot_df
        
    except Exception as e:
        logger.error(f"[ENV_PROCESSOR] 获取环境数据失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return pd.DataFrame()

def calculate_env_statistics(env_data: pd.DataFrame) -> Dict[str, Any]:
    """
    计算环境统计指标
    Args:
        env_data: 环境数据
    Returns:
        Dict[str, Any]: 统计指标
    """
    stats = {}
    
    try:
        # 温度统计
        if 'temperature' in env_data.columns:
            temp_data = env_data['temperature'].dropna()
            if not temp_data.empty:
                stats['temp_median'] = float(temp_data.median())
                stats['temp_min'] = float(temp_data.min())
                stats['temp_max'] = float(temp_data.max())
                stats['temp_q25'] = float(temp_data.quantile(0.25))
                stats['temp_q75'] = float(temp_data.quantile(0.75))
                stats['temp_count'] = len(temp_data)
        
        # 湿度统计
        if 'humidity' in env_data.columns:
            humidity_data = env_data['humidity'].dropna()
            if not humidity_data.empty:
                stats['humidity_median'] = float(humidity_data.median())
                stats['humidity_min'] = float(humidity_data.min())
                stats['humidity_max'] = float(humidity_data.max())
                stats['humidity_q25'] = float(humidity_data.quantile(0.25))
                stats['humidity_q75'] = float(humidity_data.quantile(0.75))
                stats['humidity_count'] = len(humidity_data)
        
        # CO2统计
        if 'co2' in env_data.columns:
            co2_data = env_data['co2'].dropna()
            if not co2_data.empty:
                stats['co2_median'] = float(co2_data.median())
                stats['co2_min'] = float(co2_data.min())
                stats['co2_max'] = float(co2_data.max())
                stats['co2_q25'] = float(co2_data.quantile(0.25))
                stats['co2_q75'] = float(co2_data.quantile(0.75))
                stats['co2_count'] = len(co2_data)
        
        # 其他统计指标
        stats['is_growth_phase'] = True  # 根据实际业务逻辑确定
        stats['in_day_num'] = None  # 根据实际业务逻辑计算
        
        logger.debug(f"[ENV_PROCESSOR] 计算统计指标完成: {len(stats)} 个指标")
        
    except Exception as e:
        logger.error(f"[ENV_PROCESSOR] 计算统计指标失败: {e}")
    
    return stats

def store_env_statistics(room_id: str, stat_date: date, stats: Dict[str, Any]) -> int:
    """
    存储环境统计结果
    Args:
        room_id: 库房编号
        stat_date: 统计日期
        stats: 统计指标
    Returns:
        int: 存储的记录数
    """
    try:
        from sqlalchemy import text
        
        insert_data = {
            'room_id': room_id,
            'stat_date': stat_date,
            **stats
        }
        
        with pgsql_engine.connect() as conn:
            conn.execute(text("""
                DELETE FROM mushroom_env_daily_stats 
                WHERE room_id = :room_id AND stat_date = :stat_date
            """), {'room_id': room_id, 'stat_date': stat_date})
            
            columns = ', '.join(insert_data.keys())
            placeholders = ', '.join([f':{key}' for key in insert_data.keys()])
            
            conn.execute(text(f"""
                INSERT INTO mushroom_env_daily_stats ({columns})
                VALUES ({placeholders})
            """), insert_data)
            
            conn.commit()
        
        logger.debug(f"[ENV_PROCESSOR] 环境统计数据存储完成")
        return 1
        
    except Exception as e:
        logger.error(f"[ENV_PROCESSOR] 存储环境统计失败: {e}")
        return 0

def get_env_trend_analysis(room_id: str, days: int = 7) -> Dict[str, Any]:
    """
    获取环境趋势分析
    Args:
        room_id: 库房编号
        days: 分析天数
    Returns:
        Dict[str, Any]: 趋势分析结果
    """
    try:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days)
        
        query = """
        SELECT 
            stat_date,
            temp_median,
            humidity_median,
            co2_median
        FROM mushroom_env_daily_stats 
        WHERE room_id = %(room_id)s 
        AND stat_date BETWEEN %(start_date)s AND %(end_date)s
        ORDER BY stat_date
        """
        
        df = pd.read_sql(
            query,
            pgsql_engine,
            params={
                'room_id': room_id,
                'start_date': start_date,
                'end_date': end_date
            }
        )
        
        if df.empty:
            return {'error': 'No data available for trend analysis'}
        
        trend_analysis = {
            'period': f"{start_date} to {end_date}",
            'data_points': len(df),
            'temperature_trend': calculate_trend(df['temp_median'].dropna()),
            'humidity_trend': calculate_trend(df['humidity_median'].dropna()),
            'co2_trend': calculate_trend(df['co2_median'].dropna()),
        }
        
        return trend_analysis
        
    except Exception as e:
        logger.error(f"[ENV_PROCESSOR] 获取环境趋势分析失败: {e}")
        return {'error': str(e)}

def calculate_trend(data: pd.Series) -> Dict[str, Any]:
    """
    计算数据趋势
    Args:
        data: 数据序列
    Returns:
        Dict[str, Any]: 趋势信息
    """
    if data.empty or len(data) < 2:
        return {'trend': 'insufficient_data'}
    
    try:
        x = range(len(data))
        slope = pd.Series(x).corr(data)
        
        trend_info = {
            'slope': float(slope) if not pd.isna(slope) else 0,
            'direction': 'increasing' if slope > 0.1 else 'decreasing' if slope < -0.1 else 'stable',
            'min_value': float(data.min()),
            'max_value': float(data.max()),
            'avg_value': float(data.mean()),
            'std_value': float(data.std())
        }
        
        return trend_info
        
    except Exception as e:
        logger.error(f"[ENV_PROCESSOR] 计算趋势失败: {e}")
        return {'trend': 'calculation_error', 'error': str(e)}

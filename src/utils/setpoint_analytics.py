"""
设定点变更分析模块
用于分析设定点变更的趋势、模式和统计信息
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
import pandas as pd
import numpy as np
from loguru import logger
from sqlalchemy import text

from global_const.global_const import pgsql_engine


class SetpointAnalytics:
    """设定点变更分析器"""

    def __init__(self):
        """初始化分析器"""
        logger.info("Initialized setpoint analytics")

    def get_change_statistics(
        self,
        room_id: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        获取设定点变更统计信息

        Args:
            room_id: 库房号，None表示所有库房
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            统计信息字典
        """
        try:
            # 构建查询条件
            where_conditions = []
            params = {}

            if room_id:
                where_conditions.append("room_id = :room_id")
                params["room_id"] = room_id

            if start_date:
                where_conditions.append("change_time >= :start_date")
                params["start_date"] = start_date

            if end_date:
                where_conditions.append("change_time <= :end_date")
                params["end_date"] = end_date

            where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"

            # 基础统计查询
            basic_stats_query = f"""
            SELECT 
                COUNT(*) as total_changes,
                COUNT(DISTINCT room_id) as rooms_count,
                COUNT(DISTINCT device_type) as device_types_count,
                COUNT(DISTINCT device_name) as devices_count,
                COUNT(DISTINCT point_name) as points_count,
                MIN(change_time) as earliest_change,
                MAX(change_time) as latest_change,
                AVG(ABS(current_value - previous_value)) as avg_change_magnitude,
                MAX(ABS(current_value - previous_value)) as max_change_magnitude
            FROM device_setpoint_changes
            WHERE {where_clause}
            """

            with pgsql_engine.connect() as conn:
                result = conn.execute(text(basic_stats_query), params).fetchone()

                basic_stats = {
                    "total_changes": result.total_changes or 0,
                    "rooms_count": result.rooms_count or 0,
                    "device_types_count": result.device_types_count or 0,
                    "devices_count": result.devices_count or 0,
                    "points_count": result.points_count or 0,
                    "earliest_change": result.earliest_change,
                    "latest_change": result.latest_change,
                    "avg_change_magnitude": float(result.avg_change_magnitude or 0),
                    "max_change_magnitude": float(result.max_change_magnitude or 0),
                }

            # 按设备类型统计
            device_type_query = f"""
            SELECT 
                device_type,
                COUNT(*) as change_count,
                AVG(ABS(current_value - previous_value)) as avg_magnitude
            FROM device_setpoint_changes
            WHERE {where_clause}
            GROUP BY device_type
            ORDER BY change_count DESC
            """

            device_type_stats = pd.read_sql(
                device_type_query, pgsql_engine, params=params
            )

            # 按库房统计
            room_query = f"""
            SELECT 
                room_id,
                COUNT(*) as change_count,
                COUNT(DISTINCT device_type) as device_types,
                AVG(ABS(current_value - previous_value)) as avg_magnitude
            FROM device_setpoint_changes
            WHERE {where_clause}
            GROUP BY room_id
            ORDER BY change_count DESC
            """

            room_stats = pd.read_sql(room_query, pgsql_engine, params=params)

            # 按测点统计
            point_query = f"""
            SELECT 
                device_type,
                point_name,
                point_description,
                COUNT(*) as change_count,
                AVG(ABS(current_value - previous_value)) as avg_magnitude
            FROM device_setpoint_changes
            WHERE {where_clause}
            GROUP BY device_type, point_name, point_description
            ORDER BY change_count DESC
            LIMIT 20
            """

            point_stats = pd.read_sql(point_query, pgsql_engine, params=params)

            return {
                "basic_stats": basic_stats,
                "device_type_stats": device_type_stats.to_dict("records"),
                "room_stats": room_stats.to_dict("records"),
                "point_stats": point_stats.to_dict("records"),
            }

        except Exception as e:
            logger.error(f"Failed to get change statistics: {e}")
            return {}

    def get_hourly_change_pattern(
        self, room_id: Optional[str] = None, days_back: int = 7
    ) -> pd.DataFrame:
        """
        获取按小时的变更模式分析

        Args:
            room_id: 库房号
            days_back: 往前分析的天数

        Returns:
            按小时统计的变更数据
        """
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)

            where_conditions = [
                "change_time >= :start_date",
                "change_time <= :end_date",
            ]
            params = {"start_date": start_date, "end_date": end_date}

            if room_id:
                where_conditions.append("room_id = :room_id")
                params["room_id"] = room_id

            where_clause = " AND ".join(where_conditions)

            query = f"""
            SELECT 
                EXTRACT(hour FROM change_time) as hour,
                COUNT(*) as change_count,
                COUNT(DISTINCT device_name) as devices_affected,
                AVG(ABS(current_value - previous_value)) as avg_magnitude
            FROM device_setpoint_changes
            WHERE {where_clause}
            GROUP BY EXTRACT(hour FROM change_time)
            ORDER BY hour
            """

            df = pd.read_sql(query, pgsql_engine, params=params)
            return df

        except Exception as e:
            logger.error(f"Failed to get hourly change pattern: {e}")
            return pd.DataFrame()

    def get_device_change_frequency(
        self, room_id: Optional[str] = None, days_back: int = 30
    ) -> pd.DataFrame:
        """
        获取设备变更频率分析

        Args:
            room_id: 库房号
            days_back: 往前分析的天数

        Returns:
            设备变更频率数据
        """
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)

            where_conditions = [
                "change_time >= :start_date",
                "change_time <= :end_date",
            ]
            params = {"start_date": start_date, "end_date": end_date}

            if room_id:
                where_conditions.append("room_id = :room_id")
                params["room_id"] = room_id

            where_clause = " AND ".join(where_conditions)

            query = f"""
            SELECT 
                room_id,
                device_type,
                device_name,
                COUNT(*) as total_changes,
                COUNT(DISTINCT point_name) as points_changed,
                COUNT(DISTINCT DATE(change_time)) as days_with_changes,
                MIN(change_time) as first_change,
                MAX(change_time) as last_change,
                AVG(ABS(current_value - previous_value)) as avg_magnitude,
                ROUND(COUNT(*)::numeric / :days_back, 2) as changes_per_day
            FROM device_setpoint_changes
            WHERE {where_clause}
            GROUP BY room_id, device_type, device_name
            ORDER BY total_changes DESC
            """

            params["days_back"] = days_back
            df = pd.read_sql(query, pgsql_engine, params=params)
            return df

        except Exception as e:
            logger.error(f"Failed to get device change frequency: {e}")
            return pd.DataFrame()

    def get_change_timeline(
        self,
        room_id: Optional[str] = None,
        device_name: Optional[str] = None,
        point_name: Optional[str] = None,
        hours_back: int = 24,
    ) -> pd.DataFrame:
        """
        获取变更时间线

        Args:
            room_id: 库房号
            device_name: 设备名称
            point_name: 测点名称
            hours_back: 往前查询的小时数

        Returns:
            变更时间线数据
        """
        try:
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=hours_back)

            where_conditions = [
                "change_time >= :start_time",
                "change_time <= :end_time",
            ]
            params = {"start_time": start_time, "end_time": end_time}

            if room_id:
                where_conditions.append("room_id = :room_id")
                params["room_id"] = room_id

            if device_name:
                where_conditions.append("device_name = :device_name")
                params["device_name"] = device_name

            if point_name:
                where_conditions.append("point_name = :point_name")
                params["point_name"] = point_name

            where_clause = " AND ".join(where_conditions)

            query = f"""
            SELECT 
                change_time,
                room_id,
                device_type,
                device_name,
                point_name,
                point_description,
                previous_value,
                current_value,
                ABS(current_value - previous_value) as delta_value,
                change_type
            FROM device_setpoint_changes
            WHERE {where_clause}
            ORDER BY change_time DESC
            """

            df = pd.read_sql(query, pgsql_engine, params=params)
            return df

        except Exception as e:
            logger.error(f"Failed to get change timeline: {e}")
            return pd.DataFrame()

    def detect_abnormal_changes(
        self, room_id: Optional[str] = None, days_back: int = 7
    ) -> List[Dict[str, Any]]:
        """
        检测异常变更模式

        Args:
            room_id: 库房号
            days_back: 往前分析的天数

        Returns:
            异常变更列表
        """
        try:
            # 获取设备变更频率
            freq_df = self.get_device_change_frequency(room_id, days_back)

            if freq_df.empty:
                return []

            abnormal_changes = []

            # 检测高频变更设备（变更次数超过平均值2倍标准差）
            if len(freq_df) > 1:
                mean_changes = freq_df["total_changes"].mean()
                std_changes = freq_df["total_changes"].std()
                threshold = mean_changes + 2 * std_changes

                high_freq_devices = freq_df[freq_df["total_changes"] > threshold]

                for _, device in high_freq_devices.iterrows():
                    abnormal_changes.append(
                        {
                            "type": "high_frequency",
                            "room_id": device["room_id"],
                            "device_name": device["device_name"],
                            "device_type": device["device_type"],
                            "total_changes": int(device["total_changes"]),
                            "changes_per_day": float(device["changes_per_day"]),
                            "description": f"设备变更频率异常高: {device['changes_per_day']:.1f}次/天",
                        }
                    )

            # 检测大幅度变更
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)

            where_conditions = [
                "change_time >= :start_date",
                "change_time <= :end_date",
            ]
            params = {"start_date": start_date, "end_date": end_date}

            if room_id:
                where_conditions.append("room_id = :room_id")
                params["room_id"] = room_id

            where_clause = " AND ".join(where_conditions)

            # 查找大幅度变更（变更幅度超过平均值2倍标准差）
            magnitude_query = f"""
            SELECT 
                room_id,
                device_name,
                device_type,
                point_name,
                change_time,
                previous_value,
                current_value,
                ABS(current_value - previous_value) as change_magnitude,
                (SELECT AVG(ABS(current_value - previous_value)) FROM device_setpoint_changes WHERE {where_clause}) as avg_magnitude,
                (SELECT STDDEV(ABS(current_value - previous_value)) FROM device_setpoint_changes WHERE {where_clause}) as std_magnitude
            FROM device_setpoint_changes
            WHERE {where_clause} AND ABS(current_value - previous_value) > 0
            """

            magnitude_df = pd.read_sql(magnitude_query, pgsql_engine, params=params)

            if not magnitude_df.empty and len(magnitude_df) > 1:
                avg_magnitude = magnitude_df["avg_magnitude"].iloc[0]
                std_magnitude = magnitude_df["std_magnitude"].iloc[0]

                if std_magnitude > 0:
                    magnitude_threshold = avg_magnitude + 2 * std_magnitude
                    large_changes = magnitude_df[
                        magnitude_df["change_magnitude"] > magnitude_threshold
                    ]

                    for _, change in large_changes.iterrows():
                        abnormal_changes.append(
                            {
                                "type": "large_magnitude",
                                "room_id": change["room_id"],
                                "device_name": change["device_name"],
                                "device_type": change["device_type"],
                                "point_name": change["point_name"],
                                "change_time": change["change_time"],
                                "change_magnitude": float(change["change_magnitude"]),
                                "description": f"变更幅度异常大: {change['change_magnitude']:.2f}",
                            }
                        )

            logger.info(f"Detected {len(abnormal_changes)} abnormal changes")
            return abnormal_changes

        except Exception as e:
            logger.error(f"Failed to detect abnormal changes: {e}")
            return []

    def generate_summary_report(
        self, room_id: Optional[str] = None, days_back: int = 7
    ) -> Dict[str, Any]:
        """
        生成设定点变更摘要报告

        Args:
            room_id: 库房号
            days_back: 往前分析的天数

        Returns:
            摘要报告
        """
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)

            # 获取基础统计
            stats = self.get_change_statistics(room_id, start_date, end_date)

            # 获取小时模式
            hourly_pattern = self.get_hourly_change_pattern(room_id, days_back)

            # 获取设备频率
            device_frequency = self.get_device_change_frequency(room_id, days_back)

            # 检测异常
            abnormal_changes = self.detect_abnormal_changes(room_id, days_back)

            # 计算活跃时段
            active_hours = []
            if not hourly_pattern.empty:
                avg_changes_per_hour = hourly_pattern["change_count"].mean()
                active_hours = hourly_pattern[
                    hourly_pattern["change_count"] > avg_changes_per_hour
                ]["hour"].tolist()

            # 计算最活跃的设备
            most_active_devices = []
            if not device_frequency.empty:
                top_devices = device_frequency.head(5)
                most_active_devices = top_devices[
                    ["device_name", "device_type", "total_changes", "changes_per_day"]
                ].to_dict("records")

            report = {
                "report_period": {
                    "start_date": start_date,
                    "end_date": end_date,
                    "days": days_back,
                    "room_id": room_id or "all_rooms",
                },
                "summary": stats.get("basic_stats", {}),
                "active_hours": active_hours,
                "most_active_devices": most_active_devices,
                "device_type_breakdown": stats.get("device_type_stats", []),
                "room_breakdown": stats.get("room_stats", []),
                "top_changed_points": stats.get("point_stats", [])[:10],
                "abnormal_changes": abnormal_changes,
                "generated_at": datetime.now(),
            }

            logger.info(f"Generated summary report for {room_id or 'all rooms'}")
            return report

        except Exception as e:
            logger.error(f"Failed to generate summary report: {e}")
            return {}


def create_setpoint_analytics() -> SetpointAnalytics:
    """创建设定点分析器实例"""
    return SetpointAnalytics()


if __name__ == "__main__":
    # 测试代码
    analytics = create_setpoint_analytics()

    # 生成摘要报告
    report = analytics.generate_summary_report(room_id="611", days_back=7)

    if report:
        print("📊 设定点变更摘要报告")
        print("=" * 50)

        summary = report.get("summary", {})
        print(f"总变更次数: {summary.get('total_changes', 0)}")
        print(f"涉及设备数: {summary.get('devices_count', 0)}")
        print(f"平均变更幅度: {summary.get('avg_change_magnitude', 0):.2f}")

        active_hours = report.get("active_hours", [])
        if active_hours:
            print(f"活跃时段: {', '.join(map(str, active_hours))}时")

        abnormal = report.get("abnormal_changes", [])
        if abnormal:
            print(f"异常变更: {len(abnormal)} 个")
            for change in abnormal[:3]:
                print(f"  - {change['description']}")
    else:
        print("❌ 报告生成失败")

#!/usr/bin/env python3
"""
全面设定点变更监控分析脚本

按照用户要求执行每小时设定点变更监控任务的具体操作步骤：
1. 查看过去24小时内的设定点变更情况
2. 使用batch_monitor_setpoint_changes函数执行批量分析
3. 分析完成后查询数据库验证数据完整性
4. 检查系统运行状态
5. 逐步扩展分析时间范围至2025-12-19
6. 验证每次分析结果的正确性
7. 记录关键指标
8. 异常处理和报告
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import time

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path

ensure_src_path()

try:
    from utils.setpoint_change_monitor import (
        batch_monitor_setpoint_changes,
        validate_batch_monitoring_environment,
        create_setpoint_monitor_table,
    )
    from global_const.global_const import pgsql_engine
    from sqlalchemy import text
    from loguru import logger
except ImportError as e:
    print(f"❌ 导入模块失败: {e}")
    print("请确保已安装所需依赖包并激活虚拟环境")
    sys.exit(1)


class SetpointAnalysisManager:
    """设定点分析管理器"""

    def __init__(self):
        self.analysis_results = []
        self.total_changes_detected = 0
        self.total_records_stored = 0
        self.analysis_start_time = datetime.now()

    def step1_check_past_24h_changes(self):
        """步骤1: 查看过去24小时内的设定点变更情况"""
        print("🔍 步骤1: 查看过去24小时内的设定点变更情况")
        print("=" * 60)

        try:
            with pgsql_engine.connect() as conn:
                # 检查表是否存在
                result = conn.execute(
                    text("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'device_setpoint_changes'
                    )
                """)
                )
                table_exists = result.scalar()

                if table_exists:
                    # 查询过去24小时的记录
                    result = conn.execute(
                        text("""
                        SELECT 
                            COUNT(*) as total_records,
                            COUNT(DISTINCT room_id) as rooms_count,
                            COUNT(DISTINCT device_type) as device_types_count,
                            MIN(change_time) as earliest_change,
                            MAX(change_time) as latest_change
                        FROM device_setpoint_changes 
                        WHERE change_time >= NOW() - INTERVAL '24 hours'
                    """)
                    )

                    row = result.fetchone()
                    if row and row[0] > 0:
                        print(f"📊 过去24小时设定点变更统计:")
                        print(f"   总记录数: {row[0]}")
                        print(f"   涉及库房: {row[1]}")
                        print(f"   设备类型: {row[2]}")
                        print(f"   最早变更: {row[3]}")
                        print(f"   最新变更: {row[4]}")

                        # 按库房统计
                        result = conn.execute(
                            text("""
                            SELECT room_id, COUNT(*) as change_count
                            FROM device_setpoint_changes 
                            WHERE change_time >= NOW() - INTERVAL '24 hours'
                            GROUP BY room_id
                            ORDER BY change_count DESC
                        """)
                        )

                        print(f"\\n📍 各库房变更统计:")
                        for room_row in result:
                            print(f"   库房 {room_row[0]}: {room_row[1]} 个变更")

                    else:
                        print("📊 过去24小时无设定点变更记录")

                    # 按设备类型统计
                    result = conn.execute(
                        text("""
                        SELECT device_type, COUNT(*) as change_count
                        FROM device_setpoint_changes 
                        WHERE change_time >= NOW() - INTERVAL '24 hours'
                        GROUP BY device_type
                        ORDER BY change_count DESC
                    """)
                    )

                    device_stats = result.fetchall()
                    if device_stats:
                        print(f"\\n🔧 设备类型变更统计:")
                        for device_row in device_stats:
                            print(f"   {device_row[0]}: {device_row[1]} 个变更")
                else:
                    print("⚠️ 设定点变更表不存在，将在执行监控时创建")

        except Exception as e:
            print(f"❌ 数据库查询失败: {e}")
            return False

        return True

    def step2_execute_batch_analysis(self, start_time, end_time, description=""):
        """步骤2: 使用batch_monitor_setpoint_changes函数执行批量分析"""
        print(f"\\n🚀 步骤2: 执行批量分析 {description}")
        print("=" * 60)

        time_range = end_time - start_time
        print(
            f"分析时间范围: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        print(f"时间跨度: {time_range.days}天 {time_range.seconds // 3600}小时")

        try:
            # 执行批量监控
            print("\\n🔍 正在执行批量设定点变更分析...")
            analysis_start = datetime.now()

            result = batch_monitor_setpoint_changes(
                start_time=start_time, end_time=end_time, store_results=True
            )

            analysis_duration = (datetime.now() - analysis_start).total_seconds()

            if result["success"]:
                print("✅ 批量分析执行成功")

                # 记录分析结果
                analysis_record = {
                    "start_time": start_time,
                    "end_time": end_time,
                    "description": description,
                    "total_rooms": result["total_rooms"],
                    "successful_rooms": result["successful_rooms"],
                    "total_changes": result["total_changes"],
                    "stored_records": result["stored_records"],
                    "processing_time": result["processing_time"],
                    "analysis_duration": analysis_duration,
                    "changes_by_room": result["changes_by_room"],
                    "error_rooms": result["error_rooms"],
                }

                self.analysis_results.append(analysis_record)
                self.total_changes_detected += result["total_changes"]
                self.total_records_stored += result["stored_records"]

                # 显示详细统计
                print(f"\\n📊 分析结果统计:")
                print(
                    f"   处理库房数: {result['successful_rooms']}/{result['total_rooms']}"
                )
                print(f"   检测变更数: {result['total_changes']} 个")
                print(f"   存储记录数: {result['stored_records']} 条")
                print(f"   处理耗时: {result['processing_time']:.2f} 秒")
                print(f"   分析耗时: {analysis_duration:.2f} 秒")

                if result["error_rooms"]:
                    print(f"   失败库房: {result['error_rooms']}")

                # 显示各库房统计
                print(f"\\n🏠 各库房变更详情:")
                for room_id, change_count in result["changes_by_room"].items():
                    status = "🔴" if change_count > 0 else "🟢"
                    print(f"   {status} 库房 {room_id}: {change_count} 个变更")

                return result
            else:
                print("❌ 批量分析执行失败")
                return None

        except Exception as e:
            print(f"❌ 批量分析异常: {e}")
            return None

    def step3_verify_data_integrity(self, expected_changes=None):
        """步骤3: 分析完成后查询数据库验证数据完整性和准确性"""
        print(f"\\n🔍 步骤3: 验证数据完整性和准确性")
        print("=" * 60)

        try:
            with pgsql_engine.connect() as conn:
                # 查询最近存储的记录
                result = conn.execute(
                    text("""
                    SELECT 
                        COUNT(*) as total_records,
                        COUNT(DISTINCT room_id) as rooms_count,
                        COUNT(DISTINCT device_type) as device_types_count,
                        COUNT(DISTINCT change_type) as change_types_count,
                        MIN(change_time) as earliest_change,
                        MAX(change_time) as latest_change,
                        MIN(detection_time) as earliest_detection,
                        MAX(detection_time) as latest_detection
                    FROM device_setpoint_changes 
                    WHERE detection_time >= :start_time
                """),
                    {"start_time": self.analysis_start_time},
                )

                row = result.fetchone()
                if row:
                    print(f"📊 数据完整性验证:")
                    print(f"   存储记录数: {row[0]}")
                    print(f"   涉及库房数: {row[1]}")
                    print(f"   设备类型数: {row[2]}")
                    print(f"   变更类型数: {row[3]}")
                    print(f"   变更时间范围: {row[4]} ~ {row[5]}")
                    print(f"   检测时间范围: {row[6]} ~ {row[7]}")

                    # 验证数据一致性
                    if expected_changes is not None:
                        if row[0] == expected_changes:
                            print(
                                f"✅ 数据一致性验证通过: 存储记录数({row[0]})与预期({expected_changes})一致"
                            )
                        else:
                            print(
                                f"⚠️ 数据一致性异常: 存储记录数({row[0]})与预期({expected_changes})不一致"
                            )

                    # 检查数据质量
                    result = conn.execute(
                        text("""
                        SELECT 
                            COUNT(CASE WHEN previous_value IS NULL THEN 1 END) as null_previous,
                            COUNT(CASE WHEN current_value IS NULL THEN 1 END) as null_current,
                            COUNT(CASE WHEN ABS(current_value - previous_value) = 0 THEN 1 END) as zero_magnitude
                        FROM device_setpoint_changes 
                        WHERE detection_time >= :start_time
                    """),
                        {"start_time": self.analysis_start_time},
                    )

                    quality_row = result.fetchone()
                    if quality_row:
                        print(f"\\n🔍 数据质量检查:")
                        print(f"   空的前值记录: {quality_row[0]}")
                        print(f"   空的当前值记录: {quality_row[1]}")
                        print(f"   零变化幅度记录: {quality_row[2]}")

                        if all(count == 0 for count in quality_row):
                            print("✅ 数据质量检查通过")
                        else:
                            print("⚠️ 发现数据质量问题")

                # 按变更类型统计
                result = conn.execute(
                    text("""
                    SELECT change_type, COUNT(*) as count
                    FROM device_setpoint_changes 
                    WHERE detection_time >= :start_time
                    GROUP BY change_type
                    ORDER BY count DESC
                """),
                    {"start_time": self.analysis_start_time},
                )

                change_type_stats = result.fetchall()
                if change_type_stats:
                    print(f"\\n📈 变更类型分布:")
                    for type_row in change_type_stats:
                        print(f"   {type_row[0]}: {type_row[1]} 个变更")

                return True

        except Exception as e:
            print(f"❌ 数据完整性验证失败: {e}")
            return False

    def step4_check_system_status(self):
        """步骤4: 检查系统运行状态，确认监控任务执行正常"""
        print(f"\\n🔧 步骤4: 检查系统运行状态")
        print("=" * 60)

        try:
            # 检查环境
            print("🔍 验证监控环境...")
            if validate_batch_monitoring_environment():
                print("✅ 监控环境正常")
            else:
                print("❌ 监控环境异常")
                return False

            # 检查数据库连接
            print("\\n🔍 检查数据库连接...")
            with pgsql_engine.connect() as conn:
                result = conn.execute(text("SELECT NOW()"))
                db_time = result.scalar()
                print(f"✅ 数据库连接正常，当前时间: {db_time}")

            # 检查表结构
            print("\\n🔍 检查表结构...")
            with pgsql_engine.connect() as conn:
                result = conn.execute(
                    text("""
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns 
                    WHERE table_name = 'device_setpoint_changes'
                    ORDER BY ordinal_position
                """)
                )

                columns = result.fetchall()
                if columns:
                    print("✅ 表结构正常:")
                    for col in columns:
                        nullable = "NULL" if col[2] == "YES" else "NOT NULL"
                        print(f"   {col[0]}: {col[1]} {nullable}")
                else:
                    print("❌ 表结构异常")
                    return False

            # 检查分析结果
            print(f"\\n🔍 检查分析结果...")
            if self.analysis_results:
                print(f"✅ 已完成 {len(self.analysis_results)} 次分析")
                print(f"   累计检测变更: {self.total_changes_detected} 个")
                print(f"   累计存储记录: {self.total_records_stored} 条")

                # 检查是否有错误
                error_count = sum(
                    len(result["error_rooms"]) for result in self.analysis_results
                )
                if error_count == 0:
                    print("✅ 无错误记录")
                else:
                    print(f"⚠️ 发现 {error_count} 个库房处理错误")
            else:
                print("⚠️ 尚未执行分析")

            return True

        except Exception as e:
            print(f"❌ 系统状态检查失败: {e}")
            return False

    def step5_expand_analysis_timerange(self, target_date):
        """步骤5: 逐步扩展分析时间范围"""
        print(f"\\n📅 步骤5: 扩展分析时间范围至 {target_date}")
        print("=" * 60)

        current_time = datetime.now()
        target_datetime = datetime.combine(target_date, datetime.min.time())

        if target_datetime >= current_time:
            print("❌ 目标日期不能是未来时间")
            return False

        # 计算需要分析的时间段
        total_days = (current_time.date() - target_date).days
        print(f"需要分析 {total_days} 天的历史数据")

        # 分批处理，每次处理7天
        batch_days = 7
        batches = []

        current_end = current_time
        while current_end.date() > target_date:
            batch_start = max(current_end - timedelta(days=batch_days), target_datetime)
            batches.append((batch_start, current_end))
            current_end = batch_start

        print(f"\\n📋 分析计划: 共 {len(batches)} 个批次")
        for i, (start, end) in enumerate(batches, 1):
            days = (end - start).days
            print(
                f"   批次 {i}: {start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')} ({days}天)"
            )

        # 执行分批分析
        successful_batches = 0
        for i, (batch_start, batch_end) in enumerate(batches, 1):
            print(f"\\n🔍 执行批次 {i}/{len(batches)}")

            # 执行分析
            result = self.step2_execute_batch_analysis(
                batch_start, batch_end, f"(批次 {i}/{len(batches)})"
            )

            if result:
                # 验证结果
                if self.step3_verify_data_integrity(result["total_changes"]):
                    successful_batches += 1
                    print(f"✅ 批次 {i} 完成")
                else:
                    print(f"⚠️ 批次 {i} 数据验证失败")
            else:
                print(f"❌ 批次 {i} 执行失败")

            # 检查系统状态
            if not self.step4_check_system_status():
                print(f"❌ 系统状态异常，停止分析")
                break

            # 批次间休息
            if i < len(batches):
                print("⏳ 批次间休息 2 秒...")
                time.sleep(2)

        print(f"\\n📊 扩展分析完成: {successful_batches}/{len(batches)} 个批次成功")
        return successful_batches == len(batches)

    def generate_final_report(self):
        """生成最终分析报告"""
        print(f"\\n📋 最终分析报告")
        print("=" * 60)

        if not self.analysis_results:
            print("❌ 无分析结果")
            return

        total_analysis_time = (
            datetime.now() - self.analysis_start_time
        ).total_seconds()

        print(f"📊 总体统计:")
        print(f"   分析批次数: {len(self.analysis_results)}")
        print(f"   累计检测变更: {self.total_changes_detected} 个")
        print(f"   累计存储记录: {self.total_records_stored} 条")
        print(f"   总分析时间: {total_analysis_time:.2f} 秒")

        # 时间范围统计
        if self.analysis_results:
            earliest_start = min(
                result["start_time"] for result in self.analysis_results
            )
            latest_end = max(result["end_time"] for result in self.analysis_results)
            print(f"   分析时间范围: {earliest_start} ~ {latest_end}")

        # 库房统计
        all_rooms = set()
        room_changes = {}

        for result in self.analysis_results:
            for room_id, changes in result["changes_by_room"].items():
                all_rooms.add(room_id)
                room_changes[room_id] = room_changes.get(room_id, 0) + changes

        print(f"\\n🏠 库房统计:")
        print(f"   涉及库房数: {len(all_rooms)}")
        for room_id in sorted(all_rooms):
            total_changes = room_changes.get(room_id, 0)
            print(f"   库房 {room_id}: {total_changes} 个变更")

        # 性能统计
        total_processing_time = sum(
            result["processing_time"] for result in self.analysis_results
        )
        if total_processing_time > 0:
            processing_rate = self.total_changes_detected / total_processing_time
            print(f"\\n⚡ 性能统计:")
            print(f"   总处理时间: {total_processing_time:.2f} 秒")
            print(f"   处理速度: {processing_rate:.1f} 变更/秒")

        # 数据质量统计
        storage_rate = (
            (self.total_records_stored / self.total_changes_detected * 100)
            if self.total_changes_detected > 0
            else 0
        )
        print(f"\\n📈 数据质量:")
        print(f"   存储成功率: {storage_rate:.1f}%")

        if storage_rate == 100:
            print("✅ 所有检测到的变更都已成功存储")
        else:
            print("⚠️ 部分变更记录存储失败")


def main():
    """主函数"""
    print("🚀 全面设定点变更监控分析系统")
    print("=" * 80)

    # 创建分析管理器
    manager = SetpointAnalysisManager()

    try:
        # 步骤1: 查看过去24小时内的设定点变更情况
        if not manager.step1_check_past_24h_changes():
            print("❌ 步骤1失败，终止分析")
            return

        # 步骤2: 执行过去24小时的批量分析
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=24)

        result = manager.step2_execute_batch_analysis(
            start_time, end_time, "(过去24小时)"
        )

        if not result:
            print("❌ 步骤2失败，终止分析")
            return

        # 步骤3: 验证数据完整性
        if not manager.step3_verify_data_integrity(result["total_changes"]):
            print("❌ 步骤3失败，终止分析")
            return

        # 步骤4: 检查系统运行状态
        if not manager.step4_check_system_status():
            print("❌ 步骤4失败，终止分析")
            return

        # 步骤5: 扩展分析时间范围至2025-12-19
        target_date = datetime(2025, 12, 19).date()
        print(f"\\n🎯 开始扩展分析时间范围至 {target_date}")

        if manager.step5_expand_analysis_timerange(target_date):
            print("✅ 扩展分析完成")
        else:
            print("⚠️ 扩展分析部分完成")

        # 生成最终报告
        manager.generate_final_report()

        print(f"\\n🎉 全面设定点变更监控分析完成！")

    except KeyboardInterrupt:
        print(f"\\n⚠️ 用户中断执行")
        manager.generate_final_report()
    except Exception as e:
        print(f"\\n❌ 执行异常: {e}")
        import traceback

        traceback.print_exc()
        manager.generate_final_report()


if __name__ == "__main__":
    main()

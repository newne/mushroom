#!/usr/bin/env python3
"""
设备设定点变更监控脚本
用于监控指定库房或所有库房的设定点变化情况
"""

import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# 使用BASE_DIR统一管理路径
from global_const.paths import ensure_src_path

ensure_src_path()

from utils.setpoint_change_monitor import (
    create_setpoint_monitor,
    create_setpoint_monitor_table,
    DeviceSetpointChangeMonitor,
)
from utils.loguru_setting import loguru_setting
from loguru import logger


def monitor_single_room(room_id: str, hours_back: int = 1, store_results: bool = True):
    """监控单个库房的设定点变更"""
    print(f"🔍 监控库房 {room_id} 的设定点变更（过去 {hours_back} 小时）")
    print("=" * 60)

    try:
        monitor = create_setpoint_monitor()

        # 监控设定点变更
        changes = monitor.monitor_room_setpoint_changes(room_id, hours_back)

        if not changes:
            print(f"ℹ️ 库房 {room_id} 在过去 {hours_back} 小时内未检测到设定点变更")
            return

        print(f"✅ 检测到 {len(changes)} 个设定点变更:")
        print()

        # 按设备类型分组显示
        device_types = {}
        for change in changes:
            device_type = change["device_type"]
            if device_type not in device_types:
                device_types[device_type] = []
            device_types[device_type].append(change)

        for device_type, type_changes in device_types.items():
            print(f"📱 {device_type.upper()} ({len(type_changes)} 个变更):")
            for change in type_changes:
                change_time = change["change_time"].strftime("%Y-%m-%d %H:%M:%S")
                print(f"   • {change['device_name']}.{change['point_name']}")
                print(f"     描述: {change['point_description']}")
                print(
                    f"     变更: {change['previous_value']} -> {change['current_value']}"
                )
                print(f"     时间: {change_time}")
                delta_value = change["current_value"] - change["previous_value"]
                print(f"     幅度: {abs(delta_value):.2f}")
                print()

        # 存储结果
        if store_results:
            print("💾 存储变更记录到数据库...")
            success = monitor.store_setpoint_changes(changes)
            if success:
                print("✅ 变更记录已成功存储到数据库")
            else:
                print("❌ 变更记录存储失败")

        return changes

    except Exception as e:
        print(f"❌ 监控库房 {room_id} 失败: {e}")
        logger.error(f"Failed to monitor room {room_id}: {e}")
        return None


def monitor_all_rooms(hours_back: int = 1, store_results: bool = True):
    """监控所有库房的设定点变更"""
    print(f"🔍 监控所有库房的设定点变更（过去 {hours_back} 小时）")
    print("=" * 60)

    try:
        monitor = create_setpoint_monitor()

        # 监控所有库房
        all_changes = monitor.monitor_all_rooms_setpoint_changes(hours_back)

        total_changes = sum(len(changes) for changes in all_changes.values())

        if total_changes == 0:
            print(f"ℹ️ 所有库房在过去 {hours_back} 小时内未检测到设定点变更")
            return

        print(f"✅ 总共检测到 {total_changes} 个设定点变更:")
        print()

        # 按库房显示结果
        for room_id, changes in all_changes.items():
            if not changes:
                print(f"📍 库房 {room_id}: 无变更")
                continue

            print(f"📍 库房 {room_id}: {len(changes)} 个变更")

            # 按设备类型统计
            device_stats = {}
            for change in changes:
                device_type = change["device_type"]
                device_stats[device_type] = device_stats.get(device_type, 0) + 1

            for device_type, count in device_stats.items():
                print(f"   • {device_type}: {count} 个变更")

            # 显示最近的几个变更
            recent_changes = sorted(
                changes, key=lambda x: x["change_time"], reverse=True
            )[:3]
            print("   最近变更:")
            for change in recent_changes:
                change_time = change["change_time"].strftime("%H:%M:%S")
                print(
                    f"     - {change['device_name']}.{change['point_name']}: "
                    f"{change['previous_value']} -> {change['current_value']} ({change_time})"
                )
            print()

        # 存储所有结果
        if store_results:
            print("💾 存储所有变更记录到数据库...")
            all_changes_list = []
            for changes in all_changes.values():
                all_changes_list.extend(changes)

            if all_changes_list:
                success = monitor.store_setpoint_changes(all_changes_list)
                if success:
                    print("✅ 所有变更记录已成功存储到数据库")
                else:
                    print("❌ 变更记录存储失败")

        return all_changes

    except Exception as e:
        print(f"❌ 监控所有库房失败: {e}")
        logger.error(f"Failed to monitor all rooms: {e}")
        return None


def show_setpoint_summary(room_id: str = None):
    """显示设定点配置摘要"""
    print("📋 设定点监控配置摘要（基于静态配置）")
    print("=" * 60)

    try:
        monitor = create_setpoint_monitor()

        print(f"📊 总计从静态配置加载 {len(monitor.setpoint_configs)} 个设定点监控配置")
        print()

        # 按设备类型分组显示配置
        device_configs = {}
        for config in monitor.setpoint_configs:
            device_type = config.device_type
            if device_type not in device_configs:
                device_configs[device_type] = []
            device_configs[device_type].append(config)

        for device_type, configs in device_configs.items():
            print(f"🔧 {device_type.upper()} ({len(configs)} 个监控点):")
            for config in configs:
                threshold_info = (
                    f", 阈值: {config.threshold}" if config.threshold else ""
                )
                enum_info = (
                    f", 枚举: {list(config.enum_mapping.keys())}"
                    if config.enum_mapping
                    else ""
                )
                print(
                    f"   • {config.point_alias} ({config.change_type.value}{threshold_info}{enum_info})"
                )
                print(f"     描述: {config.description}")
                print(f"     测点名: {config.point_name}")
            print()

        # 显示静态配置来源信息
        try:
            from global_const.global_const import static_settings

            datapoint_config = static_settings.mushroom.datapoint
            device_types_in_config = [
                key for key in datapoint_config.keys() if key != "remark"
            ]
            print(f"📁 静态配置中的设备类型: {', '.join(device_types_in_config)}")

            # 显示库房配置
            rooms_cfg = getattr(static_settings.mushroom, "rooms", {})
            if rooms_cfg:
                rooms = list(rooms_cfg.keys())
                print(f"🏠 配置的库房: {', '.join(rooms)}")
            else:
                print("⚠️ 静态配置中未找到库房配置")
        except Exception as e:
            print(f"⚠️ 读取静态配置信息时出错: {e}")

    except Exception as e:
        print(f"❌ 显示配置摘要失败: {e}")
        logger.error(f"Failed to show setpoint summary: {e}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="设备设定点变更监控工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 监控库房611过去1小时的设定点变更
  python scripts/monitor_setpoint_changes.py --room-id 611 --hours 1
  
  # 监控所有库房过去2小时的设定点变更
  python scripts/monitor_setpoint_changes.py --all-rooms --hours 2
  
  # 显示设定点配置摘要
  python scripts/monitor_setpoint_changes.py --show-config
  
  # 监控但不存储到数据库
  python scripts/monitor_setpoint_changes.py --room-id 611 --no-store
  
  # 创建数据库表
  python scripts/monitor_setpoint_changes.py --create-table
        """,
    )

    parser.add_argument("--room-id", type=str, help="指定库房号")
    parser.add_argument("--all-rooms", action="store_true", help="监控所有库房")
    parser.add_argument(
        "--hours", type=int, default=1, help="往前查询的小时数 (默认: 1)"
    )
    parser.add_argument("--no-store", action="store_true", help="不存储结果到数据库")
    parser.add_argument("--show-config", action="store_true", help="显示设定点配置摘要")
    parser.add_argument("--create-table", action="store_true", help="创建数据库表")

    args = parser.parse_args()

    # 初始化日志
    loguru_setting()

    print("🔧 设备设定点变更监控工具")
    print(f"⏰ 开始时间: {datetime.now()}")
    print()

    try:
        # 创建数据库表
        if args.create_table:
            print("🗄️ 创建数据库表...")
            create_setpoint_monitor_table()
            print("✅ 数据库表创建完成")
            return

        # 显示配置摘要
        if args.show_config:
            show_setpoint_summary()
            return

        store_results = not args.no_store

        if args.room_id:
            # 监控单个库房
            monitor_single_room(args.room_id, args.hours, store_results)
        elif args.all_rooms:
            # 监控所有库房
            monitor_all_rooms(args.hours, store_results)
        else:
            # 默认显示帮助
            parser.print_help()
            return

        print(f"\n⏰ 结束时间: {datetime.now()}")
        print("✅ 监控完成！")

    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断操作")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 程序执行失败: {e}")
        logger.error(f"Program execution failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

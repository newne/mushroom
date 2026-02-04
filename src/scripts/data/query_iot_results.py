#!/usr/bin/env python3
"""
查询决策分析静态配置和动态结果

该脚本用于查询数据库中存储的决策分析静态配置和动态结果，
支持按房间ID、设备类型、批次ID等进行过滤。

使用方法:
    # 查询所有静态配置
    python scripts/query_iot_results.py --type static

    # 查询特定房间的静态配置
    python scripts/query_iot_results.py --type static --room-id 611

    # 查询动态结果
    python scripts/query_iot_results.py --type dynamic --room-id 611 --limit 10

    # 查询变更记录
    python scripts/query_iot_results.py --type dynamic --changes-only --limit 20

    # 查询特定批次
    python scripts/query_iot_results.py --type dynamic --batch-id batch_611_20260123_122501

    # 显示详细信息
    python scripts/query_iot_results.py --type static --room-id 611 --verbose
"""

import argparse
import sys
from datetime import datetime, timedelta

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path

ensure_src_path()

from loguru import logger

from utils.create_table import (
    query_decision_analysis_dynamic_results,
    query_decision_analysis_static_configs,
)
from utils.loguru_setting import loguru_setting

# 初始化日志
loguru_setting(production=False)


def format_static_config_summary(config) -> str:
    """
    格式化静态配置摘要信息

    Args:
        config: 静态配置记录

    Returns:
        格式化的摘要字符串
    """
    summary_lines = [
        f"📋 Config ID: {config.id}",
        f"🏠 Room: {config.room_id}",
        f"🔧 Device: {config.device_type} ({config.device_alias})",
        f"📍 Point: {config.point_alias} ({config.point_name})",
        f"📝 Remark: {config.remark or 'N/A'}",
        f"🔄 Type: {config.change_type}",
        f"⚖️  Threshold: {config.threshold if config.threshold is not None else 'N/A'}",
        f"📊 Version: {config.config_version}",
        f"✅ Active: {'Yes' if config.is_active else 'No'}",
        f"🕒 Updated: {config.updated_at.strftime('%Y-%m-%d %H:%M:%S') if config.updated_at else 'N/A'}",
    ]

    if config.enum_mapping:
        enum_str = ", ".join([f"{k}={v}" for k, v in config.enum_mapping.items()])
        summary_lines.append(f"🏷️  Enum: {enum_str}")

    return "\n".join(summary_lines)


def format_dynamic_result_summary(result) -> str:
    """
    格式化动态结果摘要信息

    Args:
        result: 动态结果记录

    Returns:
        格式化的摘要字符串
    """
    change_icon = "🔄" if result.change else "➖"
    status_labels = {
        0: "pending",
        1: "accepted",
        2: "manual",
        3: "ignored",
    }
    status_icons = {
        0: "⏳",
        1: "✅",
        2: "🛠️",
        3: "🚫",
    }
    status_icon = status_icons.get(result.status, "❓")
    status_label = status_labels.get(result.status, str(result.status))

    summary_lines = [
        f"📋 Result ID: {result.id}",
        f"🏠 Room: {result.room_id}",
        f"📦 Batch: {result.batch_id}",
        f"🔧 Device: {result.device_type} ({result.device_alias})",
        f"📍 Point: {result.point_alias} ({result.point_name or 'N/A'})",
        f"{change_icon} Change: {'Yes' if result.change else 'No'}",
        f"🔄 Values: {result.old} → {result.new}",
        f"📊 Level: {result.level}",
        f"{status_icon} Status: {status_label}",
        f"🕒 Time: {result.time.strftime('%Y-%m-%d %H:%M:%S') if result.time else 'N/A'}",
    ]

    if result.reason:
        summary_lines.append(f"💭 Reason: {result.reason}")

    if result.apply_time:
        summary_lines.append(
            f"⚡ Applied: {result.apply_time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    return "\n".join(summary_lines)


def query_and_display_static_configs(args):
    """查询并显示静态配置"""
    logger.info("Querying static point configurations...")

    try:
        results = query_decision_analysis_static_configs(
            room_id=args.room_id,
            device_type=args.device_type,
            device_alias=args.device_alias,
            is_active=None if args.include_inactive else True,
            limit=args.limit,
        )

        if not results:
            print("No static configurations found.")
            return

        print(f"\nFound {len(results)} static configuration(s):")
        print("=" * 80)

        # 统计信息
        device_types = {}
        room_stats = {}

        for i, config in enumerate(results, 1):
            if args.verbose:
                print(f"\nConfiguration {i}:")
                print(format_static_config_summary(config))
                print("-" * 40)
            else:
                print(
                    f"{i:3d}. {config.room_id} | {config.device_type:15s} | {config.device_alias:20s} | {config.point_alias:15s} | {config.remark or 'N/A'}"
                )

            # 收集统计信息
            device_types[config.device_type] = (
                device_types.get(config.device_type, 0) + 1
            )
            room_stats[config.room_id] = room_stats.get(config.room_id, 0) + 1

        # 显示统计摘要
        print("\n" + "=" * 80)
        print("📊 SUMMARY STATISTICS:")
        print(f"   Total Configurations: {len(results)}")
        print(f"   Rooms: {', '.join(room_stats.keys())}")
        print("   Device Types:")
        for device_type, count in sorted(device_types.items()):
            print(f"     - {device_type}: {count}")
        print("=" * 80)

    except Exception as e:
        logger.error(f"Failed to query static configurations: {e}")


def query_and_display_dynamic_results(args):
    """查询并显示动态结果"""
    logger.info("Querying dynamic point results...")

    try:
        # 处理时间过滤
        start_time = None
        end_time = None

        if args.hours:
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=args.hours)
        elif args.days:
            end_time = datetime.now()
            start_time = end_time - timedelta(days=args.days)

        results = query_decision_analysis_dynamic_results(
            room_id=args.room_id,
            batch_id=args.batch_id,
            device_alias=args.device_alias,
            point_alias=args.point_alias,
            change_only=args.changes_only,
            status=args.status,
            start_time=start_time,
            end_time=end_time,
            limit=args.limit,
        )

        if not results:
            print("No dynamic results found.")
            return

        print(f"\nFound {len(results)} dynamic result(s):")
        print("=" * 80)

        # 统计信息
        batch_stats = {}
        change_stats = {"total": 0, "changes": 0}
        status_stats = {}
        device_stats = {}

        for i, result in enumerate(results, 1):
            if args.verbose:
                print(f"\nResult {i}:")
                print(format_dynamic_result_summary(result))
                print("-" * 40)
            else:
                change_icon = "🔄" if result.change else "➖"
                status_icon = {0: "⏳", 1: "✅", 2: "🛠️", 3: "🚫"}.get(
                    result.status, "❓"
                )
                time_str = result.time.strftime("%m-%d %H:%M") if result.time else "N/A"
                status_label = {
                    0: "pending",
                    1: "accepted",
                    2: "manual",
                    3: "ignored",
                }.get(result.status, str(result.status))
                print(
                    f"{i:3d}. {result.room_id} | {time_str} | {result.device_type:12s} | {result.point_alias:12s} | {change_icon} {result.old}→{result.new} | {status_icon} {status_label}"
                )

            # 收集统计信息
            batch_stats[result.batch_id] = batch_stats.get(result.batch_id, 0) + 1
            change_stats["total"] += 1
            if result.change:
                change_stats["changes"] += 1
            status_stats[result.status] = status_stats.get(result.status, 0) + 1
            device_stats[result.device_type] = (
                device_stats.get(result.device_type, 0) + 1
            )

        # 显示统计摘要
        print("\n" + "=" * 80)
        print("📊 SUMMARY STATISTICS:")
        print(f"   Total Results: {len(results)}")
        print(
            f"   Changes: {change_stats['changes']}/{change_stats['total']} ({change_stats['changes'] / change_stats['total'] * 100:.1f}%)"
        )
        print(f"   Batches: {len(batch_stats)}")

        if len(batch_stats) <= 5:  # 只显示少量批次的详情
            for batch_id, count in sorted(batch_stats.items()):
                print(f"     - {batch_id}: {count} results")

        print("   Status Distribution:")
        for status, count in sorted(status_stats.items()):
            print(f"     - {status}: {count}")

        print("   Device Types:")
        for device_type, count in sorted(device_stats.items()):
            print(f"     - {device_type}: {count}")

        print("=" * 80)

    except Exception as e:
        logger.error(f"Failed to query dynamic results: {e}")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Query decision analysis static configs and dynamic results"
    )

    # 查询类型
    parser.add_argument(
        "--type",
        choices=["static", "dynamic"],
        required=True,
        help="Type of data to query",
    )

    # 通用过滤参数
    parser.add_argument("--room-id", type=str, help="Room ID filter")
    parser.add_argument("--device-type", type=str, help="Device type filter")
    parser.add_argument("--device-alias", type=str, help="Device alias filter")
    parser.add_argument(
        "--limit", type=int, default=100, help="Maximum number of results"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Show detailed information"
    )

    # 静态配置特有参数
    parser.add_argument(
        "--include-inactive",
        action="store_true",
        help="Include inactive configurations (static only)",
    )

    # 动态结果特有参数
    parser.add_argument("--batch-id", type=str, help="Batch ID filter (dynamic only)")
    parser.add_argument(
        "--point-alias", type=str, help="Point alias filter (dynamic only)"
    )
    parser.add_argument(
        "--changes-only",
        action="store_true",
        help="Show only records with changes (dynamic only)",
    )
    parser.add_argument(
        "--status",
        type=int,
        choices=[0, 1, 2, 3],
        help="Status filter (dynamic only): 0=pending,1=accepted,2=manual,3=ignored",
    )
    parser.add_argument(
        "--hours", type=int, help="Show results from last N hours (dynamic only)"
    )
    parser.add_argument(
        "--days", type=int, help="Show results from last N days (dynamic only)"
    )

    args = parser.parse_args()

    try:
        if args.type == "static":
            query_and_display_static_configs(args)
        else:  # dynamic
            query_and_display_dynamic_results(args)

    except Exception as e:
        logger.error(f"Query failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

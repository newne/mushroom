#!/usr/bin/env python3
"""
查询增强决策分析结果

该脚本用于查询数据库中存储的增强决策分析结果，
支持按房间ID、时间范围和状态进行过滤。

使用方法:
    # 查询所有记录
    python scripts/query_decision_analysis_results.py
    
    # 查询特定房间的记录
    python scripts/query_decision_analysis_results.py --room-id 611
    
    # 查询最近的记录
    python scripts/query_decision_analysis_results.py --limit 5
    
    # 显示详细信息
    python scripts/query_decision_analysis_results.py --room-id 611 --verbose
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from utils.create_table import query_enhanced_decision_analysis_results
from utils.loguru_setting import loguru_setting
from loguru import logger

# 初始化日志
loguru_setting(production=False)


def format_record_summary(record) -> str:
    """
    格式化记录摘要信息
    
    Args:
        record: 数据库记录
        
    Returns:
        格式化的摘要字符串
    """
    summary_lines = [
        f"📋 Record ID: {record.id}",
        f"🏠 Room ID: {record.room_id}",
        f"📅 Analysis Time: {record.analysis_datetime}",
        f"📊 Status: {record.status}",
        f"📄 Format: {record.output_format}",
        f"⏱️  Processing Time: {record.processing_time:.2f}s",
    ]
    
    if record.multi_image_count:
        summary_lines.append(f"🖼️  Multi-image Count: {record.multi_image_count}")
    
    if record.core_objective:
        summary_lines.append(f"🎯 Objective: {record.core_objective[:100]}...")
    
    # 添加统计信息
    stats = record.get_summary_stats()
    if stats.get('total_monitoring_points'):
        summary_lines.append(f"📈 Monitoring Points: {stats['total_monitoring_points']}")
        summary_lines.append(f"🔄 Changes Required: {stats['changes_required']} ({stats['change_percentage']:.1f}%)")
    
    if record.warnings_count > 0:
        summary_lines.append(f"⚠️  Warnings: {record.warnings_count}")
    
    if record.errors_count > 0:
        summary_lines.append(f"❌ Errors: {record.errors_count}")
    
    return "\n".join(summary_lines)


def format_detailed_record(record) -> str:
    """
    格式化记录详细信息
    
    Args:
        record: 数据库记录
        
    Returns:
        格式化的详细信息字符串
    """
    lines = [
        "=" * 80,
        f"📋 Enhanced Decision Analysis Record",
        "=" * 80,
        f"ID: {record.id}",
        f"Room ID: {record.room_id}",
        f"Analysis Time: {record.analysis_datetime}",
        f"Status: {record.status}",
        f"Output Format: {record.output_format}",
        f"Processing Time: {record.processing_time:.2f}s",
        f"Created At: {record.created_at}",
        ""
    ]
    
    # 策略信息
    if record.core_objective:
        lines.extend([
            "🎯 Core Objective:",
            f"   {record.core_objective}",
            ""
        ])
    
    if record.priority_ranking:
        lines.extend([
            "📊 Priority Ranking:",
            *[f"   {i+1}. {priority}" for i, priority in enumerate(record.priority_ranking)],
            ""
        ])
    
    if record.key_risk_points:
        lines.extend([
            "⚠️  Key Risk Points:",
            *[f"   • {risk}" for risk in record.key_risk_points],
            ""
        ])
    
    # 多图像分析信息
    if record.multi_image_analysis:
        multi_img = record.multi_image_analysis
        lines.extend([
            "🖼️  Multi-image Analysis:",
            f"   Total Images: {multi_img.get('total_images_analyzed', 'N/A')}",
            f"   Confidence Score: {multi_img.get('confidence_score', 'N/A')}",
            f"   View Consistency: {multi_img.get('view_consistency', 'N/A')}",
            f"   Aggregation Method: {multi_img.get('aggregation_method', 'N/A')}",
            ""
        ])
    
    # 性能指标
    lines.extend([
        "⚡ Performance Metrics:",
        f"   Processing Time: {record.processing_time:.2f}s",
        f"   Analysis Time: {record.analysis_time:.2f}s" if record.analysis_time else "   Analysis Time: N/A",
        f"   Save Time: {record.save_time:.2f}s" if record.save_time else "   Save Time: N/A",
        f"   Memory Usage: {record.memory_usage_mb:.2f}MB" if record.memory_usage_mb else "   Memory Usage: N/A",
        ""
    ])
    
    # 数据源信息
    lines.extend([
        "📊 Data Sources:",
        f"   Data Sources Count: {record.data_sources_count or 'N/A'}",
        f"   Total Records Processed: {record.total_records_processed or 'N/A'}",
        f"   Multi-image Count: {record.multi_image_count or 'N/A'}",
        ""
    ])
    
    # 质量指标
    lines.extend([
        "🔍 Quality Metrics:",
        f"   Warnings: {record.warnings_count}",
        f"   Errors: {record.errors_count}",
        f"   LLM Fallback Used: {'Yes' if record.llm_fallback_used else 'No'}",
        ""
    ])
    
    # 输出文件信息
    if record.output_file_path:
        file_size_mb = record.output_file_size / (1024 * 1024) if record.output_file_size else 0
        lines.extend([
            "📁 Output File:",
            f"   Path: {record.output_file_path}",
            f"   Size: {file_size_mb:.2f}MB" if record.output_file_size else "   Size: N/A",
            ""
        ])
    
    # 摘要统计
    stats = record.get_summary_stats()
    if stats.get('total_monitoring_points'):
        lines.extend([
            "📈 Monitoring Points Summary:",
            f"   Total Points: {stats['total_monitoring_points']}",
            f"   Changes Required: {stats['changes_required']}",
            f"   Change Percentage: {stats['change_percentage']:.1f}%",
            ""
        ])
    
    lines.append("=" * 80)
    return "\n".join(lines)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="Query enhanced decision analysis results from database"
    )
    
    parser.add_argument(
        "--room-id",
        type=str,
        help="Filter by room ID"
    )
    
    parser.add_argument(
        "--status",
        type=str,
        choices=["success", "failed", "warning"],
        help="Filter by status"
    )
    
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of records to return (default: 10)"
    )
    
    parser.add_argument(
        "--days",
        type=int,
        help="Filter records from the last N days"
    )
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed information for each record"
    )
    
    args = parser.parse_args()
    
    try:
        # 计算时间范围
        start_date = None
        if args.days:
            start_date = datetime.now() - timedelta(days=args.days)
            logger.info(f"Filtering records from the last {args.days} days (since {start_date})")
        
        # 查询记录
        logger.info("Querying enhanced decision analysis results...")
        results = query_enhanced_decision_analysis_results(
            room_id=args.room_id,
            start_date=start_date,
            status=args.status,
            limit=args.limit
        )
        
        if not results:
            logger.info("No records found matching the criteria")
            return
        
        logger.info(f"Found {len(results)} records")
        print()
        
        # 显示结果
        for i, record in enumerate(results, 1):
            if args.verbose:
                print(format_detailed_record(record))
                if i < len(results):
                    print("\n" + "─" * 80 + "\n")
            else:
                print(f"Record {i}:")
                print(format_record_summary(record))
                print()
        
        # 显示汇总信息
        if len(results) > 1:
            print("=" * 80)
            print("📊 Summary Statistics:")
            print(f"   Total Records: {len(results)}")
            
            # 按房间ID统计
            room_counts = {}
            status_counts = {}
            format_counts = {}
            
            for record in results:
                room_counts[record.room_id] = room_counts.get(record.room_id, 0) + 1
                status_counts[record.status] = status_counts.get(record.status, 0) + 1
                format_counts[record.output_format] = format_counts.get(record.output_format, 0) + 1
            
            print(f"   Rooms: {', '.join(f'{room}({count})' for room, count in room_counts.items())}")
            print(f"   Status: {', '.join(f'{status}({count})' for status, count in status_counts.items())}")
            print(f"   Formats: {', '.join(f'{fmt}({count})' for fmt, count in format_counts.items())}")
            
            # 平均处理时间
            avg_processing_time = sum(r.processing_time for r in results) / len(results)
            print(f"   Average Processing Time: {avg_processing_time:.2f}s")
            
            print("=" * 80)
        
    except Exception as e:
        logger.error(f"❌ Query failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
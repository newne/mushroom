#!/usr/bin/env python3
"""
Inference Results Management Script

This script provides command-line utilities for managing model inference results,
including querying, viewing, and updating application status of stored results.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from loguru import logger
from utils.loguru_setting import loguru_setting
from decision_analysis.inference_persistence import decision_persistence

# 初始化日志设置
loguru_setting(production=False)


def query_inference_results(
    room_id: Optional[str] = None,
    days_back: int = 7,
    model_version: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50
):
    """
    查询推理结果
    
    Args:
        room_id: 房间ID过滤
        days_back: 查询最近几天的结果
        model_version: 模型版本过滤
        status: 状态过滤
        limit: 结果数量限制
    """
    logger.info(f"Querying inference results...")
    logger.info(f"  Room ID: {room_id or 'All'}")
    logger.info(f"  Days back: {days_back}")
    logger.info(f"  Model version: {model_version or 'All'}")
    logger.info(f"  Status: {status or 'All'}")
    logger.info(f"  Limit: {limit}")
    
    try:
        # 计算开始时间
        start_time = datetime.now() - timedelta(days=days_back)
        
        # 查询结果
        results = decision_persistence.storage.get_inference_results(
            room_id=room_id,
            start_time=start_time,
            model_version=model_version,
            status=status,
            limit=limit
        )
        
        if not results:
            print("No inference results found matching the criteria.")
            return
        
        print(f"\nFound {len(results)} inference results:")
        print("=" * 120)
        print(f"{'ID':<36} {'Room':<6} {'Time':<20} {'Type':<25} {'Status':<10} {'Confidence':<10} {'App Status':<12}")
        print("=" * 120)
        
        for result in results:
            print(
                f"{result['id']:<36} "
                f"{result['room_id']:<6} "
                f"{result['inference_time'][:19]:<20} "
                f"{result['analysis_type']:<25} "
                f"{result['inference_status']:<10} "
                f"{result['overall_confidence_score']:<10.2f} "
                f"{result['application_status']:<12}"
            )
        
        print("=" * 120)
        
    except Exception as e:
        logger.error(f"Failed to query inference results: {e}")
        sys.exit(1)


def view_inference_result(inference_id: str):
    """
    查看特定推理结果的详细信息
    
    Args:
        inference_id: 推理结果ID
    """
    logger.info(f"Viewing inference result: {inference_id}")
    
    try:
        # 查询特定结果
        results = decision_persistence.storage.get_inference_results(limit=1000)
        
        # 查找匹配的结果
        target_result = None
        for result in results:
            if result['id'] == inference_id:
                target_result = result
                break
        
        if not target_result:
            print(f"Inference result not found: {inference_id}")
            return
        
        # 获取完整的结果详情（需要直接查询数据库）
        from utils.model_inference_storage import ModelInferenceResult
        from sqlalchemy.orm import sessionmaker
        
        Session = sessionmaker(bind=decision_persistence.storage.engine)
        session = Session()
        
        try:
            full_result = session.query(ModelInferenceResult).filter(
                ModelInferenceResult.id == inference_id
            ).first()
            
            if not full_result:
                print(f"Detailed result not found: {inference_id}")
                return
            
            print(f"\nInference Result Details:")
            print("=" * 80)
            print(f"ID: {full_result.id}")
            print(f"Room ID: {full_result.room_id}")
            print(f"Inference Time: {full_result.inference_time}")
            print(f"Analysis Type: {full_result.analysis_type}")
            print(f"Model Version: {full_result.model_version}")
            print(f"Status: {full_result.inference_status}")
            print(f"Overall Confidence: {full_result.overall_confidence_score:.3f}")
            print(f"Processing Time: {full_result.processing_time_seconds:.2f}s")
            print(f"Growth Day: {full_result.growth_day}")
            print(f"Application Status: {full_result.application_status}")
            
            if full_result.applied_at:
                print(f"Applied At: {full_result.applied_at}")
            if full_result.applied_by:
                print(f"Applied By: {full_result.applied_by}")
            
            print(f"\nWarnings ({len(full_result.warnings)}):")
            for i, warning in enumerate(full_result.warnings, 1):
                print(f"  {i}. {warning}")
            
            print(f"\nErrors ({len(full_result.errors)}):")
            for i, error in enumerate(full_result.errors, 1):
                print(f"  {i}. {error}")
            
            # 显示设备建议摘要
            if full_result.device_recommendations:
                print(f"\nDevice Recommendations:")
                for device_type, recommendations in full_result.device_recommendations.items():
                    if isinstance(recommendations, dict):
                        change_count = sum(
                            1 for param, config in recommendations.items()
                            if isinstance(config, dict) and config.get("action") == "adjust"
                        )
                        print(f"  {device_type}: {change_count} adjustments recommended")
            
            # 显示优先级建议摘要
            if full_result.priority_recommendations:
                print(f"\nPriority Recommendations:")
                for priority, actions in full_result.priority_recommendations.items():
                    if actions:
                        print(f"  {priority}: {len(actions)} actions")
            
            print("=" * 80)
            
        finally:
            session.close()
        
    except Exception as e:
        logger.error(f"Failed to view inference result: {e}")
        sys.exit(1)


def update_application_status(
    inference_id: str,
    status: str,
    operator: str,
    reason: Optional[str] = None
):
    """
    更新推理结果的应用状态
    
    Args:
        inference_id: 推理结果ID
        status: 新状态 (applied, rejected, expired)
        operator: 操作员
        reason: 原因或反馈
    """
    logger.info(f"Updating application status: {inference_id} -> {status}")
    
    try:
        if status == "applied":
            feedback = {"application_notes": reason} if reason else None
            success = decision_persistence.mark_result_as_applied(
                inference_id=inference_id,
                applied_by=operator,
                feedback=feedback
            )
        elif status == "rejected":
            success = decision_persistence.mark_result_as_rejected(
                inference_id=inference_id,
                rejected_by=operator,
                reason=reason
            )
        else:
            # 其他状态直接更新
            success = decision_persistence.storage.update_application_status(
                inference_id=inference_id,
                status=status,
                applied_by=operator,
                feedback={"notes": reason} if reason else None
            )
        
        if success:
            print(f"Successfully updated application status to '{status}'")
        else:
            print(f"Failed to update application status")
            sys.exit(1)
        
    except Exception as e:
        logger.error(f"Failed to update application status: {e}")
        sys.exit(1)


def export_inference_results(
    output_file: str,
    room_id: Optional[str] = None,
    days_back: int = 30,
    format_type: str = "json"
):
    """
    导出推理结果到文件
    
    Args:
        output_file: 输出文件路径
        room_id: 房间ID过滤
        days_back: 查询最近几天的结果
        format_type: 输出格式 (json, csv)
    """
    logger.info(f"Exporting inference results to: {output_file}")
    
    try:
        # 计算开始时间
        start_time = datetime.now() - timedelta(days=days_back)
        
        # 查询结果
        results = decision_persistence.storage.get_inference_results(
            room_id=room_id,
            start_time=start_time,
            limit=10000  # 大量导出
        )
        
        if not results:
            print("No inference results found for export.")
            return
        
        output_path = Path(output_file)
        
        if format_type.lower() == "json":
            # JSON格式导出
            export_data = {
                "export_time": datetime.now().isoformat(),
                "query_parameters": {
                    "room_id": room_id,
                    "days_back": days_back,
                    "start_time": start_time.isoformat()
                },
                "total_results": len(results),
                "results": results
            }
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
        
        elif format_type.lower() == "csv":
            # CSV格式导出
            import csv
            
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                if results:
                    writer = csv.DictWriter(f, fieldnames=results[0].keys())
                    writer.writeheader()
                    writer.writerows(results)
        
        else:
            raise ValueError(f"Unsupported format: {format_type}")
        
        print(f"Successfully exported {len(results)} results to {output_path}")
        
    except Exception as e:
        logger.error(f"Failed to export inference results: {e}")
        sys.exit(1)


def main():
    """主CLI入口点"""
    parser = argparse.ArgumentParser(
        description="Manage model inference results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Query recent results for room 611
  python scripts/analysis/manage_inference_results.py query --room-id 611 --days-back 7
  
  # View detailed information for a specific result
  python scripts/analysis/manage_inference_results.py view --id <inference_id>
  
  # Mark a result as applied
  python scripts/analysis/manage_inference_results.py update --id <inference_id> --status applied --operator "john_doe"
  
  # Export results to JSON
  python scripts/analysis/manage_inference_results.py export --output results.json --days-back 30
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Query command
    query_parser = subparsers.add_parser('query', help='Query inference results')
    query_parser.add_argument('--room-id', type=str, help='Room ID filter')
    query_parser.add_argument('--days-back', type=int, default=7, help='Days back to query (default: 7)')
    query_parser.add_argument('--model-version', type=str, help='Model version filter')
    query_parser.add_argument('--status', type=str, help='Status filter')
    query_parser.add_argument('--limit', type=int, default=50, help='Result limit (default: 50)')
    
    # View command
    view_parser = subparsers.add_parser('view', help='View detailed inference result')
    view_parser.add_argument('--id', type=str, required=True, help='Inference result ID')
    
    # Update command
    update_parser = subparsers.add_parser('update', help='Update application status')
    update_parser.add_argument('--id', type=str, required=True, help='Inference result ID')
    update_parser.add_argument('--status', type=str, required=True, 
                              choices=['applied', 'rejected', 'expired', 'pending'],
                              help='New application status')
    update_parser.add_argument('--operator', type=str, required=True, help='Operator name')
    update_parser.add_argument('--reason', type=str, help='Reason or feedback')
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export inference results')
    export_parser.add_argument('--output', type=str, required=True, help='Output file path')
    export_parser.add_argument('--room-id', type=str, help='Room ID filter')
    export_parser.add_argument('--days-back', type=int, default=30, help='Days back to export (default: 30)')
    export_parser.add_argument('--format', type=str, default='json', choices=['json', 'csv'],
                              help='Output format (default: json)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    try:
        if args.command == 'query':
            query_inference_results(
                room_id=args.room_id,
                days_back=args.days_back,
                model_version=args.model_version,
                status=args.status,
                limit=args.limit
            )
        
        elif args.command == 'view':
            view_inference_result(args.id)
        
        elif args.command == 'update':
            update_application_status(
                inference_id=args.id,
                status=args.status,
                operator=args.operator,
                reason=args.reason
            )
        
        elif args.command == 'export':
            export_inference_results(
                output_file=args.output,
                room_id=args.room_id,
                days_back=args.days_back,
                format_type=args.format
            )
        
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Command failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
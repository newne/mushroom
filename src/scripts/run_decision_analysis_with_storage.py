#!/usr/bin/env python3
"""
执行决策分析并将结果存储到动态结果表

该脚本执行完整的决策分析流程，包括：
1. 运行增强决策分析
2. 将结果存储到动态结果表中
3. 验证存储结果

使用方法:
    # 基本用法：指定房间ID
    python scripts/run_decision_analysis_with_storage.py --room-id 611
    
    # 指定分析时间
    python scripts/run_decision_analysis_with_storage.py --room-id 611 --datetime "2024-01-15 10:00:00"
    
    # 详细输出
    python scripts/run_decision_analysis_with_storage.py --room-id 611 --verbose
    
    # 指定输出格式
    python scripts/run_decision_analysis_with_storage.py --room-id 611 --format both
"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from utils.loguru_setting import loguru_setting
from utils.create_table import (
    store_decision_analysis_results,
    query_decision_analysis_dynamic_results
)
from loguru import logger

# 初始化日志
loguru_setting(production=False)


def parse_datetime(datetime_str: Optional[str]) -> datetime:
    """解析日期时间字符串"""
    if datetime_str is None:
        return datetime.now()
    
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d"
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(datetime_str, fmt)
        except ValueError:
            continue
    
    raise ValueError(f"无效的日期时间格式: {datetime_str}")


def run_enhanced_decision_analysis(room_id: str, analysis_datetime: datetime, 
                                   output_format: str = "both", verbose: bool = False) -> Dict[str, Any]:
    """
    运行增强决策分析
    
    Args:
        room_id: 房间ID
        analysis_datetime: 分析时间
        output_format: 输出格式
        verbose: 详细输出
        
    Returns:
        分析结果字典
    """
    try:
        logger.info(f"开始执行决策分析 - 房间: {room_id}, 时间: {analysis_datetime}")
        
        # 导入决策分析模块
        from analysis.run_enhanced_decision_analysis import execute_enhanced_decision_analysis
        
        # 执行决策分析
        result = execute_enhanced_decision_analysis(
            room_id=room_id,
            analysis_datetime=analysis_datetime,
            output_file=None,  # 让系统自动生成文件名
            verbose=verbose,
            output_format=output_format
        )
        
        if not result.success:
            raise Exception(f"决策分析执行失败: {result.error_message}")
        
        logger.info(f"决策分析执行成功，耗时: {result.processing_time:.2f}秒")
        
        # 读取生成的JSON文件
        if result.output_file and result.output_file.exists():
            with open(result.output_file, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            logger.info(f"成功读取输出文件: {result.output_file}")
            return json_data
        else:
            raise Exception("输出文件未生成或不存在")
            
    except ImportError as e:
        logger.error(f"导入决策分析模块失败: {e}")
        # 如果无法导入，尝试直接调用脚本
        return run_decision_analysis_via_script(room_id, analysis_datetime, output_format, verbose)
    except Exception as e:
        logger.error(f"执行决策分析失败: {e}")
        raise


def run_decision_analysis_via_script(room_id: str, analysis_datetime: datetime, 
                                     output_format: str = "both", verbose: bool = False) -> Dict[str, Any]:
    """
    通过脚本调用方式运行决策分析
    
    Args:
        room_id: 房间ID
        analysis_datetime: 分析时间
        output_format: 输出格式
        verbose: 详细输出
        
    Returns:
        分析结果字典
    """
    import subprocess
    import tempfile
    
    try:
        logger.info("通过脚本调用方式执行决策分析")
        
        # 构建命令
        script_path = Path(__file__).parent / "analysis" / "run_enhanced_decision_analysis.py"
        
        cmd = [
            sys.executable,
            str(script_path),
            "--room-id", room_id,
            "--datetime", analysis_datetime.strftime("%Y-%m-%d %H:%M:%S"),
            "--format", output_format,
            "--no-console"  # 禁用控制台输出
        ]
        
        if verbose:
            cmd.append("--verbose")
        
        # 执行命令
        logger.info(f"执行命令: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
        
        if result.returncode != 0:
            logger.error(f"脚本执行失败: {result.stderr}")
            raise Exception(f"决策分析脚本执行失败: {result.stderr}")
        
        logger.info("脚本执行成功")
        
        # 查找生成的输出文件
        output_dir = Path(__file__).parent.parent / "output"
        timestamp = analysis_datetime.strftime("%Y%m%d_%H%M%S")
        output_pattern = f"enhanced_decision_analysis_{room_id}_{timestamp}.json"
        
        # 查找匹配的文件（可能时间戳略有差异）
        output_files = list(output_dir.glob(f"enhanced_decision_analysis_{room_id}_*.json"))
        
        if not output_files:
            raise Exception(f"未找到输出文件，模式: {output_pattern}")
        
        # 选择最新的文件
        latest_file = max(output_files, key=lambda f: f.stat().st_mtime)
        logger.info(f"找到输出文件: {latest_file}")
        
        # 读取文件内容
        with open(latest_file, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        return json_data
        
    except Exception as e:
        logger.error(f"通过脚本调用执行决策分析失败: {e}")
        raise


def store_results_to_dynamic_table(json_data: Dict[str, Any], room_id: str, 
                                   analysis_datetime: datetime) -> Dict[str, Any]:
    """
    将分析结果存储到动态结果表
    
    Args:
        json_data: 分析结果JSON数据
        room_id: 房间ID
        analysis_datetime: 分析时间
        
    Returns:
        存储结果统计
    """
    try:
        logger.info(f"开始存储分析结果到动态结果表 - 房间: {room_id}")
        
        # 存储到决策分析动态结果表
        storage_result = store_decision_analysis_results(
            json_data=json_data,
            room_id=room_id,
            analysis_time=analysis_datetime
        )
        
        logger.info("存储完成，统计信息:")
        logger.info(f"  批次ID: {storage_result.get('batch_id')}")
        logger.info(f"  动态结果记录数: {storage_result.get('dynamic_results_count', 0)}")
        logger.info(f"  变更记录数: {storage_result.get('change_count', 0)}")
        logger.info(f"  处理耗时: {storage_result.get('processing_time', 0):.2f}秒")
        
        return storage_result
        
    except Exception as e:
        logger.error(f"存储分析结果失败: {e}")
        raise


def verify_storage_results(batch_id: str, room_id: str) -> bool:
    """
    验证存储结果
    
    Args:
        batch_id: 批次ID
        room_id: 房间ID
        
    Returns:
        验证是否成功
    """
    try:
        logger.info(f"验证存储结果 - 批次ID: {batch_id}")
        
        # 查询动态结果
        dynamic_results = query_decision_analysis_dynamic_results(
            room_id=room_id,
            batch_id=batch_id,
            limit=100
        )
        
        if not dynamic_results:
            logger.warning("未找到动态结果记录")
            return False
        
        logger.info(f"验证成功，找到 {len(dynamic_results)} 条动态结果记录")
        
        # 显示前几条记录的详细信息
        logger.info("前5条记录详情:")
        for i, result in enumerate(dynamic_results[:5], 1):
            logger.info(f"  记录 {i}:")
            logger.info(f"    设备: {result.device_type} ({result.device_alias})")
            logger.info(f"    点位: {result.point_alias}")
            logger.info(f"    变更: {result.change} ({result.old} -> {result.new})")
            logger.info(f"    级别: {result.level}")
            logger.info(f"    状态: {result.status}")
        
        # 统计变更情况
        change_count = sum(1 for r in dynamic_results if r.change)
        logger.info(f"变更统计: {change_count}/{len(dynamic_results)} 个参数需要调整")
        
        return True
        
    except Exception as e:
        logger.error(f"验证存储结果失败: {e}")
        return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="执行决策分析并存储到动态结果表")
    
    parser.add_argument("--room-id", type=str, required=True,
                       choices=["607", "608", "611", "612"],
                       help="库房编号")
    parser.add_argument("--datetime", type=str, help="分析时间 (YYYY-MM-DD HH:MM:SS)")
    parser.add_argument("--format", type=str, default="both",
                       choices=["enhanced", "monitoring", "both"],
                       help="输出格式")
    parser.add_argument("--verbose", action="store_true", help="详细输出")
    parser.add_argument("--skip-analysis", action="store_true", 
                       help="跳过分析，直接使用最新的输出文件")
    
    args = parser.parse_args()
    
    try:
        logger.info("=" * 80)
        logger.info("决策分析执行与存储工具")
        logger.info("=" * 80)
        
        # 解析分析时间
        analysis_datetime = parse_datetime(args.datetime)
        logger.info(f"分析时间: {analysis_datetime}")
        
        # 步骤1: 执行决策分析（或跳过）
        if args.skip_analysis:
            logger.info("跳过决策分析，查找最新输出文件...")
            
            # 查找最新的输出文件
            output_dir = Path(__file__).parent.parent / "output"
            output_files = list(output_dir.glob(f"enhanced_decision_analysis_{args.room_id}_*.json"))
            
            if not output_files:
                logger.error(f"未找到房间 {args.room_id} 的输出文件")
                return 1
            
            latest_file = max(output_files, key=lambda f: f.stat().st_mtime)
            logger.info(f"使用最新输出文件: {latest_file}")
            
            with open(latest_file, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
        else:
            logger.info("步骤 1: 执行决策分析...")
            json_data = run_enhanced_decision_analysis(
                room_id=args.room_id,
                analysis_datetime=analysis_datetime,
                output_format=args.format,
                verbose=args.verbose
            )
        
        # 步骤2: 存储结果到动态表
        logger.info("步骤 2: 存储结果到动态结果表...")
        storage_result = store_results_to_dynamic_table(
            json_data=json_data,
            room_id=args.room_id,
            analysis_datetime=analysis_datetime
        )
        
        # 步骤3: 验证存储结果
        logger.info("步骤 3: 验证存储结果...")
        batch_id = storage_result.get('batch_id')
        if batch_id:
            verification_success = verify_storage_results(batch_id, args.room_id)
            if verification_success:
                logger.info("✅ 验证成功")
            else:
                logger.warning("⚠️  验证失败")
        else:
            logger.warning("未获取到批次ID，跳过验证")
        
        logger.info("=" * 80)
        logger.info("决策分析执行与存储完成")
        logger.info("=" * 80)
        
        print(f"\n✅ 决策分析执行与存储完成!")
        print(f"   房间ID: {args.room_id}")
        print(f"   分析时间: {analysis_datetime}")
        print(f"   批次ID: {batch_id}")
        print(f"   动态结果记录数: {storage_result.get('dynamic_results_count', 0)}")
        print(f"   变更记录数: {storage_result.get('change_count', 0)}")
        
        return 0
        
    except Exception as e:
        logger.error(f"执行失败: {e}")
        print(f"❌ 执行失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
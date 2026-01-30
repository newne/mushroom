import sys
import os
import argparse
from datetime import datetime

# 添加项目根目录到路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "../../../src"))
sys.path.append(PROJECT_ROOT)

from loguru import logger
from scripts.automation.task_executor import TaskExecutor

# 导入业务任务
try:
    from environment.tasks import safe_daily_env_stats
    from monitoring.tasks import safe_hourly_setpoint_monitoring
    from vision.tasks import safe_hourly_text_quality_inference, safe_daily_top_quality_clip_inference
    from decision_analysis.tasks import safe_batch_decision_analysis
except ImportError as e:
    logger.error(f"无法导入任务模块: {e}")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Mushroom Solution 自动化任务执行器")
    parser.add_argument("task", nargs="?", help="指定要运行的任务名称 (可选)", default="all")
    parser.add_argument("--list", action="store_true", help="列出所有可用任务")
    parser.add_argument("--report", default=f"task_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md", help="报告输出路径")
    
    args = parser.parse_args()
    
    # 初始化执行器
    executor = TaskExecutor()
    
    # 注册任务
    executor.register(
        "daily_env_stats", 
        safe_daily_env_stats, 
        "每日环境数据统计与分析 (通常在凌晨执行)"
    )
    executor.register(
        "hourly_setpoint_monitoring", 
        safe_hourly_setpoint_monitoring, 
        "每小时设定点变更监控 (基于静态配置表)"
    )
    executor.register(
        "hourly_text_quality_inference", 
        safe_hourly_text_quality_inference, 
        "每小时文本编码与图像质量评估"
    )
    executor.register(
        "daily_top_quality_clip_inference", 
        safe_daily_top_quality_clip_inference, 
        "每日Top-5质量图像编码 (02:10)"
    )
    executor.register(
        "decision_analysis", 
        safe_batch_decision_analysis, 
        "综合决策分析 (生成环境调控建议)"
    )
    
    # 列出任务
    if args.list:
        print("可用任务列表:")
        for name, desc in executor.task_descriptions.items():
            print(f"  - {name}: {desc}")
        return

    # 执行任务
    if args.task == "all":
        logger.info("开始执行所有任务...")
        executor.run_all()
    else:
        if args.task in executor.tasks:
            executor.run(args.task)
        else:
            logger.error(f"未知任务: {args.task}")
            print(f"错误: 未知任务 '{args.task}'。请使用 --list 查看可用任务。")
            sys.exit(1)
            
    # 生成报告
    executor.generate_report(args.report)
    print(f"\n任务执行完成。报告已保存至: {args.report}")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
简单的环境统计计算脚本
从2024年12月19日到当前时间
"""

import sys
from pathlib import Path
from datetime import datetime

# 使用BASE_DIR统一管理路径
from global_const.paths import ensure_src_path
ensure_src_path()

# 导入必要的模块
from environment.processor import create_env_data_processor
from utils.loguru_setting import logger

def main():
    """执行环境统计计算"""
    
    # 设置时间范围
    start_time = datetime(2024, 12, 19)  # 2024年12月19日
    end_time = datetime.now()            # 当前时间
    
    print(f"开始计算环境统计数据...")
    print(f"时间范围: {start_time.strftime('%Y-%m-%d')} 到 {end_time.strftime('%Y-%m-%d')}")
    
    try:
        # 创建环境数据处理器
        processor = create_env_data_processor()
        print("✅ 环境数据处理器创建成功")
        
        # 执行计算
        print("🔄 开始计算环境统计...")
        processor.compute_and_store_daily_stats(
            start_time=start_time,
            end_time=end_time,
            rooms=None  # 自动推断所有房间
        )
        
        print("🎉 环境统计计算完成！")
        
    except Exception as e:
        print(f"❌ 计算失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
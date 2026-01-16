#!/usr/bin/env python3
"""
调度器入口 - 开发环境使用
直接运行调度器系统
"""

import sys
from pathlib import Path

# 添加src目录到路径
src_dir = Path(__file__).parent / 'src'
sys.path.insert(0, str(src_dir))

def main():
    """主函数 - 运行调度器"""
    
    try:
        print("🔧 启动调度器系统...")
        from scheduling.optimized_scheduler import main as scheduler_main
        scheduler_main()
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断操作")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 调度器启动失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
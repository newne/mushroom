"""
[DEPRECATED] 优化版调度器模块（旧入口）
请直接使用 src.scheduling 包
"""
from scheduling.core.scheduler import OptimizedScheduler, run_scheduler as main

# 保持向后兼容的类名
OptimizedScheduler = OptimizedScheduler
main = main

if __name__ == "__main__":
    main()

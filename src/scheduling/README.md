# 调度器模块说明

## 概述

优化版调度器模块 `src/scheduling/optimized_scheduler.py` 提供基于APScheduler和Redis的定时任务管理功能。采用面向对象设计，提供更好的可维护性和扩展性。

## 主要功能

1. **Redis持久化调度器** - 使用Redis存储任务状态，支持重启恢复
2. **面向对象设计** - 使用类封装，便于扩展和测试
3. **定时任务管理** - 支持建表任务和每日环境统计任务
4. **优雅退出** - 响应系统信号，确保任务完成后退出
5. **自动恢复** - 调度器异常时自动重启
6. **健康检查集成** - 与系统健康检查模块集成

## 文件结构

```
src/scheduling/
├── optimized_scheduler.py        # 优化版调度器（主要使用）
├── add_scheduler_job_legacy.py   # 原版调度器（备份）
└── README.md                     # 本说明文档
```

## 调度器类设计

### OptimizedScheduler 类

```python
class OptimizedScheduler:
    """优化版调度器类"""
    
    def __init__(self):
        # 初始化配置参数
        
    def run(self) -> NoReturn:
        # 主运行方法
        
    def _init_scheduler(self) -> BackgroundScheduler:
        # 初始化APScheduler
        
    def _setup_jobs(self) -> None:
        # 设置所有任务
        
    def _run_main_loop(self) -> None:
        # 运行主循环
```

## 任务配置

### 当前任务
- **建表任务** (`create_tables`) - 启动时执行一次，确保数据库表存在
- **每日环境统计** (`daily_env_stats`) - 每天01:03:20执行，计算前一日环境数据统计
- **每小时设定点监控** (`hourly_setpoint_monitoring`) - 每小时第5分钟执行，监控所有库房关键参数变化

### 添加新任务
在 `_add_business_jobs()` 方法中添加新的定时任务：

```python
def _add_business_jobs(self) -> None:
    # 现有任务...
    
    # 添加新任务
    self.scheduler.add_job(
        func=self._your_new_task,
        trigger=CronTrigger(hour=2, minute=0, timezone=self.timezone),
        id="your_task_id",
    )
    logger.info("[SCHEDULER] 新任务已添加")
```

## 配置参数

调度器类的配置参数：
- `timezone` - 调度器时区（自动检测本地时区）
- `misfire_grace_time` - 任务错过执行的宽限时间（300秒）
- `max_job_instances` - 单个任务最大并发实例数（1）
- `create_tables_delay` - 建表任务等待时间（5秒）
- `main_loop_interval` - 主循环检查间隔（5秒）
- `max_failures` - 最大连续失败次数（3次）

## 使用方式

### 直接运行
```bash
python src/main.py
```

### 作为模块导入
```python
from scheduling.optimized_scheduler import OptimizedScheduler

scheduler = OptimizedScheduler()
scheduler.run()
```

### 作为函数调用
```python
from scheduling.optimized_scheduler import main
main()
```

## 优势特性

### 1. 面向对象设计
- **封装性** - 所有配置和状态都封装在类中
- **可扩展性** - 易于继承和扩展功能
- **可测试性** - 便于单元测试和模拟

### 2. 改进的错误处理
- **连续失败保护** - 限制最大连续失败次数
- **自动恢复** - 调度器异常时自动重启
- **详细日志** - 完整的错误信息和执行状态

### 3. 资源管理
- **优雅关闭** - 响应系统信号，等待任务完成
- **状态跟踪** - 实时监控调度器和任务状态
- **内存管理** - 避免资源泄漏

## 日志格式

调度器使用统一的日志标签：
- `[SCHEDULER]` - 调度器相关日志
- `[TASK]` - 任务执行日志

## 错误处理

- **连续失败保护** - 最多允许3次连续失败，超过后退出
- **异常捕获** - 所有任务函数都有异常捕获和日志记录
- **优雅关闭** - 响应SIGINT/SIGTERM信号，等待任务完成后退出

## 依赖要求

- APScheduler >= 3.11.2
- Redis服务器
- 项目配置文件（settings.redis.*）

## 健康检查

调度器自动注册到健康检查系统，可通过以下接口监控状态：
- `/health/` - 详细健康状态
- `/health/status` - 简化健康状态

## 故障排查

1. **调度器启动失败** - 检查Redis连接配置
2. **任务执行失败** - 查看任务函数日志
3. **调度器意外停止** - 检查系统资源和网络连接
4. **任务重复执行** - 检查时区配置和任务ID唯一性

## 迁移指南

### 从原版调度器迁移

如果你之前使用 `add_scheduler_job_legacy.py`，迁移到优化版很简单：

1. **更新导入**：
   ```python
   # 旧版
   from scheduling.add_scheduler_job import main
   
   # 新版
   from scheduling.optimized_scheduler import main
   ```

2. **功能保持一致** - 所有原有功能都保持不变
3. **配置兼容** - 使用相同的配置文件和环境变量

## 注意事项

- 确保Redis服务正常运行
- 任务函数应该是幂等的（可重复执行）
- 长时间运行的任务可能影响调度器性能
- 生产环境建议使用进程管理工具（如systemd）
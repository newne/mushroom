# 调度系统

本目录包含当前生效的调度器包实现。旧的单文件入口已经移除，统一使用包入口和核心模块。

## 目录说明

- `__main__.py`: 命令行入口，支持 `python -m scheduling`
- `core/`: `OptimizedScheduler` 与 `run_scheduler()` 的核心实现
- `tasks/`: 任务注册层，装配环境统计、监控、视觉、批产量、决策分析任务
- `config/` 与 `utils/`: 调度配置、时区和公共辅助逻辑

## 启动方式

```bash
# 启动完整服务
PYTHONPATH=/mnt/d/code/mushroom/src uv run python src/main.py

# 单独启动调度器
PYTHONPATH=/mnt/d/code/mushroom/src uv run python -m scheduling
```

## 任务概览

- 启动建表：启动阶段执行，确保基础表结构可用
- 环境统计：按日汇总温度、湿度、CO2 等环境数据
- 设定点监控：按小时巡检设备设定值与开关变化
- 视觉处理：处理最近图像或批量图像编码任务
- 决策分析：按固定时段执行多图像分析和调控建议生成

## 当前实现特性

- 使用 APScheduler 内存存储管理任务
- 使用 PostgreSQL advisory lock 做跨进程互斥
- 统一由 `scheduling.tasks` 注册业务任务
- 支持信号处理、异常监听和主循环自恢复

## 公共 API

```python
from scheduling import OptimizedScheduler, run_scheduler
```

## 维护建议

- 优先通过 `scheduling` 包导入，而不是依赖文件路径
- 新增任务时在 `scheduling/tasks/` 中注册，不再回填单文件调度器
- 文档和测试引用应与包入口保持一致
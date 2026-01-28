# 自动化任务执行器

该工具提供了一个统一的入口，用于执行、测试和监控调度任务中定义的业务逻辑。它具备详细的日志捕获、错误诊断、资源监控和重试机制，适合用于手动触发任务、调试或集成到 CI/CD 流程中。

## 功能特性

- **统一入口**: 通过命令行执行任何已注册的调度任务。
- **重试机制**: 自动重试失败的任务（默认 3 次），提高健壮性。
- **资源监控**: 记录任务执行期间的 CPU 和内存使用变化。
- **日志捕获**: 独立捕获每个任务的执行日志，便于排查问题。
- **诊断报告**: 任务执行结束后自动生成 Markdown 格式的详细报告。

## 使用方法

### 1. 运行环境准备
确保已激活项目虚拟环境，并安装了所有依赖。

```bash
# 激活虚拟环境 (Windows)
.venv\Scripts\activate

# 安装依赖 (如果尚未安装)
pip install loguru psutil tenacity
```

### 2. 查看可用任务列表
```bash
python src/scripts/automation/run_tasks.py --list
```
输出示例：
```text
可用任务列表:
  - daily_env_stats: 每日环境数据统计与分析
  - hourly_setpoint_monitoring: 每小时设定点变更监控
  - hourly_clip_inference: 每小时图像采集与 CLIP 推理分析
  - decision_analysis: 综合决策分析
```

### 3. 执行特定任务
```bash
python src/scripts/automation/run_tasks.py daily_env_stats
```

### 4. 执行所有任务
```bash
python src/scripts/automation/run_tasks.py all
```
或直接运行：
```bash
python src/scripts/automation/run_tasks.py
```

### 5. 自定义报告路径
```bash
python src/scripts/automation/run_tasks.py daily_env_stats --report my_report.md
```

## 报告说明

生成的报告包含以下部分：
1. **执行概览**: 表格形式展示每个任务的状态、耗时、内存变化和错误数。
2. **详细诊断**: 每个任务的详细信息，包括：
   - 任务描述
   - 开始/结束时间
   - **错误详情**: 完整的错误堆栈信息。
   - **执行日志**: 任务执行期间捕获的日志片段（默认最后 20 行）。

## 故障排查

如果任务执行失败，请检查生成的报告文件。
- **状态为 FAILURE**: 任务抛出了未捕获的异常，或重试次数耗尽。请查看“错误详情”。
- **状态为 WARNING**: 任务执行完成，但日志中出现了 ERROR 级别的记录。请查看“执行日志”定位问题。
- **数据库连接错误**: 常见原因，请检查数据库配置及网络连接。

## 扩展指南

要添加新任务，请修改 `src/scripts/automation/run_tasks.py`:

1. 导入新的任务函数。
2. 在 `main` 函数中使用 `executor.register` 注册任务。

```python
from my_module import my_new_task

# ...
executor.register("my_task", my_new_task, "这是我的新任务")
```

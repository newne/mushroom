# Scripts Directory

该目录包含项目的所有实用脚本和命令行工具，按功能分类组织。

## 目录结构

### 1. Analysis (`analysis/`)
用于数据分析和可视化的脚本。
- `run_decision_analysis.py`: 运行决策分析的 CLI 工具。
- `run_env_stats.py`: 运行环境统计分析。
- `run_visualization.py`: 生成环境数据可视化报告。

### 2. Processing (`processing/`)
用于数据处理和计算的脚本。
- `compute_historical_env_stats.py`: 计算历史环境统计数据。
- `process_recent_images.py`: 处理最近采集的图像（CLI 工具）。
- `extract_monitoring_point_configs.py`: 提取监控点配置。
- `visualize_latest_batch.py`: 可视化最新批次数据。

### 3. Monitoring (`monitoring/`)
用于监控系统状态和设定点的脚本。
- `batch_setpoint_monitoring.py`: 批量设定点监控。
- `monitor_setpoint_changes.py`: 监控设定点变更。
- `comprehensive_setpoint_analysis.py`: 综合设定点分析。

### 4. Config (`config/`)
用于配置管理的脚本。
- `import_static_config.py`: 导入静态配置。
- `clear_and_reimport_static_config.py`: 清除并重新导入静态配置。

### 5. Data (`data/`)
用于数据查询和存储的脚本。
- `query_decision_analysis_results.py`: 查询决策分析结果。
- `query_iot_results.py`: 查询 IoT 数据结果。
- `store_decision_analysis_result.py`: 存储决策分析结果。
- `store_iot_analysis_results.py`: 存储 IoT 分析结果。

### 6. Maintenance (`maintenance/`)
用于系统维护和检查的脚本。
- `cache_manager.py`: 缓存管理。
- `check_env_stats.py`: 检查环境统计数据。
- `check_embedding_data.py`: 检查向量数据。
- `check_december_data.py`: 数据检查工具。

## 主入口

### `mushroom_cli.py`
这是一个集成的命令行工具，用于执行常见的图像处理任务。

**使用示例:**

```bash
# 列出图像
python src/scripts/mushroom_cli.py list --mushroom-id 611 --date 20240101

# 处理图像
python src/scripts/mushroom_cli.py process --mushroom-id 611 --batch-size 20

# 编码图像
python src/scripts/mushroom_cli.py encode --mushroom-id 611

# 搜索相似图像
python src/scripts/mushroom_cli.py search path/to/image.jpg

# 健康检查
python src/scripts/mushroom_cli.py health
```

## 注意事项

- 所有脚本应在项目根目录下运行，或确保 `PYTHONPATH` 包含 `src` 目录。
- 推荐使用 `python -m src.scripts.xxx` 的方式运行（如果作为模块），或直接 `python src/scripts/xxx.py`。
- 运行脚本前请确保已激活虚拟环境并安装所有依赖。

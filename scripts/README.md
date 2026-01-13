# 脚本目录

本目录包含蘑菇图像处理系统的各种工具脚本和管理脚本。

## 脚本分类

### 数据处理脚本
- `process_recent_images.py` - 处理最近的图像数据
- `compute_historical_env_stats.py` - 计算历史环境统计数据
- `add_llama_description_fields.py` - 添加LLAMA描述字段

### 监控脚本
- `monitor_setpoint_changes.py` - 监控设定点变更
- `batch_setpoint_monitoring.py` - 批量设定点监控
- `setpoint_demo.py` - 设定点监控演示

### 环境统计脚本
- `run_env_stats.py` - 运行环境统计
- `check_env_stats.py` - 检查环境统计数据
- `check_december_data.py` - 检查12月数据
- `check_data_source.py` - 检查数据源

### 管理工具
- `cache_manager.py` - 缓存管理工具
- `mushroom_cli.py` - 蘑菇系统命令行工具
- `run_visualization.py` - 运行可视化

## 使用方法

```bash
# 进入项目根目录
cd /path/to/mushroom_solution

# 运行脚本
python scripts/script_name.py

# 或者使用模块方式运行
python -m scripts.script_name
```

## 开发规范

1. 所有脚本应该有清晰的文档字符串
2. 脚本应该支持命令行参数
3. 错误处理应该完善
4. 日志记录应该规范
5. 脚本应该可以独立运行

## 注意事项

- 运行脚本前请确保已激活虚拟环境
- 某些脚本可能需要特定的配置文件
- 数据处理脚本可能需要较长时间运行
- 监控脚本通常用于定时任务
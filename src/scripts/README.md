# 脚本目录

本目录包含蘑菇种植智能调控系统的生产脚本和工具脚本。

## 目录结构

### analysis/ - 分析脚本
用于数据分析和决策生成的脚本。

**脚本文件：**
- `run_decision_analysis.py` - 运行决策分析
- `run_env_stats.py` - 运行环境统计分析
- `run_visualization.py` - 运行数据可视化

**使用示例：**
```bash
# 运行决策分析
python src/scripts/analysis/run_decision_analysis.py --room-id 611

# 运行环境统计
python src/scripts/analysis/run_env_stats.py --room-id 611 --date 2024-01-15

# 运行可视化
python src/scripts/analysis/run_visualization.py --room-id 611
```

### processing/ - 处理脚本
用于数据处理和图像处理的脚本。

**脚本文件：**
- `process_recent_images.py` - 处理最近的图像
- `process_recent_hour_images.py` - 处理最近一小时的图像
- `compute_historical_env_stats.py` - 计算历史环境统计
- `extract_monitoring_point_configs.py` - 提取监控点配置
- `visualize_latest_batch.py` - 可视化最新批次

**使用示例：**
```bash
# 处理最近的图像
python src/scripts/processing/process_recent_images.py

# 处理最近一小时的图像
python src/scripts/processing/process_recent_hour_images.py

# 计算历史环境统计
python src/scripts/processing/compute_historical_env_stats.py --start-date 2024-01-01 --end-date 2024-01-31
```

### monitoring/ - 监控脚本
用于系统监控和设定点监控的脚本。

**脚本文件：**
- `monitor_setpoint_changes.py` - 监控设定点变化
- `batch_setpoint_monitoring.py` - 批量设定点监控
- `setpoint_demo.py` - 设定点演示

**使用示例：**
```bash
# 监控设定点变化
python scripts/monitoring/monitor_setpoint_changes.py --room-id 611

# 批量监控
python scripts/monitoring/batch_setpoint_monitoring.py
```

### maintenance/ - 维护脚本
用于系统维护和数据检查的脚本。

**脚本文件：**
- `cache_manager.py` - 缓存管理
- `check_data_source.py` - 检查数据源
- `check_december_data.py` - 检查12月数据
- `check_embedding_data.py` - 检查嵌入数据
- `check_env_stats.py` - 检查环境统计

**使用示例：**
```bash
# 清理缓存
python scripts/maintenance/cache_manager.py --clear

# 检查数据源
python scripts/maintenance/check_data_source.py

# 检查嵌入数据
python scripts/maintenance/check_embedding_data.py --room-id 611
```

### migration/ - 迁移脚本
用于数据库迁移和数据结构更新的脚本。

**脚本文件：**
- `migrate_mushroom_embedding_table.py` - 迁移蘑菇嵌入表
- `add_image_quality_index.py` - 添加图像质量索引
- `add_llama_description_fields.py` - 添加 LLaMA 描述字段

**使用示例：**
```bash
# 迁移嵌入表
python scripts/migration/migrate_mushroom_embedding_table.py

# 添加图像质量索引
python scripts/migration/add_image_quality_index.py
```

### 工具脚本

**mushroom_cli.py** - 蘑菇系统命令行工具

提供统一的命令行接口来执行各种操作。

```bash
# 查看帮助
python scripts/mushroom_cli.py --help

# 运行决策分析
python scripts/mushroom_cli.py analyze --room-id 611

# 处理图像
python scripts/mushroom_cli.py process-images --recent

# 查看统计
python scripts/mushroom_cli.py stats --room-id 611
```

## 脚本使用规范

### 命令行参数
所有脚本应该支持以下标准参数：
- `--help` - 显示帮助信息
- `--verbose` - 显示详细输出
- `--dry-run` - 模拟运行，不实际执行

### 日志记录
脚本应该使用 loguru 记录日志：
```python
from loguru import logger

logger.info("Processing started")
logger.warning("Warning message")
logger.error("Error message")
```

### 错误处理
脚本应该优雅地处理错误：
```python
try:
    # 执行操作
    result = process_data()
except Exception as e:
    logger.error(f"Failed to process data: {e}")
    sys.exit(1)
```

### 配置管理
使用 dynaconf 管理配置：
```python
from global_const.global_const import settings

db_host = settings.postgresql.host
```

## 开发新脚本

### 脚本模板
```python
#!/usr/bin/env python3
"""
脚本名称

脚本描述和用途。

Usage:
    python scripts/category/script_name.py [options]

Examples:
    python scripts/category/script_name.py --room-id 611
"""

import argparse
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="脚本描述",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--room-id",
        type=str,
        required=True,
        help="Room ID"
    )
    
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose output"
    )
    
    return parser.parse_args()


def main():
    """主函数"""
    args = parse_args()
    
    # 配置日志
    if args.verbose:
        logger.add(sys.stdout, level="DEBUG")
    
    try:
        logger.info("Script started")
        
        # 执行主要逻辑
        result = process(args.room_id)
        
        logger.info(f"Script completed: {result}")
        return 0
        
    except Exception as e:
        logger.error(f"Script failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

### 脚本分类指南

**分析脚本 (analysis/)**
- 生成报告和分析结果
- 运行决策算法
- 数据可视化

**处理脚本 (processing/)**
- 数据转换和清洗
- 图像处理
- 批量数据处理

**监控脚本 (monitoring/)**
- 实时监控
- 告警和通知
- 健康检查

**维护脚本 (maintenance/)**
- 数据清理
- 缓存管理
- 系统检查

**迁移脚本 (migration/)**
- 数据库迁移
- 数据结构更新
- 版本升级

## 注意事项

1. **不要在脚本中硬编码敏感信息**
2. **使用配置文件管理参数**
3. **添加适当的错误处理和日志**
4. **编写清晰的帮助文档**
5. **测试脚本在不同环境下的运行**
6. **使用 --dry-run 模式进行测试**
7. **定期清理不再使用的脚本**

## 定时任务

某些脚本可以配置为定时任务：

```bash
# 编辑 crontab
crontab -e

# 每小时处理图像
0 * * * * /path/to/venv/bin/python /path/to/scripts/processing/process_recent_hour_images.py

# 每天凌晨计算统计
0 0 * * * /path/to/venv/bin/python /path/to/scripts/processing/compute_historical_env_stats.py
```

## 获取帮助

如果遇到问题：
1. 查看脚本的 `--help` 输出
2. 查看日志文件
3. 查看相关文档
4. 联系开发团队

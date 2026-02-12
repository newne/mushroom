# 离线图像文本分析模块

本模块用于离线处理蘑菇图像数据，自动生成图文描述并记录到 MLflow。

## 功能特性

1. **自动数据获取**：从数据库中随机抽取每日每库房的图像样本。
2. **MinIO 集成**：自动从 MinIO 下载图像，并支持本地缓存。
3. **LLaMA-VL 分析**：调用 LLaMA-VL 模型生成图像的中文/英文描述及质量评分。
4. **MLflow 记录**：将原始图片、生成的描述文本及元数据记录到 MLflow 实验中。
5. **配置管理**：支持通过 `settings.toml` 进行多环境配置。

## 目录结构

```
src/vision/offline/
├── __init__.py
├── config.py          # 配置加载
├── data_loader.py     # 数据查询与下载
├── mlflow_logger.py   # MLflow 日志封装
└── processor.py       # 核心处理流程
```

## 配置说明

在 `src/configs/settings.toml` 中添加或确认以下配置：

```toml
[development.mlflow]
host = "10.77.77.33"
port = "5000"
experiment_name = "offline_mushroom_analysis"

[development.llama_vl]
# ... LLaMA相关配置 ...
```

## 运行方式

### 1. 命令行运行

编写一个脚本调用 `OfflineProcessor`：

```python
# run_offline_analysis.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from vision.offline.processor import OfflineProcessor

if __name__ == "__main__":
    processor = OfflineProcessor()
    # 每天每个库房处理 2 张图片
    processor.process_daily_batch(limit_per_room_day=2)
```

### 2. 定时任务集成

可以将上述逻辑集成到 APScheduler 或其他定时任务系统中。

## 依赖

确保安装了以下依赖（已包含在 pyproject.toml 中）：
- mlflow
- pillow
- sqlalchemy
- requests

### Torch 安装说明

默认安装 CPU 版本（无需额外参数）：
```bash
uv sync
```

需要 CUDA 版本时：
```bash
uv sync --group torch-cu129
```

如果遇到 libcudart.so 缺失错误，说明安装了 CUDA 版 torch，可执行：
```bash
uv sync --group torch-cpu --reinstall
```

## 测试

运行单元测试：
```bash
pytest tests/vision_offline/
```

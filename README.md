# 蘑菇图像处理系统

基于 MinIO、PostgreSQL/pgvector、CLIP 与 LLM 的蘑菇图像处理与决策分析系统，当前代码结构已收敛为 `src/` 下的领域模块 + `docs/` 下的业务/技术/算法文档分层。

## 快速开始

```bash
# 安装依赖
uv sync

# 启动统一服务入口（FastAPI + Scheduler + Streamlit）
PYTHONPATH=/mnt/d/code/mushroom/src uv run python src/main.py

# 单独启动调度器
PYTHONPATH=/mnt/d/code/mushroom/src uv run python -m scheduling

# CLI 健康检查
PYTHONPATH=/mnt/d/code/mushroom/src uv run python src/scripts/mushroom_cli.py health
```

## 📖 文档

### 文档入口
- **[文档中心](docs/README.md)** - 文档总索引
- **[业务文档](docs/business/README.md)** - 系统概览、使用指南、功能说明
- **[技术文档](docs/technical/README.md)** - 架构、部署、开发与排障
- **[算法文档](docs/algorithms/README.md)** - 核心算法说明

## ✨ 核心功能

### 🖼️ 多模态图像处理
- **CLIP向量化**: 512维图像向量编码
- **LLaMA描述生成**: 智能生成蘑菇生长情况描述
- **多模态融合**: 图像特征(70%) + 文本特征(30%)
- **环境数据集成**: 结合温度、湿度、CO2等参数

### ⏰ 定时任务调度
- **模块化设计**: 每个任务独立模块，便于维护
- **统一接口**: 清晰的任务接口设计
- **公共组件**: 统一的错误处理、日志记录、数据库操作
- **配置集中**: 所有配置参数集中管理
- **完善监控**: 详细的执行日志和状态监控

#### 定时任务列表
| 任务 | 执行时间 | 功能 |
|------|---------|------|
| 建表任务 | 启动时执行 | 创建和维护数据库表 |
| 每日环境统计 | 每天 01:03:20 | 计算环境数据统计 |
| 设定点监控 | 每小时第5分钟 | 监控设定点变更 |
| CLIP推理 | 每小时第25分钟 | 处理图像数据 |
| 决策分析 | 每天 10:00, 12:00, 14:00 | 多图像分析和参数调整 |

### 🚄 性能优化
- **缓存机制**: 图片数据和设备配置缓存
- **共享实例**: 避免重复初始化
- **批量处理**: 高效的大规模图片处理
- **异步处理**: 支持后台处理任务

### 🎯 智能管理
- **时间序列处理**: 按时间范围查询和处理
- **库房映射**: 智能的库房号映射
- **错误恢复**: 优雅的错误处理和降级
- **监控统计**: 详细的处理统计和分析

## 🏗️ 系统架构（重构版）

### 当前模块结构
```
src/
├── api/                 # FastAPI 路由与应用工厂
├── scheduling/          # 调度器包入口、核心与任务注册
├── tasks/               # 共享任务接口
├── vision/              # 图像编码、质量推理、离线处理
├── decision_analysis/   # 决策分析与评分
├── monitoring/          # 设定点与监控逻辑
├── environment/         # 环境统计相关任务
├── storage/             # 存储模型与仓储抽象
├── scripts/             # CLI 与维护脚本
└── utils/               # 通用工具与基础设施
```

### 重构改进
- ✅ **任务模块分离**: 每个定时任务独立模块
- ✅ **公共组件抽取**: 统一的工具函数和错误处理
- ✅ **接口设计统一**: 清晰的调用接口
- ✅ **配置集中管理**: 参数统一配置
- ✅ **依赖关系清晰**: 避免循环引用
- ✅ **保持兼容性**: 功能行为与原版本一致

## 🛠️ 主要命令

### 处理最近图片
```bash
PYTHONPATH=/mnt/d/code/mushroom/src uv run python src/scripts/mushroom_cli.py process --mushroom-id 611
PYTHONPATH=/mnt/d/code/mushroom/src uv run python src/vision/clip_inference_scheduler.py recent --hours 1
```

### 批量处理
```bash
PYTHONPATH=/mnt/d/code/mushroom/src uv run python src/scripts/mushroom_cli.py process --mushroom-id 611 --date 20260119
PYTHONPATH=/mnt/d/code/mushroom/src uv run python src/vision/clip_inference_scheduler.py batch-all --date-filter 20260119
```

### 系统验证
```bash
PYTHONPATH=/mnt/d/code/mushroom/src uv run python src/scripts/mushroom_cli.py health
PYTHONPATH=/mnt/d/code/mushroom/src uv run pytest tests/unit
```

## 📊 系统架构

```
蘑菇图像处理系统
├── 存储层
│   ├── MinIO对象存储 (图片文件)
│   └── PostgreSQL数据库 (向量和元数据)
├── 处理层
│   ├── CLIP模型 (图像向量化)
│   ├── LLaMA模型 (图像描述生成)
│   └── 环境数据处理器 (设备参数集成)
├── 应用层
│   ├── 图像编码器 (多模态处理)
│   ├── 最近图片处理器 (实时处理)
│   └── 批量处理器 (大规模处理)
└── 接口层
    ├── 命令行工具 (main.py)
    ├── 处理脚本 (scripts/)
    └── API接口 (可扩展)
```

## 🔧 环境要求

- Python 3.12+
- CUDA支持的GPU (推荐)
- PostgreSQL with pgvector
- MinIO对象存储

## 项目结构

以 [docs/technical/04_项目结构说明.md](docs/technical/04_项目结构说明.md) 为准；根目录主要保留 `src/`、`docs/`、`tests/`、`docker/`、`examples/`、`models/` 等主干目录，运行产物目录 `logs/`、`output/`、`.archive/` 不纳入版本控制。

## 🎉 最新特性

### v1.2.0 (2026-01-05)
- ✅ 完成LLaMA图像描述功能
- ✅ 数据库字段扩展 (llama_description, full_text_description)
- ✅ 系统性能优化 (缓存机制、共享实例)
- ✅ 主程序功能扩展 (支持批量处理所有图片)
- ✅ 图像缩放优化 (960x540分辨率减少LLaMA运算量)

### 技术亮点
- **多模态处理**: 图像 + 文本的联合编码
- **智能缓存**: 5分钟缓存减少重复查询
- **优雅降级**: LLaMA失败时自动使用身份元数据
- **批量优化**: 支持大规模历史数据处理

## 🤝 贡献

欢迎提交Issue和Pull Request来改进系统功能。

## 📄 许可证

本项目遵循项目许可证条款。

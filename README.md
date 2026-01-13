# 蘑菇图像处理系统

基于MinIO存储和CLIP模型的蘑菇图像向量化处理系统，集成LLaMA模型生成图像描述，支持多模态编码和智能搜索。

## 🚀 快速开始

```bash
# 安装依赖
uv sync

# 系统验证
python main.py validate --max-per-room 3

# 处理最近图片
python main.py recent --hours 1

# 批量处理所有图片
python main.py batch-all
```

## 📖 文档

- **[系统总览](docs/system_overview.md)** - 系统架构和核心功能介绍
- **[使用指南](docs/usage_guide.md)** - 详细的使用方法和API文档
- **[系统优化总结](docs/SYSTEM_OPTIMIZATION_SUMMARY.md)** - 最新的优化成果和技术特性
- **[蘑菇系统说明](docs/README_MUSHROOM_SYSTEM.md)** - 完整的系统功能说明
- **[CLIP推理工作流](docs/CLIP_INFERENCE_AND_GLOBAL_WORKFLOW.md)** - CLIP模型推理流程
- **[MinIO配置指南](docs/minio_setup_guide.md)** - MinIO存储配置
- **[蘑菇处理指南](docs/mushroom_processing_guide.md)** - 蘑菇图像处理详细指南

## ✨ 核心功能

### 🖼️ 多模态图像处理
- **CLIP向量化**: 512维图像向量编码
- **LLaMA描述生成**: 智能生成蘑菇生长情况描述
- **多模态融合**: 图像特征(70%) + 文本特征(30%)
- **环境数据集成**: 结合温度、湿度、CO2等参数

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

## 🛠️ 主要命令

### 处理最近图片
```bash
python main.py recent --hours 1                    # 处理最近1小时
python main.py recent --hours 2 --room-id 7        # 指定库房
python main.py recent --hours 1 --max-per-room 5   # 限制数量
```

### 批量处理
```bash
python main.py batch-all                           # 处理所有图片
python main.py batch-all --room-id 7               # 指定库房
python main.py batch-all --date-filter 20251231    # 指定日期
python main.py batch-all --batch-size 20           # 设置批次大小
```

### 系统验证
```bash
python main.py validate --max-per-room 3           # 系统功能验证
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

## 📁 项目结构

```
mushroom_solution/
├── docs/                    # 文档目录
├── src/                     # 源代码
│   ├── clip/               # CLIP相关模块
│   ├── utils/              # 工具模块
│   ├── configs/            # 配置文件
│   └── global_const/       # 全局常量
├── scripts/                # 脚本工具
├── examples/               # 示例代码
├── models/                 # 模型文件
└── main.py                 # 主程序入口
```

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

---

🍄 **蘑菇图像处理系统** - 让图像数据更智能！
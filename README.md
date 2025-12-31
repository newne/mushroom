# 蘑菇多模态CLIP编码系统

## 项目概述

这是一个基于CLIP模型的蘑菇图像多模态编码系统，将蘑菇图像与环境参数相结合，生成512维的联合向量表示。系统能够从MinIO中获取蘑菇图像，解析时间信息，并获取对应的环境参数，最终生成可用于检索和分析的多模态向量。

## 主要功能

- 蘑菇图像的CLIP向量编码
- 环境参数的语义化描述
- 图像与环境数据的多模态融合编码
- MinIO图像存储集成
- 数据库存储与检索

## 技术栈

- Python 3.13
- PyTorch & Transformers (CLIP模型)
- Pandas & NumPy
- MinIO客户端
- PostgreSQL (通过SQLAlchemy)
- Redis缓存
- Loguru日志

## 项目结构

```
src/
├── clip/                 # CLIP模型相关
├── configs/             # 配置文件
├── global_const/        # 全局常量
└── utils/               # 工具函数
    ├── create_table.py  # 数据库表创建
    ├── data_preprocessing.py  # 数据预处理
    ├── env_data_processor.py  # 环境数据处理器
    ├── get_data.py      # 数据获取
    ├── minio_client.py  # MinIO客户端
    └── mushroom_image_encoder.py  # 蘑菇图像编码器
```

## 安装与运行

1. 安装依赖：
   ```bash
   pip install -r requirements.txt
   # 或使用uv
   uv sync
   ```

2. 配置环境变量：
   - 设置MinIO、PostgreSQL、Redis连接信息
   - 配置模型路径

3. 初始化数据库：
   ```bash
   python src/utils/create_table.py
   ```

4. 运行系统：
   ```bash
   python -m src.utils.mushroom_image_encoder
   ```

## 上传到GitHub的说明

此仓库已配置适当的`.gitignore`文件，确保不会上传以下类型的文件：

- 模型文件（.pt, .pth, .h5, .bin等）
- 虚拟环境文件夹（.venv/, venv/, env/）
- 大型数据文件
- 二进制文件
- 日志文件
- 配置敏感信息

## 注意事项

- 模型必须预先下载到本地路径，代码仅实现从本地加载模型
- 项目遵循将静态配置文件按设备类型分组存储到Redis的规范
- 系统使用多模态编码融合图像和环境数据

## 许可证

请根据项目需求添加适当的许可证信息。
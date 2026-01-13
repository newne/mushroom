# 蘑菇图像向量化处理系统

基于MinIO存储和CLIP模型的蘑菇图像向量化处理系统，支持图像存储、向量化、相似度搜索等功能。

## 系统概述

本系统专门处理蘑菇数据结构的图像文件，实现以下功能：

- 🗂️ **标准化数据结构**: 支持蘑菇库号-时间-图片的标准格式
- 🔍 **智能路径解析**: 自动解析文件路径中的蘑菇库号、采集时间等信息
- 🖼️ **图像向量化**: 使用CLIP模型进行512维向量化
- 🔎 **相似度搜索**: 基于向量的图像相似度搜索
- 📊 **统计分析**: 提供详细的处理统计和分析
- 🌐 **多环境支持**: 支持开发和生产环境配置

## 数据结构规范

### 文件路径格式
```
mogu/{蘑菇库号}/{日期}/{蘑菇库号}_{采集IP}_{采集日期}_{详细时间}.jpg
```

### 示例
```
mogu/612/20251224/612_1921681235_20251218_20251224160000.jpg
```

### 字段说明
- **蘑菇库号**: 612 (标识不同的蘑菇库)
- **采集IP**: 1921681235 (采集设备IP，格式化数字)
- **采集日期**: 20251218 (YYYYMMDD格式)
- **详细时间**: 20251224160000 (YYYYMMDDHHMMSS格式)

## 快速开始

### 1. 环境准备

```bash
# 安装依赖
uv sync

# 设置环境变量
export prod=false  # 开发环境
```

### 2. 系统测试

```bash
# 运行完整系统测试
python test_mushroom_system.py
```

### 3. 基础使用

```python
from src.utils.mushroom_image_processor import create_mushroom_processor

# 创建处理器
processor = create_mushroom_processor()

# 获取蘑菇图像列表
images = processor.get_mushroom_images()
print(f"发现 {len(images)} 个图像文件")

# 处理图像向量化
for image_info in images[:5]:
    success = processor.process_single_image(image_info)
    print(f"处理 {image_info.file_name}: {'成功' if success else '失败'}")
```

## 主要功能

### 📁 路径解析和验证

```python
from src.utils.mushroom_image_processor import MushroomImagePathParser

parser = MushroomImagePathParser()

# 解析路径
path = "mogu/612/20251224/612_1921681235_20251218_20251224160000.jpg"
image_info = parser.parse_path(path)

print(f"蘑菇库号: {image_info.mushroom_id}")
print(f"采集时间: {image_info.collection_datetime}")
```

### 🖼️ 图像处理和向量化

```python
# 单个图像处理
success = processor.process_single_image(image_info, description="蘑菇生长记录")

# 批量处理
results = processor.batch_process_images(mushroom_id="612", batch_size=10)
print(f"处理结果: 成功{results['success']}, 失败{results['failed']}")
```

### 🔍 图像搜索和过滤

```python
# 按条件过滤
mushroom_612_images = processor.get_mushroom_images(mushroom_id="612")
today_images = processor.get_mushroom_images(date_filter="20251224")

# 相似度搜索
similar_images = processor.search_similar_images(query_path, top_k=5)
```

### 📊 统计分析

```python
# 处理统计
stats = processor.get_processing_statistics()
print(f"已处理: {stats['total_processed']} 张图片")

# 存储统计
from src.utils.minio_service import create_minio_service
service = create_minio_service()
minio_stats = service.get_image_statistics()
print(f"存储总量: {minio_stats['total_size_mb']} MB")
```

## 命令行工具

### 基本命令

```bash
# 查看帮助
python scripts/mushroom_cli.py --help

# 列出图像文件
python scripts/mushroom_cli.py list

# 按条件过滤
python scripts/mushroom_cli.py list -m 612 -d 20251224

# 处理图像
python scripts/mushroom_cli.py process -m 612

# 查看统计
python scripts/mushroom_cli.py stats

# 健康检查
python scripts/mushroom_cli.py health
```

### 高级功能

```bash
# 处理单个文件
python scripts/mushroom_cli.py process -f "mogu/612/20251224/612_1921681235_20251218_20251224160000.jpg"

# 搜索相似图像
python scripts/mushroom_cli.py search "mogu/612/20251224/612_1921681235_20251218_20251224160000.jpg" -k 5

# 验证路径格式
python scripts/mushroom_cli.py validate -p "mogu/612/20251224/612_1921681235_20251218_20251224160000.jpg"

# 详细输出
python scripts/mushroom_cli.py list -v
```

## 系统架构

```
蘑菇图像处理系统
├── MinIO存储服务
│   ├── 开发环境: http://10.77.77.39:9000
│   └── 生产环境: http://172.17.0.1:9000
├── PostgreSQL数据库
│   ├── 图像向量存储
│   └── 元数据管理
├── CLIP向量化模型
│   ├── 本地模型: models/clip-vit-base-patch32
│   └── 512维向量输出
└── 处理组件
    ├── 路径解析器
    ├── 图像处理器
    ├── 向量化引擎
    └── 相似度搜索
```

## 配置管理

### 环境配置

配置文件：`src/configs/settings.toml`

```toml
[development.minio]
endpoint = "http://10.77.77.39:9000"
bucket = "mogu"

[production.minio]  
endpoint = "http://172.17.0.1:9000"
bucket = "mogu"
```

### 环境切换

```bash
# 开发环境（默认）
export prod=false

# 生产环境
export prod=true
```

## 文件结构

```
项目根目录/
├── src/
│   ├── clip/
│   │   └── clip_app.py              # CLIP向量化应用
│   ├── utils/
│   │   ├── minio_client.py          # MinIO客户端
│   │   ├── minio_service.py         # MinIO服务
│   │   ├── mushroom_image_processor.py  # 蘑菇图像处理器
│   │   └── create_table.py          # 数据库表定义
│   └── configs/
│       └── settings.toml            # 配置文件
├── examples/
│   ├── mushroom_processing_example.py   # 处理示例
│   ├── minio_example.py             # MinIO示例
│   └── integrated_minio_example.py  # 集成示例
├── scripts/
│   └── mushroom_cli.py              # 命令行工具
├── docs/
│   ├── mushroom_processing_guide.md # 详细使用指南
│   └── minio_setup_guide.md         # MinIO配置指南
└── test_mushroom_system.py          # 系统测试脚本
```

## 数据库结构

### mushroom_embeddings 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| image_path | TEXT | MinIO中的图像路径 |
| file_name | VARCHAR(255) | 文件名 |
| description | TEXT | 图像描述 |
| embedding | JSON | 512维向量 |
| growth_day | INTEGER | 生长天数（可选） |
| created_at | TIMESTAMP | 创建时间 |

## 示例代码

### 完整处理流程

```python
from src.utils.mushroom_image_processor import create_mushroom_processor

def process_mushroom_images():
    # 创建处理器
    processor = create_mushroom_processor()
    
    # 1. 获取特定蘑菇库的图像
    images = processor.get_mushroom_images(mushroom_id="612")
    print(f"找到蘑菇库612的图像: {len(images)} 张")
    
    # 2. 批量处理向量化
    results = processor.batch_process_images(
        mushroom_id="612",
        batch_size=5
    )
    
    # 3. 查看处理结果
    print(f"处理完成: {results['success']}/{results['total']} 成功")
    
    # 4. 获取统计信息
    stats = processor.get_processing_statistics()
    print(f"数据库中共有: {stats['total_processed']} 张处理过的图像")
    
    # 5. 相似度搜索示例
    if images:
        similar = processor.search_similar_images(images[0].file_path, top_k=3)
        print(f"找到 {len(similar)} 张相似图像")

if __name__ == "__main__":
    process_mushroom_images()
```

### 路径解析示例

```python
from src.utils.mushroom_image_processor import MushroomImagePathParser

def parse_mushroom_paths():
    parser = MushroomImagePathParser()
    
    # 测试路径
    paths = [
        "mogu/612/20251224/612_1921681235_20251218_20251224160000.jpg",
        "mogu/613/20251225/613_1921681236_20251219_20251225090000.jpg"
    ]
    
    for path in paths:
        image_info = parser.parse_path(path)
        if image_info:
            print(f"路径: {path}")
            print(f"  蘑菇库号: {image_info.mushroom_id}")
            print(f"  采集时间: {image_info.collection_datetime}")
            print(f"  采集IP: {image_info.collection_ip}")
            print()

if __name__ == "__main__":
    parse_mushroom_paths()
```

## 性能优化

### 批量处理建议

- 批量大小：10-20张图片
- 内存监控：定期清理临时文件
- 网络优化：使用本地MinIO缓存
- 数据库优化：使用批量提交

### 存储优化

- 图像压缩：适当的JPEG质量
- 路径组织：按日期和库号分层
- 索引优化：为常用查询字段建索引

## 故障排除

### 常见问题

1. **路径格式错误**
   ```bash
   python scripts/mushroom_cli.py validate -p "your_path"
   ```

2. **MinIO连接失败**
   ```bash
   python scripts/mushroom_cli.py health
   ```

3. **向量化失败**
   - 检查CLIP模型是否正确加载
   - 验证图像文件完整性
   - 确认临时目录权限

4. **数据库连接问题**
   - 检查PostgreSQL服务状态
   - 验证pgvector扩展安装
   - 确认连接配置正确

### 调试模式

```python
# 启用详细日志
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="DEBUG")

# 运行测试
python test_mushroom_system.py
```

## 扩展功能

### 自定义处理逻辑

```python
def custom_image_processor(image, image_info):
    # 自定义图像处理逻辑
    return {
        'custom_feature': extract_custom_feature(image),
        'metadata': generate_metadata(image_info)
    }

# 使用自定义处理器
from src.utils.minio_service import create_minio_service
service = create_minio_service()
results = service.process_images_with_callback(custom_image_processor)
```

### 批量分析

```python
def analyze_mushroom_growth():
    processor = create_mushroom_processor()
    
    # 按时间序列分析
    for mushroom_id in ["612", "613", "614"]:
        images = processor.get_mushroom_images(mushroom_id=mushroom_id)
        images.sort(key=lambda x: x.collection_datetime)
        
        print(f"蘑菇库{mushroom_id}生长记录:")
        for img in images:
            print(f"  {img.collection_datetime}: {img.file_name}")
```

## 监控和维护

### 定期检查脚本

```bash
#!/bin/bash
# daily_check.sh

echo "=== 每日蘑菇系统检查 ==="
echo "时间: $(date)"

# 健康检查
python scripts/mushroom_cli.py health

# 统计信息
python scripts/mushroom_cli.py stats

# 路径验证
python scripts/mushroom_cli.py validate

echo "=== 检查完成 ==="
```

### 性能监控

```python
def monitor_system_performance():
    from src.utils.minio_service import create_minio_service
    from src.utils.mushroom_image_processor import create_mushroom_processor
    
    # MinIO性能
    service = create_minio_service()
    stats = service.get_image_statistics()
    
    # 处理性能
    processor = create_mushroom_processor()
    db_stats = processor.get_processing_statistics()
    
    print(f"存储使用: {stats['total_size_mb']} MB")
    print(f"处理进度: {db_stats['total_processed']} 张")
    print(f"处理效率: {db_stats['total_processed'] / stats['total_images'] * 100:.1f}%")
```

## 文档和支持

- 📖 [详细使用指南](docs/mushroom_processing_guide.md)
- 🔧 [MinIO配置指南](docs/minio_setup_guide.md)
- 🧪 [系统测试](test_mushroom_system.py)
- 💡 [示例代码](examples/)
- 🛠️ [命令行工具](scripts/mushroom_cli.py)

## 版本信息

- **系统版本**: 1.0.0
- **CLIP模型**: openai/clip-vit-base-patch32
- **向量维度**: 512
- **支持格式**: JPG, JPEG, PNG, BMP, GIF, TIFF
- **Python版本**: >=3.12

## 许可证

本项目遵循项目许可证条款。

---

🍄 **蘑菇图像向量化处理系统** - 让图像数据更智能！
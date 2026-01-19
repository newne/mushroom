# 蘑菇图像处理系统使用指南

## 快速开始

### 1. 环境准备

```bash
# 安装依赖
uv sync

# 设置环境变量
export prod=false  # 开发环境
```

### 2. 系统验证

```bash
# 运行系统验证
python main.py validate --max-per-room 3
```

## 主程序使用

### 命令行接口

系统提供统一的命令行接口 `main.py`，支持三种主要模式：

#### 1. 处理最近图片 (recent)

```bash
# 处理最近1小时的图片
python main.py recent --hours 1

# 处理指定库房最近2小时的图片
python main.py recent --hours 2 --room-id 7

# 限制每个库房最多处理5张图片
python main.py recent --hours 1 --max-per-room 5

# 处理多个指定库房
python main.py recent --hours 1 --room-ids 7 8

# 测试模式（不保存到数据库）
python main.py recent --hours 1 --no-save
```

#### 2. 批量处理所有图片 (batch-all)

```bash
# 处理所有图片
python main.py batch-all

# 处理指定库房的图片
python main.py batch-all --room-id 7

# 处理指定日期的图片
python main.py batch-all --date-filter 20251231

# 设置批处理大小
python main.py batch-all --batch-size 20

# 组合条件处理
python main.py batch-all --room-id 7 --date-filter 20251231 --batch-size 10
```

#### 3. 系统验证 (validate)

```bash
# 系统验证（每个库房处理3张图片）
python main.py validate --max-per-room 3

# 快速验证（每个库房处理1张图片）
python main.py validate --max-per-room 1
```

### 参数说明

| 参数 | 说明 | 适用模式 |
|------|------|----------|
| `--hours` | 查询最近多少小时的图片 | recent |
| `--room-id` | 指定单个库房号 | recent, batch-all |
| `--room-ids` | 指定多个库房号 | recent |
| `--max-per-room` | 每个库房最多处理多少张图片 | recent, validate |
| `--date-filter` | 日期过滤 (YYYYMMDD格式) | batch-all |
| `--batch-size` | 批处理大小 | batch-all |
| `--no-save` | 测试模式，不保存到数据库 | recent, batch-all |

## 脚本工具使用

### 最近图片处理脚本

```bash
# 使用优化版本的处理脚本
python scripts/process_recent_images.py --hours 1 --max-per-room 2

# 仅显示摘要信息
python scripts/process_recent_images.py --hours 1 --summary-only

# 处理指定库房
python scripts/process_recent_images.py --hours 2 --room-id 7
```

### 数据库迁移脚本

```bash
# 添加LLaMA描述字段到数据库
python scripts/add_llama_description_fields.py
```

## Python API使用

### 基础使用

```python
from src.utils.mushroom_image_encoder import create_mushroom_encoder
from src.utils.recent_image_processor import create_recent_image_processor
from src.utils.minio_client import create_minio_client

# 创建共享实例（推荐方式）
shared_encoder = create_mushroom_encoder()
shared_minio_client = create_minio_client()

# 创建处理器
processor = create_recent_image_processor(
    shared_encoder=shared_encoder,
    shared_minio_client=shared_minio_client
)
```

### 处理最近图片

```python
# 整合处理（推荐）
result = processor.get_recent_image_summary_and_process(
    hours=1,
    max_images_per_room=5,
    save_to_db=True,
    show_summary=True
)

print(f"处理结果: {result['processing']['total_success']} 成功")
```

### 批量处理

```python
# 使用编码器进行批量处理
encoder = create_mushroom_encoder()

stats = encoder.batch_process_images(
    mushroom_id="7",  # 可选，指定库房
    date_filter="20251231",  # 可选，指定日期
    batch_size=10
)

print(f"批量处理: {stats['success']}/{stats['total']} 成功")
```

### 系统验证

```python
# 系统功能验证
validation_results = encoder.validate_system_with_limited_samples(
    max_per_mushroom=3
)

print(f"验证结果: {validation_results['total_success']} 成功")
```

## 高级功能

### 自定义处理逻辑

```python
def custom_process_images():
    # 创建编码器
    encoder = create_mushroom_encoder()
    
    # 获取特定条件的图片
    from src.utils.mushroom_image_processor import create_mushroom_processor
    processor = create_mushroom_processor()
    
    images = processor.get_mushroom_images(
        mushroom_id="7",
        date_filter="20251231"
    )
    
    # 自定义处理逻辑
    for image_info in images:
        result = encoder.process_single_image(
            image_info, 
            save_to_db=True
        )
        
        if result and result.get('saved_to_db'):
            print(f"成功处理: {image_info.file_name}")
        else:
            print(f"处理失败: {image_info.file_name}")
```

### 性能监控

```python
def monitor_processing():
    encoder = create_mushroom_encoder()
    
    # 获取处理统计
    stats = encoder.get_processing_statistics()
    
    print(f"数据库总记录: {stats['total_processed']}")
    print(f"有环境控制的记录: {stats['with_environmental_control']}")
    
    # 库房分布
    for room_id, count in stats['room_distribution'].items():
        print(f"库房{room_id}: {count}张")
```

### 缓存管理

```python
# 使用缓存优化的处理器
processor = create_recent_image_processor(
    shared_encoder=shared_encoder,
    shared_minio_client=shared_minio_client
)

# 第一次查询（建立缓存）
summary1 = processor.get_recent_image_summary(hours=1)

# 第二次查询（使用缓存，更快）
summary2 = processor.get_recent_image_summary(hours=1)
```

## 配置和优化

### 环境配置

```bash
# 开发环境
export prod=false

# 生产环境  
export prod=true
```

### 性能调优

```python
# 批处理大小调优
# 小批次：内存占用少，但处理慢
python main.py batch-all --batch-size 5

# 大批次：处理快，但内存占用多
python main.py batch-all --batch-size 50

# 推荐设置：平衡性能和资源
python main.py batch-all --batch-size 20
```

### LLaMA优化

系统自动进行以下优化：
- 图像缩放到960x540分辨率减少运算量
- 600秒超时处理适应CPU部署
- 优雅降级：LLaMA失败时使用身份元数据

## 故障排除

### 常见问题

#### 1. 初始化失败
```bash
# 检查配置文件
cat src/configs/settings.toml

# 检查数据库连接
python -c "from src.global_const.global_const import pgsql_engine; print(pgsql_engine)"
```

#### 2. MinIO连接问题
```bash
# 测试MinIO连接
python -c "from src.utils.minio_client import create_minio_client; client = create_minio_client(); print('连接成功')"
```

#### 3. LLaMA超时
```bash
# 使用测试模式跳过LLaMA
python main.py recent --hours 1 --no-save
```

#### 4. 内存不足
```bash
# 减少批处理大小
python main.py batch-all --batch-size 5
```

### 调试模式

```python
# 启用详细日志
import sys
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="DEBUG")

# 运行处理
python main.py validate --max-per-room 1
```

### 性能分析

```python
import time

def benchmark_processing():
    start_time = time.time()
    
    # 运行处理
    result = processor.get_recent_image_summary_and_process(
        hours=1,
        max_images_per_room=5,
        save_to_db=False  # 测试模式
    )
    
    end_time = time.time()
    processing_time = end_time - start_time
    
    print(f"处理时间: {processing_time:.2f}秒")
    print(f"处理速度: {result['processing']['total_processed'] / processing_time:.2f} 张/秒")
```

## 最佳实践

### 1. 生产环境使用

```bash
# 定期处理最近图片
python main.py recent --hours 1 --max-per-room 10

# 批量处理历史数据
python main.py batch-all --batch-size 20

# 定期系统验证
python main.py validate --max-per-room 1
```

### 2. 开发环境测试

```bash
# 快速测试
python main.py recent --hours 1 --max-per-room 1 --no-save

# 功能验证
python main.py validate --max-per-room 1
```

### 3. 性能优化

- 使用共享实例避免重复初始化
- 合理设置批处理大小
- 利用缓存机制减少重复查询
- 在测试时使用 `--no-save` 参数

### 4. 监控和维护

- 定期检查处理统计
- 监控系统资源使用
- 查看详细日志排查问题
- 定期清理临时文件

---

通过本指南，您可以充分利用蘑菇图像处理系统的各项功能，实现高效的图像处理和分析。
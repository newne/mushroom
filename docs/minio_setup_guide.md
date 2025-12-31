# MinIO存储服务配置指南

本文档介绍如何在项目中配置和使用MinIO存储服务，支持生产环境和开发环境的配置切换。

## 配置概览

### 生产环境配置
- **端点**: `http://172.17.0.1:9000`
- **存储桶**: `mogu`
- **访问密钥**: `admin` / `admin`

### 开发环境配置
- **端点**: `http://10.77.77.39:9000`
- **存储桶**: `mogu`
- **访问密钥**: `admin` / `admin`

## 安装依赖

确保项目中已安装MinIO Python客户端：

```bash
# 使用uv安装
uv add minio

# 或使用pip安装
pip install minio>=7.2.0
```

## 配置文件

配置信息已添加到 `src/configs/settings.toml` 文件中：

```toml
[development.minio]
endpoint = "http://10.77.77.39:9000"
access_key = "admin"
secret_key = "admin"
bucket = "mogu"
region = "us-east-1"
# ... 其他配置

[production.minio]
endpoint = "http://172.17.0.1:9000"
access_key = "admin"
secret_key = "admin"
bucket = "mogu"
region = "us-east-1"
# ... 其他配置
```

## 环境切换

通过环境变量 `prod` 控制环境切换：

```bash
# 使用开发环境（默认）
export prod=false

# 使用生产环境
export prod=true
```

## 使用方法

### 1. 基础使用

```python
from src.utils.minio_client import create_minio_client

# 创建客户端
client = create_minio_client()

# 测试连接
if client.test_connection():
    print("连接成功!")
    
    # 列出图片文件
    images = client.list_images()
    print(f"找到 {len(images)} 张图片")
    
    # 获取图片
    if images:
        image = client.get_image(images[0])
        if image:
            print(f"图片尺寸: {image.size}")
```

### 2. 高级服务使用

```python
from src.utils.minio_service import create_minio_service

# 创建服务
service = create_minio_service()

# 健康检查
health = service.health_check()
print(f"服务状态: {'健康' if health['healthy'] else '异常'}")

# 获取图片统计
stats = service.get_image_statistics()
print(f"总图片数: {stats['total_images']}")
print(f"总大小: {stats['total_size_mb']} MB")

# 获取图片数据集
dataset = service.get_image_dataset()
for img in dataset[:5]:  # 显示前5张图片信息
    print(f"图片: {img['file_name']}, 大小: {img['size']} bytes")
```

### 3. 批量处理图片

```python
def analyze_image(image, image_info):
    """图片分析函数"""
    width, height = image.size
    return {
        'dimensions': f"{width}x{height}",
        'mode': image.mode,
        'file_size': image_info['size']
    }

# 批量处理
results = service.process_images_with_callback(analyze_image, batch_size=5)
print(f"处理完成: {len(results)} 张图片")
```

## 主要功能

### MinIOClient 类
- `test_connection()`: 测试连接
- `list_images()`: 列出图片文件
- `get_image()`: 获取PIL图片对象
- `get_image_bytes()`: 获取图片字节数据
- `upload_image()`: 上传图片
- `delete_image()`: 删除图片
- `get_image_info()`: 获取图片信息

### MinIOService 类
- `get_image_dataset()`: 获取图片数据集
- `get_images_by_category()`: 按类别分组图片
- `batch_download_images()`: 批量下载图片
- `create_image_manifest()`: 创建图片清单
- `get_image_statistics()`: 获取统计信息
- `process_images_with_callback()`: 批量处理图片
- `health_check()`: 健康检查

## 示例代码

### 运行基础示例
```bash
python examples/minio_example.py
```

### 运行集成示例
```bash
python examples/integrated_minio_example.py
```

## 错误处理

所有MinIO操作都包含完整的错误处理和日志记录：

```python
try:
    image = client.get_image("test.jpg")
    if image:
        print("图片获取成功")
    else:
        print("图片获取失败")
except Exception as e:
    print(f"操作异常: {e}")
```

## 最佳实践

1. **连接测试**: 在使用前先调用 `test_connection()` 测试连接
2. **存储桶检查**: 使用 `ensure_bucket_exists()` 确保存储桶存在
3. **批量操作**: 对于大量图片，使用批量处理方法提高效率
4. **资源管理**: 及时关闭图片对象和连接
5. **错误处理**: 始终包含适当的错误处理逻辑
6. **日志记录**: 利用内置的日志功能进行调试和监控

## 配置验证

运行以下命令验证配置：

```python
from src.utils.minio_service import create_minio_service

service = create_minio_service()
health = service.health_check()

if health['healthy']:
    print("✅ MinIO配置正确")
    print(f"环境: {health['environment']}")
    print(f"端点: {health['endpoint']}")
    print(f"存储桶: {health['bucket']}")
    print(f"图片数量: {health['image_count']}")
else:
    print("❌ MinIO配置有问题:")
    for error in health['errors']:
        print(f"  - {error}")
```

## 故障排除

### 常见问题

1. **连接失败**
   - 检查网络连接
   - 验证端点地址和端口
   - 确认MinIO服务运行状态

2. **认证失败**
   - 检查访问密钥和秘密密钥
   - 验证用户权限

3. **存储桶不存在**
   - 使用 `ensure_bucket_exists()` 创建存储桶
   - 检查存储桶名称拼写

4. **环境配置错误**
   - 检查 `prod` 环境变量设置
   - 验证配置文件路径和格式

### 调试技巧

启用详细日志：

```python
from loguru import logger
logger.remove()
logger.add(sys.stderr, level="DEBUG")
```

## 安全注意事项

1. **敏感信息**: 不要在代码中硬编码访问密钥
2. **网络安全**: 在生产环境中使用HTTPS连接
3. **访问控制**: 配置适当的用户权限和策略
4. **数据备份**: 定期备份重要数据

## 扩展功能

可以根据需要扩展以下功能：

- 图片格式转换
- 缩略图生成
- 图片压缩
- 元数据提取
- 批量重命名
- 自动分类

## 支持

如有问题，请检查：
1. 配置文件格式
2. 网络连接
3. MinIO服务状态
4. 日志输出信息
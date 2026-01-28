# CLIP推理模块

本目录包含基于CLIP模型的图像推理和处理功能。

## 文件说明

### clip_inference_scheduler.py
CLIP推理调度器，提供蘑菇图像的CLIP编码和处理功能。

#### 主要功能
- **实时图像处理**: 处理最近时间段的图片
- **批量图像处理**: 处理指定日期或库房的图片
- **系统验证**: 验证系统功能完整性
- **多模态编码**: CLIP图像编码 + LLaMA文本描述

### get_env_status.py
环境状态获取模块，提供环境数据查询和处理功能。

## 使用方法

### 1. 处理最近图片
```bash
# 处理最近1小时的图片
python src/clip/clip_inference_scheduler.py recent --hours 1

# 处理指定库房最近2小时的图片
python src/clip/clip_inference_scheduler.py recent --hours 2 --room-id 7

# 处理多个库房
python src/clip/clip_inference_scheduler.py recent --hours 1 --room-ids 7 8 9

# 限制每个库房处理的图片数量
python src/clip/clip_inference_scheduler.py recent --hours 2 --max-per-room 5
```

### 2. 批量处理
```bash
# 批量处理所有图片
python src/clip/clip_inference_scheduler.py batch-all

# 处理指定库房
python src/clip/clip_inference_scheduler.py batch-all --room-id 7

# 处理指定日期
python src/clip/clip_inference_scheduler.py batch-all --date-filter 20251231

# 调整批处理大小
python src/clip/clip_inference_scheduler.py batch-all --batch-size 20
```

### 3. 系统验证
```bash
# 系统功能验证
python src/clip/clip_inference_scheduler.py validate

# 指定每个库房处理的图片数量
python src/clip/clip_inference_scheduler.py validate --max-per-room 5
```

### 4. 测试模式
```bash
# 不保存到数据库，仅测试处理
python src/clip/clip_inference_scheduler.py recent --hours 1 --no-save
python src/clip/clip_inference_scheduler.py batch-all --no-save
```

### 5. 从项目根目录使用
```bash
# 通过主入口使用（自动重定向）
python main.py recent --hours 1
python main.py batch-all --date-filter 20251231
python main.py validate
```

## 功能特性

### 多模态图像处理
- **CLIP编码**: 使用CLIP模型进行512维图像向量化
- **LLaMA描述**: 本地LLaMA模型生成蘑菇生长情况描述
- **特征融合**: 图像特征(70%) + 文本特征(30%)的加权融合
- **环境集成**: 结合温度、湿度、CO₂等环境参数

### 智能数据管理
- **路径解析**: 自动解析蘑菇库号、采集时间等信息
- **时间序列**: 支持按时间范围查询和处理图片
- **库房映射**: MinIO库房号到环境配置库房号的智能映射
- **批量处理**: 高效的批量图片处理能力

### 性能优化
- **缓存机制**: 图片数据和设备配置缓存
- **共享实例**: 避免重复初始化MinIO客户端和CLIP模型
- **异步处理**: 支持大规模图片的异步处理
- **错误恢复**: 优雅的错误处理和降级机制

## 技术架构

### 核心组件
- **MushroomImageEncoder**: 核心图像编码器
- **RecentImageProcessor**: 最近图片处理器
- **EnvironmentDataProcessor**: 环境数据处理器
- **MinIOClient**: 对象存储客户端

### 数据流程
```
图片上传到MinIO
    ↓
路径解析和验证
    ↓
获取环境数据
    ↓
LLaMA生成描述
    ↓
多模态CLIP编码
    ↓
存储到PostgreSQL
    ↓
支持相似度搜索
```

### 存储架构
- **MinIO**: 图片文件对象存储
- **PostgreSQL**: 向量和元数据存储
- **pgvector**: 向量相似度搜索扩展
- **Redis**: 缓存和配置存储

## 处理统计

### 输出指标
- **处理统计**: 找到/处理/成功/失败/跳过数量
- **成功率**: 图像处理成功率百分比
- **库房分布**: 各库房的图片处理分布
- **执行时间**: 处理耗时统计
- **数据库统计**: 总记录数和环境控制记录数

### 监控信息
- **实时反馈**: 处理进度和状态实时显示
- **错误日志**: 详细的错误信息和堆栈跟踪
- **性能指标**: 处理速度和资源使用情况
- **质量检查**: 向量编码质量验证

## 配置说明

### 环境配置
- **开发环境**: 本地测试和开发配置
- **生产环境**: 生产部署配置
- **模型配置**: CLIP和LLaMA模型参数
- **数据库配置**: PostgreSQL连接参数

### 处理参数
- **批处理大小**: 默认10张图片，可调整
- **缓存时间**: 设备配置5分钟缓存
- **超时设置**: LLaMA处理600秒超时
- **并发控制**: 避免资源竞争

## 错误处理

### 异常类型
- **文件访问错误**: MinIO连接或文件损坏
- **模型推理错误**: CLIP或LLaMA模型异常
- **数据库错误**: PostgreSQL连接或存储异常
- **配置错误**: 环境配置或参数错误

### 恢复机制
- **优雅降级**: 组件失败不影响主流程
- **重试机制**: 临时错误自动重试
- **跳过策略**: 无法处理的图片自动跳过
- **状态保存**: 处理状态持久化

## 与调度器集成

CLIP推理功能已集成到系统调度器中：
- **定时任务**: 每天凌晨03:02:25自动执行
- **自动处理**: 处理前一天的所有图像数据
- **任务ID**: `daily_clip_inference`
- **监控日志**: `[CLIP_TASK]` 标签

详见 `src/scheduling/README.md` 了解调度器配置。

## 开发指南

### 添加新功能
1. 在相应的处理器类中添加方法
2. 在命令行参数中添加新选项
3. 在主函数中添加处理逻辑
4. 更新帮助文档和示例

### 性能调优
1. 调整批处理大小适应硬件性能
2. 优化缓存策略减少重复查询
3. 使用GPU加速模型推理
4. 监控内存使用避免泄漏

### 测试验证
1. 使用`--no-save`参数进行测试
2. 从小批量开始验证功能
3. 监控日志确保正常运行
4. 检查数据库存储结果

---

CLIP推理模块为蘑菇图像处理系统提供了强大的多模态AI分析能力，支持大规模图像处理和智能向量化存储。
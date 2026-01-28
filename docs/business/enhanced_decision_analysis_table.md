# 增强决策分析结果表设计文档

## 概述

本文档描述了新创建的 `enhanced_decision_analysis_results` 数据表的设计和使用方法。该表用于存储通过运行 `python scripts/analysis/run_enhanced_decision_analysis.py` 命令生成的监控格式增强决策分析结果。

## 表结构设计

### 表名
`enhanced_decision_analysis_results`

### 主要字段

#### 基本信息字段
- `id` (UUID): 主键，使用UUID4格式
- `room_id` (String): 库房编号，如 "611"
- `analysis_datetime` (DateTime): 分析时间
- `status` (String): 分析状态 (success, failed, warning)
- `output_format` (String): 输出格式 (monitoring, enhanced, both)

#### 策略信息字段
- `core_objective` (Text): 核心决策目标
- `priority_ranking` (JSON): 优先级排序列表
- `key_risk_points` (JSON): 关键风险点列表

#### 数据存储字段
- `device_recommendations` (JSON): 设备推荐配置，包含所有设备类型的详细参数调整建议
- `monitoring_points_config` (JSON): 监控点配置数据，符合monitoring_points_config.json格式
- `multi_image_analysis` (JSON): 多图像分析结果
- `similar_cases_info` (JSON): 相似历史案例信息
- `env_data_stats` (JSON): 环境数据统计
- `device_changes_stats` (JSON): 设备变更记录统计

#### 性能指标字段
- `processing_time` (Float): 处理耗时(秒)
- `analysis_time` (Float): 分析耗时(秒)
- `save_time` (Float): 保存耗时(秒)
- `memory_usage_mb` (Float): 内存使用量(MB)

#### 数据源信息字段
- `data_sources_count` (Integer): 数据源数量
- `total_records_processed` (Integer): 处理的总记录数
- `multi_image_count` (Integer): 多图像数量

#### 质量指标字段
- `warnings_count` (Integer): 警告数量
- `errors_count` (Integer): 错误数量
- `llm_fallback_used` (Boolean): 是否使用了LLM后备策略

#### 输出文件信息字段
- `output_file_path` (Text): 输出文件路径
- `output_file_size` (BigInteger): 输出文件大小(字节)

#### LLM相关信息字段
- `llm_model_used` (String): 使用的LLM模型
- `llm_response_time` (Float): LLM响应时间(秒)
- `llm_token_count` (Integer): LLM令牌数量
- `llm_fallback_used` (Boolean): 是否使用了LLM后备策略

#### 备份和调试字段
- `raw_output_data` (JSON): 完整的原始输出数据
- `error_messages` (JSON): 错误信息列表
- `warning_messages` (JSON): 警告信息列表

#### 时间戳字段
- `created_at` (DateTime): 创建时间
- `updated_at` (DateTime): 更新时间

### 索引设计

为了优化查询性能，创建了以下索引：

- `idx_room_analysis_time`: 复合索引 (room_id, analysis_datetime)
- `idx_analysis_datetime`: 分析时间索引
- `idx_room_id`: 库房ID索引
- `idx_status`: 状态索引
- `idx_output_format`: 输出格式索引
- `idx_created_at`: 创建时间索引

## 数据格式说明

### 监控点配置格式 (monitoring_points_config)

该字段存储符合 `src/configs/monitoring_points_config.json` 格式的数据：

```json
{
  "room_id": "611",
  "devices": {
    "air_cooler": [
      {
        "device_name": "TD1_Q611MDCH01",
        "device_alias": "air_cooler_611",
        "point_list": [
          {
            "point_alias": "on_off",
            "point_name": "OnOff",
            "remark": "冷风机开关",
            "change_type": "digital_on_off",
            "threshold": null,
            "enum_mapping": {"0": "关闭", "1": "开启"},
            "change": true,
            "old": 1,
            "new": 0,
            "level": "medium"
          }
        ]
      }
    ]
  },
  "metadata": {
    "generated_at": "2026-01-23T11:37:41.622884",
    "room_id": "611",
    "source": "enhanced_decision_analysis",
    "total_points": 31
  }
}
```

### 设备推荐格式 (device_recommendations)

该字段存储完整的设备推荐数据：

```json
{
  "air_cooler": {
    "tem_set": {
      "current_value": 18.5,
      "recommended_value": 18.5,
      "action": "maintain",
      "change_reason": "当前设定温度与目标区间一致，无需调整",
      "priority": "low",
      "urgency": "routine",
      "risk_assessment": {
        "adjustment_risk": "low",
        "no_action_risk": "medium",
        "impact_scope": "系统稳定性",
        "mitigation_measures": []
      }
    }
  }
}
```

### 多图像分析格式 (multi_image_analysis)

```json
{
  "total_images_analyzed": 144,
  "confidence_score": 0.945,
  "view_consistency": "high",
  "aggregation_method": "weighted_average"
}
```

## 使用方法

### 1. 创建表

```python
from src.utils.create_table import create_tables
create_tables()
```

### 2. 存储分析结果

#### 方法一：使用脚本存储
```bash
# 存储最新的输出文件
python scripts/store_decision_analysis_result.py --latest

# 存储指定文件
python scripts/store_decision_analysis_result.py --file output/enhanced_decision_analysis_611_20260123_122501.json

# 存储指定房间的最新文件
python scripts/store_decision_analysis_result.py --latest --room-id 611
```

#### 方法二：编程方式存储
```python
from src.utils.create_table import store_enhanced_decision_analysis_result
from datetime import datetime
import json

# 读取JSON数据
with open('output/enhanced_decision_analysis_611_20260123_122501.json', 'r') as f:
    data = json.load(f)

# 存储到数据库
record_id = store_enhanced_decision_analysis_result(
    result_data=data,
    room_id="611",
    analysis_datetime=datetime.now(),
    output_format="monitoring",
    output_file_path="output/enhanced_decision_analysis_611_20260123_122501.json"
)
```

### 3. 查询分析结果

#### 方法一：使用脚本查询
```bash
# 查询所有记录
python scripts/query_decision_analysis_results.py

# 查询特定房间的记录
python scripts/query_decision_analysis_results.py --room-id 611

# 查询最近的记录
python scripts/query_decision_analysis_results.py --limit 5

# 显示详细信息
python scripts/query_decision_analysis_results.py --room-id 611 --verbose

# 查询最近7天的记录
python scripts/query_decision_analysis_results.py --days 7
```

#### 方法二：编程方式查询
```python
from src.utils.create_table import query_enhanced_decision_analysis_results
from datetime import datetime, timedelta

# 查询特定房间的记录
results = query_enhanced_decision_analysis_results(
    room_id="611",
    limit=10
)

# 查询最近7天的记录
start_date = datetime.now() - timedelta(days=7)
results = query_enhanced_decision_analysis_results(
    start_date=start_date,
    status="success",
    limit=20
)

# 获取记录的摘要统计
for record in results:
    stats = record.get_summary_stats()
    print(f"Room {record.room_id}: {stats['changes_required']} changes required")
```

### 4. 数据转换

```python
# 转换为监控点格式
monitoring_format = record.to_monitoring_points_format()

# 获取摘要统计
summary_stats = record.get_summary_stats()
```

## 数据库规范化

### 设计原则
1. **单一职责**: 每个字段都有明确的用途和含义
2. **数据完整性**: 使用适当的约束和索引确保数据质量
3. **查询优化**: 基于常见查询模式设计索引
4. **扩展性**: JSON字段支持灵活的数据结构扩展
5. **兼容性**: 与现有系统的数据格式保持兼容

### 字段类型选择
- **UUID**: 使用UUID4作为主键，避免ID冲突
- **JSON**: 用于存储复杂的嵌套数据结构
- **DateTime**: 支持时区的时间戳
- **Float**: 用于性能指标的精确存储
- **Text**: 用于长文本内容
- **Boolean**: 用于标志位

### 索引策略
- **复合索引**: 针对常见的查询组合 (room_id + analysis_datetime)
- **单列索引**: 针对频繁过滤的字段
- **时间索引**: 支持时间范围查询

## 测试和验证

### 运行测试
```bash
# 运行完整的表功能测试
python scripts/test_enhanced_decision_table.py
```

### 验证数据完整性
```python
from src.utils.create_table import test_enhanced_decision_analysis_table

# 运行内置测试
success = test_enhanced_decision_analysis_table()
```

## 维护和监控

### 定期维护任务
1. **数据清理**: 定期清理过期的分析结果
2. **索引优化**: 根据查询模式调整索引
3. **性能监控**: 监控查询性能和存储使用情况

### 备份策略
1. **定期备份**: 定期备份表数据
2. **增量备份**: 基于时间戳的增量备份
3. **数据验证**: 验证备份数据的完整性

## 扩展和集成

### 与现有系统集成
1. **调度器集成**: 可以在调度器中自动存储分析结果
2. **API集成**: 提供REST API接口查询分析结果
3. **监控集成**: 集成到监控系统中进行实时监控

### 未来扩展
1. **分析结果比较**: 支持不同时间点的分析结果比较
2. **趋势分析**: 基于历史数据进行趋势分析
3. **自动化决策**: 基于分析结果自动执行决策

## 故障排除

### 常见问题
1. **导入错误**: 确保Python路径正确设置
2. **数据库连接**: 检查数据库配置和连接
3. **JSON格式**: 验证输入数据的JSON格式正确性

### 调试方法
1. **日志查看**: 查看详细的日志输出
2. **数据验证**: 使用测试脚本验证数据
3. **手动查询**: 直接查询数据库验证数据存储

## 总结

`enhanced_decision_analysis_results` 表提供了一个完整的解决方案来存储和管理增强决策分析结果。通过合理的表结构设计、完善的索引策略和丰富的功能接口，该表能够有效支持蘑菇种植环境控制系统的决策分析需求。

表的设计遵循了数据库规范化原则，确保了数据的完整性和查询性能。同时，通过提供便捷的脚本工具和编程接口，使得数据的存储、查询和分析变得简单高效。
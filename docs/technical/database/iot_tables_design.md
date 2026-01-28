# 决策分析两表设计实现文档

## 概述

本文档描述了决策分析静态配置表和动态结果表的设计实现，用于分离静态配置和动态结果数据，同时严格保证不重命名任何现有的key。

## 设计原则

### 1. 严格保持现有Key不变
- 所有来自JSON数据的字段名称保持不变
- `room_id`, `device_type`, `device_name`, `device_alias`, `point_alias`, `point_name`, `remark`, `change_type`, `threshold`, `enum_mapping`
- `change`, `old`, `new`, `level` 等动态字段保持原样

### 2. 两表分离设计
- **静态配置表**: 存储变更频率低的元数据
- **动态结果表**: 存储模型每次输出的调节结果

## 表结构设计

### 静态配置表 (decision_analysis_static_config)

#### 表名
`decision_analysis_static_config`

#### 设计目标
存储库房-设备-点位的"静态元数据"，通常变更频率低。

#### 记录粒度
一行 = 一个 point（点位），把 point_list 数组扁平化，每个点位落一行。

#### 字段设计

**✅ 必选字段（全部来自现有 key）**
- `room_id`: 库房编号
- `device_type`: 设备类型（来自 devices 的一级 key，如 air_cooler/fresh_air_fan/humidifier/grow_light）
- `device_name`: 设备名称（如 TD1_Q611MDCH01）
- `device_alias`: 设备别名（如 air_cooler_611）
- `point_alias`: 点位别名（如 on_off）
- `point_name`: 点位名称（如 OnOff）
- `remark`: 点位备注（如 冷风机开关）
- `change_type`: 变更类型（digital_on_off/analog_value/enum_state）
- `threshold`: 阈值（可空）
- `enum_mapping`: 枚举映射（JSON格式存储）

**➕ 推荐新增字段（不影响前端现有 key）**
- `config_version`: 配置版本号（递增整数）
- `is_active`: 是否启用（true/false）
- `effective_time`: 配置生效时间
- `source`: 配置来源（manual/import/platform等）
- `operator`: 配置维护人/系统
- `comment`: 配置备注

#### 唯一键约束
- 主约束：`(room_id, device_alias, point_alias)`
- 备选约束：`(room_id, device_name, point_name)`

#### 索引设计
```sql
-- 唯一约束索引
CREATE UNIQUE INDEX uq_decision_room_device_point ON decision_analysis_static_config (room_id, device_alias, point_alias);
CREATE UNIQUE INDEX uq_decision_room_device_point_name ON decision_analysis_static_config (room_id, device_name, point_name);

-- 查询索引
CREATE INDEX idx_decision_room_device_type ON decision_analysis_static_config (room_id, device_type);
CREATE INDEX idx_decision_static_device_alias ON decision_analysis_static_config (device_alias);
CREATE INDEX idx_decision_static_point_alias ON decision_analysis_static_config (point_alias);
CREATE INDEX idx_decision_is_active ON decision_analysis_static_config (is_active);
CREATE INDEX idx_decision_config_version ON decision_analysis_static_config (config_version);
CREATE INDEX idx_decision_effective_time ON decision_analysis_static_config (effective_time);
```

### 动态结果表 (decision_analysis_dynamic_result)

#### 表名
`decision_analysis_dynamic_result`

#### 设计目标
存储模型每次输出的"调节结果"，高频写入用于回溯、对比、下发闭环。

#### 记录粒度
一行 = 一个 point 在一次推理中的结果

#### 写入策略
支持两种策略：
- **全量写入**: 所有 point 都写（change=true/false都写）→ 便于回放、分析
- **增量写入**: 只写 change=true 的点 → 更省存储，但回放需要结合静态表

#### 字段设计

**✅ 必选字段（保持现有 key 不变）**
- `room_id`: 库房编号
- `device_type`: 设备类型
- `device_alias`: 设备别名
- `point_alias`: 点位别名
- `change`: 是否变更（true/false）
- `old`: 变更前值（string存储，兼容数字/枚举/开关量）
- `new`: 变更后值（string存储，兼容数字/枚举/开关量）
- `level`: 变更级别（high/medium/low）

**➕ 强烈建议新增字段（用于一致性、追溯、闭环）**

**(1) 事件定位 / 去重**
- `batch_id`: 一次模型推理批次号（把同一批输出聚合在一起）
- `time`: 推理产生时间（毫秒级时间戳）
- `trace_id`: 链路追踪ID（可选）

**(2) 模型可追溯**
- `model_name`: 模型名称
- `model_version`: 模型版本
- `strategy_version`: 策略版本/规则版本（可选）
- `confidence`: 置信度（可选）

**(3) 可解释性**
- `reason`: 变更原因/解释
- `features`: 关键特征快照（JSON）
- `rule_hit`: 命中规则列表（JSON/array）

**(4) 控制闭环**
- `status`: 状态（pending/applied/rejected/failed）
- `apply_time`: 实际下发时间
- `apply_result`: 下发结果（成功/失败原因）
- `operator`: 操作者（人/系统）
- `rollback`: 是否回滚

**(5) 便于排查的冗余字段**
- `device_name`: 设备名称（冗余存储减少join）
- `point_name`: 点位名称（冗余存储）
- `remark`: 点位备注（可选冗余）

#### 索引设计
```sql
-- 查询索引
CREATE INDEX idx_decision_room_batch_time ON decision_analysis_dynamic_result (room_id, batch_id, time);
CREATE INDEX idx_decision_batch_id ON decision_analysis_dynamic_result (batch_id);
CREATE INDEX idx_decision_time ON decision_analysis_dynamic_result (time);
CREATE INDEX idx_decision_dynamic_device_point ON decision_analysis_dynamic_result (device_alias, point_alias);
CREATE INDEX idx_decision_change_status ON decision_analysis_dynamic_result (change, status);
CREATE INDEX idx_decision_room_time ON decision_analysis_dynamic_result (room_id, time);
CREATE INDEX idx_decision_dynamic_device_type ON decision_analysis_dynamic_result (device_type);
CREATE INDEX idx_decision_dynamic_status ON decision_analysis_dynamic_result (status);
CREATE INDEX idx_decision_dynamic_apply_time ON decision_analysis_dynamic_result (apply_time);
```

## 两表关联方式

### 推荐关联键
`room_id + device_alias + point_alias`

因为动态表中天然包含 device_alias/point_alias，静态表也有同样 key，join 成本低。

### 关联查询示例
```sql
-- 查询某个批次的变更及其静态配置
SELECT 
    d.room_id,
    d.device_alias,
    d.point_alias,
    d.change,
    d.old,
    d.new,
    d.level,
    s.remark,
    s.change_type,
    s.threshold,
    s.enum_mapping
FROM decision_analysis_dynamic_result d
JOIN decision_analysis_static_config s ON (
    d.room_id = s.room_id 
    AND d.device_alias = s.device_alias 
    AND d.point_alias = s.point_alias
)
WHERE d.batch_id = 'batch_611_20260123_113712'
  AND d.change = true;
```

## 版本管理策略

### 静态配置版本管理
- **版本号**: 使用递增整数 `config_version`
- **版本触发**: 基于文件修改时间和内容哈希
- **版本策略**: 
  - 文件内容变化时自动递增版本号
  - 支持强制更新模式
  - 保留历史版本记录

### 动态结果批次管理
- **批次ID**: 格式 `{room_id}_{datetime}_{random_hash}`
- **批次追踪**: 同一次推理的所有点位使用相同批次ID
- **时间戳**: 毫秒级精度，支持高频写入

## 实现功能

### 1. 数据提取和转换
- `extract_static_config_from_json()`: 从JSON提取静态配置
- `extract_dynamic_results_from_json()`: 从JSON提取动态结果
- 支持多种JSON格式（monitoring/enhanced/both）

### 2. 数据存储
- `store_static_point_configs()`: 批量存储静态配置（支持更新）
- `store_dynamic_point_results()`: 批量存储动态结果
- `store_iot_analysis_results()`: 一站式存储接口

### 3. 数据查询
- `query_static_point_configs()`: 静态配置查询（支持多维度过滤）
- `query_dynamic_point_results()`: 动态结果查询（支持时间范围、变更过滤等）

### 4. 静态配置导入
- `import_static_config.py`: 从static_config.json导入配置
- 支持版本管理和变更检测
- 支持预览模式和增量更新

### 5. 命令行工具
- `scripts/import_static_config.py`: 静态配置导入工具
- `scripts/store_iot_analysis_results.py`: IoT分析结果存储工具
- `scripts/query_iot_results.py`: IoT数据查询工具
- `scripts/test_iot_tables.py`: 功能测试工具

## 使用示例

### 导入静态配置
```bash
# 导入所有静态配置
python scripts/import_static_config.py

# 导入指定库房的配置
python scripts/import_static_config.py --room-id 611

# 预览模式（不实际写入）
python scripts/import_static_config.py --dry-run
```

### 存储分析结果
```bash
# 存储指定JSON文件
python scripts/store_iot_analysis_results.py --file output/enhanced_decision_analysis_611_20260123_113712.json

# 存储最新输出文件
python scripts/store_iot_analysis_results.py --latest --room-id 611
```

### 查询数据
```bash
# 查询静态配置
python scripts/query_iot_results.py --type static --room-id 611

# 查询动态结果（只显示变更）
python scripts/query_iot_results.py --type dynamic --changes-only --limit 10

# 显示详细信息
python scripts/query_iot_results.py --type static --verbose --limit 5
```

## 数据统计

### 当前导入状态
- **静态配置**: 184条记录
  - 4个库房（607, 608, 611, 612）
  - 6种设备类型（air_cooler, fresh_air_fan, humidifier, grow_light, mushroom_info, mushroom_env_status）
  - 完整的点位配置信息

- **动态结果**: 31条记录（来自示例JSON）
  - 支持变更追踪和批次管理
  - 包含完整的推理上下文信息

### 设备类型分布
- `air_cooler`: 40个点位配置
- `fresh_air_fan`: 28个点位配置  
- `grow_light`: 48个点位配置
- `humidifier`: 32个点位配置
- `mushroom_env_status`: 12个点位配置
- `mushroom_info`: 24个点位配置

## 优势特点

### 1. 数据分离清晰
- 静态配置和动态结果完全分离
- 减少数据冗余，提高查询效率
- 支持独立的版本管理策略

### 2. 兼容性保证
- 严格保持现有key不变
- 支持多种JSON格式
- 向后兼容现有系统

### 3. 扩展性强
- 支持新增字段而不影响现有功能
- 灵活的索引策略
- 可配置的写入策略

### 4. 可追溯性
- 完整的版本历史记录
- 批次级别的变更追踪
- 支持审计和回滚

### 5. 高性能
- 优化的索引设计
- 支持批量操作
- 高效的关联查询

## 未来扩展

### 1. 实时同步
- 支持配置文件变更的实时监控
- 自动触发版本更新

### 2. 数据校验
- 配置一致性检查
- 数据完整性验证

### 3. 可视化界面
- 配置管理界面
- 变更历史查看
- 批次结果分析

### 4. API接口
- RESTful API支持
- 配置CRUD操作
- 批量数据导入导出

## 总结

IoT两表设计成功实现了静态配置和动态结果的分离存储，在保持现有key不变的前提下，提供了完整的版本管理、批次追踪和数据查询功能。该设计为IoT系统的配置管理和结果分析提供了坚实的数据基础。
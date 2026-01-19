# 监控点配置提取工具使用指南

## 概述

`extract_monitoring_point_configs.py` 是一个用于从配置文件中提取所有监控点完整配置信息的工具。它整合了 `setpoint_monitor_config.json` 和 `static_config.json` 两个文件的信息，生成结构化的监控点配置数据。

---

## 功能特性

### 1. 配置信息提取

从两个配置文件中提取并整合以下信息：

- **设备类型** (device_type): 如 air_cooler, fresh_air_fan 等
- **监控点别名** (point_alias): 用户友好的别名
- **系统标识符** (point_name): 系统内部使用的标识符
- **描述信息** (remark): 监控点的中文描述
- **变更检测类型** (change_type): 如 analog_value, digital_on_off, enum_state
- **变化阈值** (threshold): 模拟量的变化阈值
- **枚举值映射** (enum_mapping): 枚举类型的值与含义映射

### 2. 智能类型推断

程序能够智能推断监控点的变更检测类型：

- **analog_value**: 有阈值配置的监控点
- **digital_on_off**: 以 `_on_off` 结尾或以 `on_off` 开头的监控点
- **enum_state**: mode, model, control, status 或以 choose 开头的监控点

### 3. 配置摘要生成

自动生成配置摘要信息：

- 设备类型数量
- 总监控点数量
- 配置阈值的监控点数量
- 配置枚举值的监控点数量
- 变更类型分布统计

---

## 使用方法

### 基本用法

#### 1. 输出到控制台

```bash
# 输出JSON到控制台（紧凑格式）
python scripts/extract_monitoring_point_configs.py

# 输出JSON到控制台（格式化）
python scripts/extract_monitoring_point_configs.py --pretty
```

#### 2. 保存到文件

```bash
# 保存到指定文件
python scripts/extract_monitoring_point_configs.py --output monitoring_points.json

# 保存到文件并格式化
python scripts/extract_monitoring_point_configs.py --output monitoring_points.json --pretty
```

#### 3. 不包含摘要信息

```bash
# 只输出监控点配置，不包含摘要
python scripts/extract_monitoring_point_configs.py --no-summary
```

### 高级用法

#### 指定配置文件路径

```bash
python scripts/extract_monitoring_point_configs.py \
  --monitor-config /path/to/setpoint_monitor_config.json \
  --static-config /path/to/static_config.json \
  --output result.json \
  --pretty
```

---

## 输出格式

### JSON结构

```json
{
  "monitoring_points": {
    "device_type_1": [
      {
        "device_type": "air_cooler",
        "point_alias": "on_off",
        "point_name": "OnOff",
        "remark": "冷风机开关",
        "change_type": "digital_on_off",
        "threshold": null,
        "enum_mapping": {
          "0": "关闭",
          "1": "开启"
        }
      },
      ...
    ],
    "device_type_2": [...]
  },
  "summary": {
    "total_device_types": 5,
    "total_monitoring_points": 30,
    "device_type_summary": {...},
    "change_type_distribution": {...},
    "threshold_configured_count": 14,
    "enum_configured_count": 16
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| device_type | string | 设备类型 | "air_cooler" |
| point_alias | string | 监控点别名 | "on_off" |
| point_name | string | 系统标识符 | "OnOff" |
| remark | string | 中文描述 | "冷风机开关" |
| change_type | string | 变更检测类型 | "digital_on_off" |
| threshold | float/null | 变化阈值 | 0.5 或 null |
| enum_mapping | object/null | 枚举值映射 | {"0": "关闭", "1": "开启"} |

---

## 实际示例

### 示例1: 冷风机监控点

```json
{
  "device_type": "air_cooler",
  "point_alias": "temp_set",
  "point_name": "TemSet",
  "remark": "温度设定(分辨率0.1)",
  "change_type": "analog_value",
  "threshold": 0.5,
  "enum_mapping": null
}
```

**说明**: 
- 这是一个模拟量监控点
- 温度变化超过 0.5°C 时才记录
- 没有枚举值映射

### 示例2: 新风机模式

```json
{
  "device_type": "fresh_air_fan",
  "point_alias": "mode",
  "point_name": "Model",
  "remark": "新风模式",
  "change_type": "enum_state",
  "threshold": null,
  "enum_mapping": {
    "0": "关闭模式",
    "1": "自动模式",
    "2": "手动模式"
  }
}
```

**说明**:
- 这是一个枚举状态监控点
- 不需要阈值（任何状态变化都记录）
- 有3个枚举值及其含义

### 示例3: 补光灯开关

```json
{
  "device_type": "grow_light",
  "point_alias": "on_off1",
  "point_name": "OnOff1",
  "remark": "1#补光开关",
  "change_type": "digital_on_off",
  "threshold": null,
  "enum_mapping": {
    "0": "关闭",
    "1": "自动"
  }
}
```

**说明**:
- 这是一个数字量开关监控点
- 不需要阈值（0/1变化即记录）
- 有2个枚举值

---

## 配置摘要示例

运行脚本后会显示配置摘要：

```
✅ 配置已保存到: monitoring_points_config.json

📊 配置摘要:
  - 设备类型数: 5
  - 总监控点数: 30
  - 配置阈值的监控点: 14
  - 配置枚举值的监控点: 16

  变更类型分布:
    - digital_on_off: 8
    - analog_value: 14
    - enum_state: 8
```

---

## 监控点统计

### 按设备类型统计

| 设备类型 | 监控点数量 | 说明 |
|---------|-----------|------|
| air_cooler | 8 | 冷风机设备 |
| fresh_air_fan | 6 | 新风机设备 |
| humidifier | 3 | 加湿器设备 |
| grow_light | 11 | 补光灯设备 |
| mushroom_info | 2 | 蘑菇信息 |
| **总计** | **30** | - |

### 按变更类型统计

| 变更类型 | 数量 | 说明 |
|---------|------|------|
| analog_value | 14 | 模拟量（需要阈值） |
| digital_on_off | 8 | 数字量开关 |
| enum_state | 8 | 枚举状态 |
| **总计** | **30** | - |

---

## 错误处理

程序包含完善的错误处理机制：

### 1. 配置文件不存在

```
[EXTRACT-002] 监控配置文件不存在: /path/to/config.json
配置文件加载失败，程序退出
```

### 2. JSON格式错误

```
[EXTRACT-004] JSON解析错误: Expecting property name enclosed in double quotes
配置文件加载失败，程序退出
```

### 3. 监控点配置缺失

```
[EXTRACT-007] 未找到监控点配置 | 设备类型: air_cooler, point_alias: unknown_point
```

程序会跳过缺失的监控点，继续处理其他配置。

---

## 日志级别

程序使用 loguru 进行日志记录，日志编号说明：

| 日志编号 | 级别 | 说明 |
|---------|------|------|
| EXTRACT-001 | INFO | 初始化配置提取器 |
| EXTRACT-002 | INFO | 加载监控配置文件 |
| EXTRACT-003 | INFO | 加载静态配置文件 |
| EXTRACT-004 | ERROR | 配置文件加载失败 |
| EXTRACT-005 | INFO | 开始提取监控点配置 |
| EXTRACT-006 | INFO | 处理设备类型 |
| EXTRACT-007 | DEBUG/WARNING | 提取监控点详情 |
| EXTRACT-008 | INFO | 提取完成统计 |
| EXTRACT-009 | INFO | 保存文件 |

---

## 应用场景

### 1. 配置验证

验证监控配置的完整性和一致性：

```bash
python scripts/extract_monitoring_point_configs.py --pretty | grep -A 5 "threshold"
```

### 2. 文档生成

生成监控点配置文档：

```bash
python scripts/extract_monitoring_point_configs.py \
  --output docs/monitoring_points_reference.json \
  --pretty
```

### 3. 配置迁移

导出配置用于系统迁移或备份：

```bash
python scripts/extract_monitoring_point_configs.py \
  --output backup/monitoring_points_$(date +%Y%m%d).json \
  --pretty
```

### 4. API接口

作为API的数据源，提供监控点配置查询：

```python
import json

# 加载配置
with open('monitoring_points_config.json', 'r') as f:
    config = json.load(f)

# 查询特定设备类型的监控点
air_cooler_points = config['monitoring_points']['air_cooler']

# 查询特定监控点
for point in air_cooler_points:
    if point['point_alias'] == 'temp_set':
        print(f"阈值: {point['threshold']}")
```

### 5. 大模型输入

将配置作为大模型的上下文，用于理解设备操作记录：

```python
# 将配置转换为提示词
def generate_prompt(config):
    prompt = "以下是设备监控点配置信息：\n\n"
    
    for device_type, points in config['monitoring_points'].items():
        prompt += f"## {device_type}\n"
        for point in points:
            prompt += f"- {point['point_alias']}: {point['remark']}\n"
            if point['enum_mapping']:
                prompt += f"  枚举值: {point['enum_mapping']}\n"
    
    return prompt
```

---

## 命令行参数

### 完整参数列表

```
usage: extract_monitoring_point_configs.py [-h] [--output OUTPUT] [--pretty]
                                           [--no-summary]
                                           [--monitor-config MONITOR_CONFIG]
                                           [--static-config STATIC_CONFIG]

提取监控点配置信息

optional arguments:
  -h, --help            显示帮助信息
  --output OUTPUT, -o OUTPUT
                        输出文件路径（如果不指定则输出到控制台）
  --pretty, -p          格式化输出JSON
  --no-summary          不包含摘要信息
  --monitor-config MONITOR_CONFIG
                        监控配置文件路径（默认: src/configs/setpoint_monitor_config.json）
  --static-config STATIC_CONFIG
                        静态配置文件路径（默认: src/configs/static_config.json）
```

---

## 常见问题

### Q1: 为什么某些监控点没有阈值？

**A**: 数字量开关和枚举状态类型的监控点不需要阈值，任何状态变化都会被记录。只有模拟量类型的监控点才需要配置阈值。

### Q2: 如何添加新的监控点？

**A**: 需要在两个配置文件中同时添加：
1. 在 `setpoint_monitor_config.json` 的 `device_types` 中添加到 `monitored_points` 列表
2. 在 `static_config.json` 的对应设备类型的 `point_list` 中添加详细配置

### Q3: 枚举值映射从哪里来？

**A**: 枚举值映射来自 `static_config.json` 中每个监控点的 `enum` 字段。

### Q4: 如何修改变更检测类型？

**A**: 变更检测类型由程序自动推断，基于：
- 是否有阈值配置
- 监控点名称模式
- 是否有枚举值映射

如需修改，可以在程序的 `_get_change_type` 方法中调整推断逻辑。

---

## 相关文档

- **设备监控点完整参考**: `docs/device_monitoring_points_reference.md`
- **监控配置文件**: `src/configs/setpoint_monitor_config.json`
- **静态配置文件**: `src/configs/static_config.json`
- **源代码**: `scripts/extract_monitoring_point_configs.py`

---

## 更新日志

### v1.0 (2026-01-14)

- ✅ 初始版本发布
- ✅ 支持从两个配置文件提取监控点信息
- ✅ 智能推断变更检测类型
- ✅ 生成配置摘要
- ✅ 完善的错误处理
- ✅ 支持多种输出格式

---

**工具版本**: 1.0  
**最后更新**: 2026-01-14  
**维护者**: 蘑菇房环境控制系统团队

# DBeaver Unicode显示问题解决方案

## 问题描述

在使用DBeaver查看PostgreSQL数据库中的中文数据时，enum_mapping字段显示为Unicode转义序列：
```json
{"0": "\u5173\u95ed", "1": "\u5f00\u542f"}
```

而不是正确的中文显示：
```json
{"0": "关闭", "1": "开启"}
```

## 问题分析

经过详细分析，确认这不是数据库存储问题，而是JSON序列化和客户端显示问题：

### 1. 数据库存储正确
- PostgreSQL数据库使用UTF-8编码
- 中文字符在数据库中正确存储为：`{'0': '关闭', '1': '开启'}`
- 数据库连接配置已正确设置UTF-8编码

### 2. JSON序列化问题
- Python的`json.dumps()`默认行为是将非ASCII字符转义为Unicode序列
- 默认设置：`json.dumps(data)` → `{"0": "\u5173\u95ed", "1": "\u5f00\u542f"}`
- 正确设置：`json.dumps(data, ensure_ascii=False)` → `{"0": "关闭", "1": "开启"}`

### 3. DBeaver显示问题
- DBeaver可能在导出/显示JSON数据时使用了默认的JSON序列化设置
- 这导致中文字符被转义为Unicode序列

## 解决方案

### 方案1：使用UTF-8 CSV导出工具

我们提供了专门的CSV导出工具，确保正确的UTF-8编码：

```bash
# 导出静态配置表
python scripts/export_csv_utf8.py --table static --output static_config_utf8.csv --check

# 导出动态结果表
python scripts/export_csv_utf8.py --table dynamic --output dynamic_results_utf8.csv --check

# 创建测试样本
python scripts/export_csv_utf8.py --sample --table static --output sample.csv
```

### 方案2：使用enum_mapping查看工具

专门的查看工具可以正确显示中文：

```bash
# 查看所有enum_mapping
python scripts/view_enum_mapping.py

# 查看特定房间
python scripts/view_enum_mapping.py --room-id 611

# 比较不同JSON编码方式
python scripts/view_enum_mapping.py --compare
```

### 方案3：DBeaver配置优化

在DBeaver中进行以下设置：

1. **导入CSV时**：
   - 文件编码选择：UTF-8
   - 字符集：UTF-8
   - 确保"检测编码"选项开启

2. **导出数据时**：
   - 选择UTF-8编码
   - 在高级选项中检查字符编码设置

3. **查看JSON数据时**：
   - 在结果集中右键选择"查看值"
   - 使用文本编辑器模式查看完整内容

## 验证结果

### 数据库存储验证
```bash
python scripts/view_enum_mapping.py --limit 5
```

输出显示中文正确存储：
```
记录 1:
  🏠 房间: 607
  🔧 设备: air_cooler (air_cooler_607)
  📍 点位: air_on_off (ArOnOff)
  📝 备注: 新风联动冷风机开关
  🏷️  枚举映射:
      0 = 关闭
      1 = 开启
```

### JSON编码对比验证
```bash
python scripts/view_enum_mapping.py --compare
```

输出显示编码差异：
```
JSON编码对比:
  默认设置:        {"0": "\u5173\u95ed", "1": "\u5f00\u542f"}
  ensure_ascii=False: {"0": "关闭", "1": "开启"}
  ensure_ascii=True:  {"0": "\u5173\u95ed", "1": "\u5f00\u542f"}

  ⚠️  注意: 默认设置会将中文转义为Unicode序列
  ✅ 推荐: 使用 ensure_ascii=False 保持中文字符
```

### CSV导出验证
```bash
python scripts/export_csv_utf8.py --table static --output test.csv --check
```

输出确认UTF-8编码正确：
```
=== CSV文件内容检查 (test.csv) ===
第2行: ...,"{'0': '关闭', '1': '开启'}",1,True,...
✅ 文件包含中文字符，编码正确
```

## 数据统计

- **静态配置表**：184条记录，100%包含中文字符
- **动态结果表**：31条记录，包含中文备注和描述
- **enum_mapping字段**：所有枚举映射正确存储中文值

## 工具文件

1. **scripts/export_csv_utf8.py** - UTF-8 CSV导出工具
2. **scripts/view_enum_mapping.py** - enum_mapping查看工具
3. **scripts/fix_enum_mapping_encoding.py** - 编码诊断工具
4. **scripts/verify_chinese_display.py** - 中文显示验证工具

## 结论

1. **数据库存储完全正确** - 中文字符以正确的UTF-8格式存储
2. **问题在于显示层** - JSON序列化和客户端显示设置导致Unicode转义
3. **解决方案已提供** - 多种工具和配置方法确保正确显示中文
4. **建议使用专用工具** - 避免依赖第三方客户端的默认设置

## 使用建议

1. **日常查看**：使用`scripts/view_enum_mapping.py`查看enum_mapping数据
2. **数据导出**：使用`scripts/export_csv_utf8.py`导出UTF-8格式的CSV文件
3. **DBeaver配置**：按照上述方案3配置DBeaver的编码设置
4. **数据验证**：定期使用验证工具确保数据完整性

通过这些解决方案，可以确保中文字符在所有环节都能正确显示和处理。
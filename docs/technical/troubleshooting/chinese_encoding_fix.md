# 中文字符编码问题解决方案

## 问题描述

在IoT两表设计实现过程中，发现写入数据表的中文字符出现乱码问题。

## 问题分析

### 1. 数据库连接编码设置
原始的PostgreSQL连接配置中缺少明确的UTF-8编码设置，可能导致中文字符在传输过程中出现编码问题。

### 2. 客户端编码配置
客户端连接参数中没有明确指定字符编码，依赖默认设置可能不稳定。

## 解决方案

### 1. 修复数据库连接配置

在 `src/global_const/global_const.py` 中修改PostgreSQL连接配置：

```python
# PostgreSQL引擎配置 - 针对Docker网络环境优化
pgsql_engine = sqlalchemy.create_engine(
    pg_engine_url,
    pool_pre_ping=True,          # 连接前检查连接是否有效
    pool_recycle=1800,            # 连接回收时间（30分钟）
    pool_size=5,                  # 连接池大小
    max_overflow=10,              # 最大溢出连接数
    pool_timeout=30,              # 获取连接的超时时间（秒）
    connect_args={
        "connect_timeout": 10,    # TCP连接超时（秒）- 适应Docker网络
        "options": "-c statement_timeout=300000 -c client_encoding=UTF8",  # SQL语句超时（5分钟）+ UTF8编码
        "client_encoding": "utf8"  # 明确设置客户端编码为UTF-8
    },
    echo=False,                   # 不输出SQL日志
    future=True                   # 使用SQLAlchemy 2.0风格
)
```

### 2. 关键修改点

- **添加client_encoding参数**: 在`connect_args`中明确设置`"client_encoding": "utf8"`
- **options参数增强**: 在PostgreSQL options中添加`-c client_encoding=UTF8`
- **双重保障**: 通过两种方式确保UTF-8编码设置生效

### 3. 编码检查和修复工具

创建了 `scripts/fix_chinese_encoding.py` 工具，提供以下功能：

#### 功能特性
- **编码检查**: 检查数据库服务器和客户端编码设置
- **中文测试**: 测试中文字符的存储和读取
- **数据验证**: 检查现有数据的编码完整性
- **自动修复**: 尝试修复数据库编码设置
- **测试清理**: 清理测试数据

#### 使用方法
```bash
# 检查编码设置
python scripts/fix_chinese_encoding.py --check

# 测试中文字符存储
python scripts/fix_chinese_encoding.py --test

# 检查现有数据编码
python scripts/fix_chinese_encoding.py --check-data

# 修复编码设置
python scripts/fix_chinese_encoding.py --fix

# 清理测试数据
python scripts/fix_chinese_encoding.py --cleanup
```

### 4. 中文字符验证工具

创建了 `scripts/verify_chinese_display.py` 工具，用于：

#### 功能特性
- **全面分析**: 分析数据库中所有中文字符
- **统计报告**: 提供详细的中文字符统计信息
- **样本展示**: 显示中文文本样本
- **词汇验证**: 验证常见中文词汇是否正确显示

#### 使用方法
```bash
python scripts/verify_chinese_display.py
```

## 验证结果

### 1. 数据库编码设置
```
Database encoding information:
  - Server encoding: UTF8
  - Client encoding: UTF8
  - Database name: mushroom_algorithm
  - Encoding ID: 6
  - Collate: en_US.utf8
  - Ctype: en_US.utf8
```

### 2. 中文字符测试结果
- ✅ 设备名称中文字符正确: "测试设备_001"
- ✅ 备注中文字符正确: "这是一个中文测试点位：温度传感器（分辨率0.1°C）"
- ✅ JSON字段中文字符正确: {"0": "关闭", "1": "开启", "2": "自动模式"}

### 3. 现有数据验证
- **总记录数**: 184
- **包含中文的记录数**: 184
- **中文覆盖率**: 100.0%
- **中文字段统计**:
  - remark: 184个记录包含中文
  - enum_mapping: 84个记录包含中文

### 4. 常见中文词汇验证
- ✅ '开关' - 找到
- ✅ '设定' - 找到  
- ✅ '温度' - 找到
- ✅ '湿度' - 找到
- ✅ '关闭' - 找到
- ✅ '开启' - 找到
- ✅ '自动' - 找到
- ✅ '手动' - 找到

## 中文字符样本

### 静态配置表中的中文
- "新风联动冷风机开关"
- "冷风机循环关闭时间设定"
- "温度设定(分辨率0.1)"
- "库内温度(分辨率0.1)"
- "CO2启动新风"
- "加湿状态"
- "补光模式"
- "进库包数"
- "库内湿度"

### 枚举值中的中文
- {"0": "关闭", "1": "开启"}
- {"0": "关闭模式", "1": "自动模式", "2": "手动模式"}
- {"0": "时控", "1": "CO2控制"}
- {"0": "加湿自动运行", "1": "加湿手动运行", "2": "加湿停止", "3": "加湿关闭"}

## 最佳实践

### 1. 数据库连接配置
- 始终明确设置客户端编码为UTF-8
- 使用多种方式确保编码设置生效
- 在连接参数中包含编码相关选项

### 2. 数据验证
- 定期运行编码检查工具
- 在数据导入后验证中文字符显示
- 保持测试用例覆盖中文字符场景

### 3. 问题排查
- 使用提供的工具进行系统性检查
- 从数据库设置、连接配置、数据内容多个层面排查
- 保留测试数据用于对比验证

## 工具文件

1. **`scripts/fix_chinese_encoding.py`** - 编码问题检查和修复工具
2. **`scripts/verify_chinese_display.py`** - 中文字符显示验证工具
3. **`docs/chinese_encoding_fix.md`** - 本解决方案文档

## 总结

通过修改PostgreSQL连接配置中的编码设置，并创建相应的检查和验证工具，成功解决了中文字符乱码问题。现在数据库中的中文字符能够正确存储和显示，覆盖率达到100%，包括：

- 设备和点位的中文描述
- 枚举值的中文标签
- JSON字段中的中文内容
- 特殊符号（℃、°、±等）

该解决方案确保了IoT系统中中文信息的完整性和可读性。
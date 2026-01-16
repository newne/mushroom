# 决策分析模块 (Decision Analysis Module)

蘑菇种植智能调控系统的决策分析模块，通过多源数据提取、CLIP相似度匹配、模板渲染和大语言模型分析，生成智能化的环境调控建议。

## 📋 目录

- [功能概述](#功能概述)
- [系统架构](#系统架构)
- [安装说明](#安装说明)
- [快速开始](#快速开始)
- [使用示例](#使用示例)
- [API文档](#api文档)
- [配置说明](#配置说明)
- [数据模型](#数据模型)
- [错误处理](#错误处理)
- [性能优化](#性能优化)
- [常见问题](#常见问题)

## 🎯 功能概述

决策分析模块是蘑菇种植智能调控系统的核心组件，提供以下功能：

### 核心功能

1. **多源数据提取**
   - 从PostgreSQL数据库提取图像嵌入数据（MushroomImageEmbedding表）
   - 提取环境统计数据（MushroomEnvDailyStats表）
   - 提取设备变更记录（DeviceSetpointChange表）
   - 智能筛选：基于库房、时间、生长阶段等维度

2. **CLIP相似度匹配**
   - 使用pgvector进行向量相似度搜索
   - 查找Top-3最相似的历史案例
   - 计算相似度分数（0-100%）
   - 置信度评估（high/medium/low）

3. **模板渲染**
   - 使用Jinja2模板引擎
   - 将提取的数据映射到decision_prompt.jinja模板
   - 生成结构化的决策提示词

4. **大语言模型分析**
   - 调用LLaMA API生成决策建议
   - 生成蘑菇生长状态评估
   - 提供环境调控策略建议
   - 输出设备参数调整方案

5. **结构化输出**
   - 验证设备参数符合static_config.json规范
   - 格式化输出JSON和可读文本
   - 提供详细的判断依据和监控建议

### 技术特性

- **模块化设计**: 各功能模块独立，接口清晰
- **高性能**: 使用数据库索引和向量索引优化查询
- **容错性强**: 完善的错误处理和降级机制
- **可扩展**: 支持插件式添加新功能
- **详细日志**: 使用Loguru记录所有关键操作

## 🏗️ 系统架构

```
决策分析模块
├── DecisionAnalyzer (主控制器)
│   ├── DataExtractor (数据提取器)
│   ├── CLIPMatcher (相似度匹配器)
│   ├── TemplateRenderer (模板渲染器)
│   ├── LLMClient (大语言模型客户端)
│   └── OutputHandler (输出处理器)
│
├── 数据层
│   ├── PostgreSQL数据库
│   ├── MushroomImageEmbedding (图像嵌入表)
│   ├── MushroomEnvDailyStats (环境统计表)
│   └── DeviceSetpointChange (设备变更表)
│
├── 配置层
│   ├── settings.toml (系统配置)
│   ├── static_config.json (设备配置)
│   └── decision_prompt.jinja (决策模板)
│
└── 外部服务
    └── LLaMA API (大语言模型服务)
```

### 数据流程

1. **数据提取** → 从数据库提取当前状态、历史统计、设备变更
2. **相似度匹配** → 使用CLIP向量查找相似历史案例
3. **模板渲染** → 将数据填充到Jinja2模板
4. **LLM分析** → 调用大语言模型生成决策
5. **输出验证** → 验证并格式化决策输出

## 📦 安装说明

### 系统要求

- Python 3.10+
- PostgreSQL 14+ with pgvector extension
- 8GB+ RAM
- 网络访问LLaMA API

### 依赖安装

```bash
# 安装核心依赖
pip install -r requirements.txt

# 主要依赖包
# - sqlalchemy>=2.0.0
# - psycopg2-binary>=2.9.0
# - pandas>=2.0.0
# - numpy>=1.24.0
# - jinja2>=3.1.0
# - dynaconf>=3.2.0
# - loguru>=0.7.0
# - requests>=2.31.0
```

### 数据库配置

确保PostgreSQL已安装pgvector扩展：

```sql
-- 创建pgvector扩展
CREATE EXTENSION IF NOT EXISTS vector;

-- 验证安装
SELECT * FROM pg_extension WHERE extname = 'vector';
```

### 配置文件

1. **settings.toml** - 系统配置
```toml
[database]
host = "localhost"
port = 5432
database = "mushroom_db"
user = "your_user"
password = "your_password"

[llama]
api_url = "http://your-llama-api:8000/v1/chat/completions"
model = "llama-3.1-70b"
timeout = 600
temperature = 0.7
```

2. **static_config.json** - 设备配置（已存在于src/configs/）

3. **decision_prompt.jinja** - 决策模板（已存在于src/configs/）

## 🚀 快速开始

### 命令行使用

```bash
# 基本用法
python scripts/run_decision_analysis.py --room-id 611 --datetime "2024-01-15 10:00:00"

# 指定输出文件
python scripts/run_decision_analysis.py \
    --room-id 611 \
    --datetime "2024-01-15 10:00:00" \
    --output decision_output.json

# 使用当前时间
python scripts/run_decision_analysis.py --room-id 611
```

### Python API使用

```python
from datetime import datetime
from decision_analysis import DecisionAnalyzer
from utils.pgsql_engine import pgsql_engine
from configs.settings import settings
from configs.static_settings import static_settings

# 初始化决策分析器
analyzer = DecisionAnalyzer(
    db_engine=pgsql_engine,
    settings=settings,
    static_config=static_settings,
    template_path="src/configs/decision_prompt.jinja"
)

# 执行决策分析
result = analyzer.analyze(
    room_id="611",
    analysis_datetime=datetime(2024, 1, 15, 10, 0, 0)
)

# 查看结果
print(f"状态: {result.status}")
print(f"核心目标: {result.strategy.core_objective}")
print(f"冷风机温度设定: {result.device_recommendations.air_cooler.tem_set}°C")
```

## 💡 使用示例

### 示例1: 基本决策分析

```python
from datetime import datetime
from decision_analysis import DecisionAnalyzer
from utils.pgsql_engine import pgsql_engine
from configs.settings import settings
from configs.static_settings import static_settings

# 初始化
analyzer = DecisionAnalyzer(
    db_engine=pgsql_engine,
    settings=settings,
    static_config=static_settings,
    template_path="src/configs/decision_prompt.jinja"
)

# 分析611库房
result = analyzer.analyze(
    room_id="611",
    analysis_datetime=datetime.now()
)

# 输出调控策略
print("=" * 60)
print("调控总体策略")
print("=" * 60)
print(f"核心目标: {result.strategy.core_objective}")
print(f"优先级排序: {', '.join(result.strategy.priority_ranking)}")
print(f"关键风险点: {', '.join(result.strategy.key_risk_points)}")
```

### 示例2: 获取设备参数建议

```python
# 获取冷风机参数建议
air_cooler = result.device_recommendations.air_cooler
print("\n冷风机参数建议:")
print(f"  温度设定: {air_cooler.tem_set}°C")
print(f"  温差设定: {air_cooler.tem_diff_set}°C")
print(f"  循环模式: {'开启' if air_cooler.cyc_on_off else '关闭'}")
print(f"  判断依据:")
for rationale in air_cooler.rationale:
    print(f"    - {rationale}")

# 获取新风机参数建议
fresh_air = result.device_recommendations.fresh_air_fan
print("\n新风机参数建议:")
print(f"  模式: {['关闭', '自动', '手动'][fresh_air.model]}")
print(f"  控制方式: {['时控', 'CO2控制'][fresh_air.control]}")
print(f"  CO2启动阈值: {fresh_air.co2_on} ppm")
print(f"  CO2停止阈值: {fresh_air.co2_off} ppm")
```

### 示例3: 监控重点和元数据

```python
# 查看监控重点
monitoring = result.monitoring_points
print("\n24小时监控重点:")
print(f"关键时段: {', '.join(monitoring.key_time_periods)}")
print(f"预警阈值:")
for param, threshold in monitoring.warning_thresholds.items():
    print(f"  {param}: {threshold}")

# 查看元数据
metadata = result.metadata
print(f"\n决策元数据:")
print(f"  数据源: {metadata.data_sources}")
print(f"  相似案例数: {metadata.similar_cases_count}")
print(f"  平均相似度: {metadata.avg_similarity_score:.2f}%")
print(f"  LLM响应时间: {metadata.llm_response_time:.2f}秒")
print(f"  总处理时间: {metadata.total_processing_time:.2f}秒")
print(f"  警告数: {len(metadata.warnings)}")
print(f"  错误数: {len(metadata.errors)}")
```

### 示例4: 错误处理

```python
try:
    result = analyzer.analyze(
        room_id="611",
        analysis_datetime=datetime.now()
    )
    
    # 检查状态
    if result.status == "error":
        print("决策分析失败:")
        for error in result.metadata.errors:
            print(f"  - {error}")
    elif result.metadata.warnings:
        print("决策分析完成，但有警告:")
        for warning in result.metadata.warnings:
            print(f"  - {warning}")
    else:
        print("决策分析成功完成")
        
except Exception as e:
    print(f"发生异常: {e}")
```

### 示例5: 批量分析多个库房

```python
from datetime import datetime

rooms = ["607", "608", "611", "612"]
analysis_time = datetime.now()

results = {}
for room_id in rooms:
    try:
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_time
        )
        results[room_id] = result
        print(f"✓ 库房{room_id}分析完成")
    except Exception as e:
        print(f"✗ 库房{room_id}分析失败: {e}")

# 汇总结果
print("\n汇总结果:")
for room_id, result in results.items():
    print(f"库房{room_id}: {result.strategy.core_objective}")
```

## 📚 API文档

### DecisionAnalyzer

主控制器，协调整个决策分析流程。

#### 初始化

```python
DecisionAnalyzer(
    db_engine: Engine,
    settings: Dynaconf,
    static_config: Dict,
    template_path: str
)
```

**参数:**
- `db_engine`: SQLAlchemy数据库引擎
- `settings`: Dynaconf配置对象
- `static_config`: 静态配置字典
- `template_path`: decision_prompt.jinja模板路径

#### analyze方法

```python
analyze(
    room_id: str,
    analysis_datetime: datetime
) -> DecisionOutput
```

**参数:**
- `room_id`: 库房编号（607/608/611/612）
- `analysis_datetime`: 分析时间点

**返回:**
- `DecisionOutput`: 结构化的决策建议

**异常:**
- 不会抛出异常，所有错误都会被捕获并记录在metadata中

### DataExtractor

数据提取器，从数据库提取和预处理数据。

#### extract_current_embedding_data

```python
extract_current_embedding_data(
    room_id: str,
    target_datetime: datetime,
    time_window_days: int = 7,
    growth_day_window: int = 3
) -> pd.DataFrame
```

提取当前图像嵌入数据。

**参数:**
- `room_id`: 库房编号
- `target_datetime`: 目标时间
- `time_window_days`: 进库日期时间窗口（±天数）
- `growth_day_window`: 生长天数窗口（±天数）

**返回:**
- DataFrame包含embedding、env_sensor_status、设备配置等字段

#### extract_env_daily_stats

```python
extract_env_daily_stats(
    room_id: str,
    target_date: date,
    days_range: int = 1
) -> pd.DataFrame
```

提取环境每日统计数据。

#### extract_device_changes

```python
extract_device_changes(
    room_id: str,
    start_time: datetime,
    end_time: datetime,
    device_types: List[str] = None
) -> pd.DataFrame
```

提取设备变更记录。

### CLIPMatcher

CLIP相似度匹配器，基于向量相似度查找历史案例。

#### find_similar_cases

```python
find_similar_cases(
    query_embedding: np.ndarray,
    room_id: str,
    in_date: date,
    growth_day: int,
    top_k: int = 3,
    date_window_days: int = 7,
    growth_day_window: int = 3
) -> List[SimilarCase]
```

查找相似历史案例。

**参数:**
- `query_embedding`: 查询向量（512维）
- `room_id`: 库房编号
- `in_date`: 进库日期
- `growth_day`: 生长天数
- `top_k`: 返回Top-K个结果
- `date_window_days`: 进库日期窗口
- `growth_day_window`: 生长天数窗口

**返回:**
- List[SimilarCase]: 相似案例列表，包含相似度分数、置信度等

### TemplateRenderer

模板渲染器，将数据映射到Jinja2模板。

#### render

```python
render(
    current_data: Dict,
    env_stats: pd.DataFrame,
    device_changes: pd.DataFrame,
    similar_cases: List[SimilarCase]
) -> str
```

渲染决策提示模板。

**返回:**
- str: 渲染后的提示词文本

### LLMClient

大语言模型客户端，调用LLaMA API。

#### generate_decision

```python
generate_decision(
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = -1
) -> Dict
```

调用LLM生成决策建议。

**参数:**
- `prompt`: 决策提示词
- `temperature`: 温度参数（0.0-1.0）
- `max_tokens`: 最大token数（-1表示无限制）

**返回:**
- Dict: 解析后的决策建议

### OutputHandler

输出处理器，验证和格式化输出结果。

#### validate_and_format

```python
validate_and_format(
    raw_decision: Dict,
    room_id: str
) -> DecisionOutput
```

验证并格式化决策输出。

**参数:**
- `raw_decision`: LLM生成的原始决策
- `room_id`: 库房编号

**返回:**
- DecisionOutput: 验证并格式化后的决策

## ⚙️ 配置说明

### settings.toml配置项

```toml
[database]
host = "localhost"          # 数据库主机
port = 5432                 # 数据库端口
database = "mushroom_db"    # 数据库名称
user = "postgres"           # 数据库用户
password = "password"       # 数据库密码

[llama]
api_url = "http://localhost:8000/v1/chat/completions"  # LLaMA API地址
model = "llama-3.1-70b"     # 模型名称
timeout = 600               # 超时时间（秒）
temperature = 0.7           # 温度参数
max_tokens = -1             # 最大token数
```

### static_config.json结构

设备配置文件定义了所有设备的测点、枚举值和范围：

```json
{
  "air_cooler": {
    "device_name": "冷风机",
    "points": {
      "TemSet": {"range": [0, 40], "unit": "°C"},
      "TemDiffSet": {"range": [0, 10], "unit": "°C"},
      "CycOnOff": {"enum": {"0": "关闭", "1": "开启"}}
    }
  },
  "fresh_air_fan": {
    "device_name": "新风机",
    "points": {
      "Model": {"enum": {"0": "关闭", "1": "自动", "2": "手动"}},
      "Control": {"enum": {"0": "时控", "1": "CO2控制"}}
    }
  }
}
```

## 📊 数据模型

### 输入数据模型

#### CurrentStateData
当前状态数据，包含：
- 库房信息：room_id, in_date, in_num, growth_day
- 环境参数：temperature, humidity, co2
- 图像嵌入：embedding (512维向量)
- 设备配置：air_cooler_config, fresh_fan_config等

#### EnvStatsData
环境统计数据，包含：
- 温度统计：temp_median, temp_min, temp_max, temp_q25, temp_q75
- 湿度统计：humidity_median, humidity_min, humidity_max等
- CO2统计：co2_median, co2_min, co2_max等

#### DeviceChangeRecord
设备变更记录，包含：
- 设备信息：device_type, device_name, point_name
- 变更信息：change_time, previous_value, current_value
- 变更幅度：change_magnitude, change_type

#### SimilarCase
相似案例，包含：
- 相似度：similarity_score (0-100), confidence_level
- 环境参数：temperature, humidity, co2
- 设备配置：air_cooler_params, fresh_air_params等

### 输出数据模型

#### DecisionOutput
完整决策输出，包含：
- status: 状态（success/error）
- strategy: 调控总体策略
- device_recommendations: 设备参数建议
- monitoring_points: 监控重点
- metadata: 决策元数据

#### ControlStrategy
调控总体策略：
- core_objective: 核心目标
- priority_ranking: 优先级排序
- key_risk_points: 关键风险点

#### DeviceRecommendations
设备参数建议：
- air_cooler: 冷风机参数
- fresh_air_fan: 新风机参数
- humidifier: 加湿器参数
- grow_light: 补光灯参数

每个设备建议都包含：
- 具体参数值
- rationale: 判断依据列表

## 🔧 错误处理

### 错误分类

1. **数据库错误**
   - 连接失败：自动重试3次
   - 查询超时：返回部分数据
   - 查询结果为空：记录警告，继续执行

2. **数据验证错误**
   - 环境参数超出范围：记录警告，保留原值
   - 设备配置枚举值无效：使用默认值替换
   - 必需字段缺失：使用"数据缺失"标记

3. **CLIP匹配错误**
   - 未找到相似案例：使用基于规则的默认策略
   - 相似度分数过低：标记低置信度
   - 向量维度不匹配：跳过CLIP匹配

4. **LLM调用错误**
   - API不可用：使用基于规则的降级策略
   - 调用超时：终止请求，使用降级策略
   - 响应格式错误：尝试修正或使用默认策略

### 降级策略

系统采用三级降级策略：

**级别1: 部分功能降级**
- CLIP匹配失败 → 使用基于规则的策略
- 环境统计数据缺失 → 仅使用当前状态数据
- 设备变更记录缺失 → 不考虑历史变更趋势

**级别2: 简化输出**
- LLM调用失败 → 使用简单规则生成基本建议
- 模板渲染失败 → 使用纯文本格式输出
- 输出验证失败 → 返回未验证的原始输出并标记

**级别3: 最小可用**
- 所有数据源失败 → 返回错误状态和错误信息
- 配置文件缺失 → 使用硬编码的默认配置
- 数据库完全不可用 → 返回错误状态，建议人工介入

### 错误处理示例

```python
result = analyzer.analyze(room_id="611", analysis_datetime=datetime.now())

# 检查状态
if result.status == "error":
    print("严重错误，无法生成决策:")
    for error in result.metadata.errors:
        print(f"  ✗ {error}")
    # 建议人工介入
    
elif result.metadata.warnings:
    print("决策生成成功，但有以下警告:")
    for warning in result.metadata.warnings:
        print(f"  ⚠ {warning}")
    # 可以使用决策，但需注意警告
    
else:
    print("✓ 决策生成成功，无警告")
    # 可以安全使用决策
```

## ⚡ 性能优化

### 数据库优化

1. **使用索引**
   - idx_room_growth_day: 加速库房和生长天数查询
   - idx_in_date: 加速进库日期查询
   - idx_room_date: 加速环境统计查询
   - idx_room_change_time: 加速设备变更查询

2. **查询优化**
   - 使用WHERE子句预筛选数据
   - 限制返回字段，避免SELECT *
   - 使用LIMIT限制结果数量

3. **向量搜索优化**
   - 使用pgvector的HNSW索引
   - 先筛选后搜索，减少搜索空间
   - 限制Top-K数量（默认3）

### 性能指标

- **数据提取**: < 5秒
- **CLIP匹配**: < 3秒
- **模板渲染**: < 1秒
- **LLM调用**: 10-60秒（取决于模型）
- **输出验证**: < 1秒
- **总处理时间**: < 35秒（不含LLM调用）

### 性能监控

```python
result = analyzer.analyze(room_id="611", analysis_datetime=datetime.now())

# 查看性能指标
print(f"LLM响应时间: {result.metadata.llm_response_time:.2f}秒")
print(f"总处理时间: {result.metadata.total_processing_time:.2f}秒")
print(f"数据源记录数: {result.metadata.data_sources}")
```

## ❓ 常见问题

### Q1: 如何处理"No embedding data found"错误？

**原因**: 数据库中没有找到指定库房和时间的图像嵌入数据。

**解决方案**:
1. 检查库房编号是否正确（607/608/611/612）
2. 检查时间范围是否有数据
3. 扩大时间窗口：`time_window_days=14`
4. 检查数据库连接是否正常

### Q2: CLIP匹配返回低置信度案例怎么办？

**原因**: 历史数据中没有非常相似的案例。

**解决方案**:
1. 系统会自动标记低置信度（<20%）
2. 决策仍然可用，但建议人工审核
3. 可以扩大筛选范围：`date_window_days=14, growth_day_window=5`

### Q3: LLM调用超时怎么办？

**原因**: LLM API响应时间过长或网络问题。

**解决方案**:
1. 增加超时时间：在settings.toml中设置`timeout = 900`
2. 检查网络连接
3. 系统会自动使用降级策略生成基本建议

### Q4: 如何自定义决策模板？

**步骤**:
1. 编辑`src/configs/decision_prompt.jinja`
2. 使用Jinja2语法添加变量：`{{ variable_name }}`
3. 在TemplateRenderer中添加变量映射
4. 重启系统

### Q5: 如何添加新的设备类型？

**步骤**:
1. 在`static_config.json`中添加设备配置
2. 在`data_models.py`中添加设备推荐数据类
3. 在`OutputHandler`中添加验证逻辑
4. 在`TemplateRenderer`中添加变量映射

### Q6: 如何查看详细日志？

**方法**:
```python
from loguru import logger

# 设置日志级别
logger.remove()
logger.add("decision_analysis.log", level="DEBUG")

# 执行分析
result = analyzer.analyze(room_id="611", analysis_datetime=datetime.now())
```

日志文件会包含所有操作的详细信息。

### Q7: 如何批量处理多个时间点？

**示例**:
```python
from datetime import datetime, timedelta

# 生成时间序列
start_time = datetime(2024, 1, 1, 0, 0, 0)
time_points = [start_time + timedelta(hours=i) for i in range(24)]

# 批量分析
results = []
for time_point in time_points:
    result = analyzer.analyze(
        room_id="611",
        analysis_datetime=time_point
    )
    results.append(result)

# 保存结果
import json
with open("batch_results.json", "w") as f:
    json.dump([r.__dict__ for r in results], f, default=str, indent=2)
```

### Q8: 如何优化查询性能？

**建议**:
1. 确保数据库索引已创建
2. 减小时间窗口和生长天数窗口
3. 限制Top-K数量
4. 使用连接池管理数据库连接
5. 定期清理旧数据

### Q9: 如何处理多个库房的并发分析？

**示例**:
```python
from concurrent.futures import ThreadPoolExecutor

def analyze_room(room_id):
    return analyzer.analyze(
        room_id=room_id,
        analysis_datetime=datetime.now()
    )

# 并发分析
rooms = ["607", "608", "611", "612"]
with ThreadPoolExecutor(max_workers=4) as executor:
    results = list(executor.map(analyze_room, rooms))

# 处理结果
for room_id, result in zip(rooms, results):
    print(f"库房{room_id}: {result.status}")
```

### Q10: 如何集成到现有系统？

**步骤**:
1. 安装依赖：`pip install -r requirements.txt`
2. 配置数据库连接和LLM API
3. 导入模块：`from decision_analysis import DecisionAnalyzer`
4. 初始化并调用：参考快速开始章节
5. 处理返回的DecisionOutput对象

## 📞 获取帮助

如果遇到问题：

1. **查看文档**: 首先查看本README和API文档
2. **查看日志**: 检查详细的日志信息
3. **运行测试**: 使用测试脚本验证功能
4. **查看示例**: 参考examples/decision_analysis_example.py

## 📄 许可证

本模块是蘑菇种植智能调控系统的一部分。

---

**版本**: 0.1.0  
**最后更新**: 2024-01  
**维护者**: 蘑菇系统开发团队

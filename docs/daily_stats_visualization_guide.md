# 基于 MushroomEnvDailyStats 的环境数据可视化指南

## 概述

本指南介绍如何使用 `src/utils/daily_stats_visualization.py` 模块从 `MushroomEnvDailyStats` 表中读取最新批次的温湿度数据并进行可视化展示。该模块参考了 `src/utils/visualization.py` 的设计模式，提供了类似的 Violin 图可视化功能。

## 功能特性

### 核心功能

1. **最新批次数据查询**: 从数据库中智能查询最新批次的环境统计数据
2. **Violin 分布图**: 基于统计值模拟生成每日环境数据的分布特征
3. **中位数趋势线**: 显示温度、湿度、CO2 的中位数变化趋势
4. **生长阶段标注**: 通过 `is_growth_phase` 和 `in_day_num` 字段标注生长阶段
5. **批次信息展示**: 在图表中标注批次信息和生长天数
6. **多库房对比**: 生成多库房环境参数变化趋势对比图表

### 技术特点

- **统一配色方案**: 与原 `visualization.py` 保持一致的颜色配置
- **1200px 高度**: 符合用户要求的图表高度设置
- **响应式布局**: 自适应的图表布局和标注系统
- **数据质量检查**: 内置数据完整性验证和错误处理

## 模块结构

### 主要函数

#### 1. `get_latest_batch_data()`
```python
def get_latest_batch_data(
    rooms: Optional[List[str]] = None,
    days_back: int = 30,
    min_records_per_room: int = 5
) -> pd.DataFrame
```

**功能**: 从 MushroomEnvDailyStats 表中查询最新批次数据
- `rooms`: 库房列表，None 表示查询所有库房
- `days_back`: 向前查询天数，默认30天
- `min_records_per_room`: 每个库房最少记录数过滤

#### 2. `plot_room_daily_stats_violin()`
```python
def plot_room_daily_stats_violin(
    df: pd.DataFrame, 
    room_id: str, 
    show: bool = True
) -> go.Figure
```

**功能**: 为单个库房生成环境统计的 Violin 图
- 基于统计值（min, q25, median, q75, max）模拟分布数据
- 包含温度、湿度、CO2 三个子图
- 添加中位数趋势线和生长阶段背景

#### 3. `plot_multi_room_comparison()`
```python
def plot_multi_room_comparison(
    df: pd.DataFrame,
    rooms: Optional[List[str]] = None,
    show: bool = True
) -> Dict[str, go.Figure]
```

**功能**: 生成多库房对比可视化图表
- 温度、湿度、CO2 中位数趋势对比
- 生长阶段分布热力图
- 环境参数变异性对比（基于四分位距）

#### 4. `analyze_and_visualize_latest_batch()`
```python
def analyze_and_visualize_latest_batch(
    rooms: Optional[List[str]] = None,
    days_back: int = 30,
    show_individual: bool = True,
    show_comparison: bool = True,
    return_figs: bool = False
) -> Optional[Dict[str, Any]]
```

**功能**: 完整的分析和可视化流程
- 自动查询数据、生成图表、提供数据摘要
- 支持单库房详细分析和多库房对比
- 返回完整的分析结果和图表对象

## 数据结构说明

### MushroomEnvDailyStats 表字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `room_id` | String | 库房编号（如 '611'） |
| `stat_date` | Date | 统计日期 |
| `in_day_num` | Integer | 入库天数 |
| `is_growth_phase` | Boolean | 是否为生长阶段 |
| `temp_median/min/max/q25/q75` | Float | 温度统计值 |
| `humidity_median/min/max/q25/q75` | Float | 湿度统计值 |
| `co2_median/min/max/q25/q75` | Float | CO2统计值 |
| `batch_date` | Date | 批次日期 |

### 分布数据模拟

由于 `MushroomEnvDailyStats` 存储的是每日统计值而非原始分布数据，系统使用以下方法模拟分布：

1. **统计值插值**: 在 min-q25-median-q75-max 之间生成插值点
2. **权重分配**: 中位数附近生成更多数据点，模拟正态分布特征
3. **Violin 图渲染**: 使用模拟数据点生成平滑的分布形状

## 使用方法

### 1. 基础使用

```python
from utils.daily_stats_visualization import analyze_and_visualize_latest_batch

# 分析所有库房的最新批次数据
results = analyze_and_visualize_latest_batch(
    rooms=None,  # 所有库房
    days_back=30,  # 最近30天
    show_individual=True,  # 显示单库房图表
    show_comparison=True   # 显示对比图表
)
```

### 2. 指定库房分析

```python
# 分析特定库房
results = analyze_and_visualize_latest_batch(
    rooms=['611', '612', '607'],
    days_back=45,
    show_individual=True,
    show_comparison=True
)
```

### 3. 单库房详细分析

```python
from utils.daily_stats_visualization import (
    get_latest_batch_by_room,
    plot_room_daily_stats_violin
)

# 获取单个库房数据
room_data = get_latest_batch_by_room('611', max_days_back=60)

# 生成详细图表
fig = plot_room_daily_stats_violin(room_data, '611', show=True)
```

### 4. 多库房对比

```python
from utils.daily_stats_visualization import (
    get_latest_batch_data,
    plot_multi_room_comparison
)

# 获取多库房数据
df = get_latest_batch_data(rooms=['611', '612'], days_back=30)

# 生成对比图表
comparison_figs = plot_multi_room_comparison(df, show=True)
```

## 脚本使用

### 命令行执行

```bash
# 执行完整的可视化分析
python scripts/visualize_latest_batch.py

# 或者直接运行模块
python -m utils.daily_stats_visualization
```

### 脚本功能

`scripts/visualize_latest_batch.py` 提供三种分析模式：

1. **完整分析**: 分析所有库房的最新批次数据
2. **指定库房分析**: 针对特定库房进行详细分析
3. **数据质量检查**: 检查数据完整性和统计信息

## 图表类型说明

### 1. 单库房详细图表

**结构**: 3行1列子图布局
- **第1行**: 温度分布 Violin 图 + 中位数趋势线
- **第2行**: 湿度分布 Violin 图 + 中位数趋势线  
- **第3行**: CO2分布 Violin 图 + 中位数趋势线

**特殊标注**:
- 生长阶段背景（绿色半透明区域）
- 批次信息标注（彩色垂直区域）
- 生长天数信息（Day 1-27 等）

### 2. 多库房对比图表

包含以下图表类型：

#### a) 趋势对比图
- 温度中位数趋势对比
- 湿度中位数趋势对比
- CO2中位数趋势对比

#### b) 生长阶段热力图
- 横轴：日期
- 纵轴：库房
- 颜色：生长阶段状态

#### c) 变异性对比图
- 基于四分位距（IQR）的环境参数稳定性分析
- 3行1列布局：温度/湿度/CO2变异性

## 配色方案

### 环境参数颜色

| 参数 | Violin图颜色 | 填充颜色 | 趋势线颜色 |
|------|-------------|----------|-----------|
| 温度 | #3366CC | rgba(51, 102, 204, 0.2) | #3366CC |
| 湿度 | #00CC96 | rgba(0, 204, 150, 0.2) | #00CC96 |
| CO2 | #9467BD | rgba(148, 103, 189, 0.2) | #9467BD |

### 阶段标注颜色

| 阶段 | 背景颜色 | 透明度 | 说明 |
|------|----------|--------|------|
| 生长期 | #E6F7E6 | 0.25 | 绿色背景 |
| 非生长期 | #FAFAFA | 0.08 | 浅灰背景 |
| 批次标注 | Plotly调色板 | 0.12 | 彩色垂直区域 |

## 性能优化

### 数据查询优化

1. **索引利用**: 利用表上的复合索引 `idx_room_date`
2. **时间范围限制**: 通过 `days_back` 参数限制查询范围
3. **数据过滤**: 过滤记录数不足的库房，避免无效图表

### 图表渲染优化

1. **分布模拟**: 智能生成适量的模拟数据点，平衡效果和性能
2. **批量处理**: 一次查询多库房数据，减少数据库访问
3. **条件渲染**: 根据数据可用性动态添加图表元素

## 错误处理

### 数据层面

- **空数据检查**: 查询结果为空时的优雅处理
- **字段缺失**: 处理表结构变化或字段缺失情况
- **数据类型**: 自动转换日期类型和数值类型

### 图表层面

- **渲染失败**: 单个图表失败不影响其他图表生成
- **标注异常**: 生长阶段或批次标注失败时的降级处理
- **布局适配**: 不同数据量下的自适应布局

## 扩展功能

### 1. 自定义时间范围

```python
# 指定具体的时间范围
from datetime import date, timedelta

end_date = date.today()
start_date = end_date - timedelta(days=60)

# 可以扩展函数支持具体日期范围
```

### 2. 导出功能

```python
# 保存图表为文件
fig = plot_room_daily_stats_violin(room_data, '611', show=False)
fig.write_html("room_611_stats.html")
fig.write_image("room_611_stats.png", width=1200, height=1200)
```

### 3. 数据导出

```python
# 导出分析数据
results = analyze_and_visualize_latest_batch(return_figs=True)
df = results['data']
df.to_csv('latest_batch_stats.csv', index=False)
```

## 与原系统对比

### 相似之处

1. **图表风格**: 保持与 `visualization.py` 一致的 Violin 图风格
2. **颜色方案**: 使用相同的环境参数颜色配置
3. **布局结构**: 类似的多子图布局和标注系统
4. **交互功能**: 支持 Plotly 的缩放、悬停等交互功能

### 主要差异

1. **数据源**: 使用预计算的统计表而非实时IoT数据
2. **分布模拟**: 基于统计值模拟分布而非原始数据分布
3. **查询效率**: 直接查询统计表，性能更优
4. **批次识别**: 更准确的批次信息和生长阶段判断

## 最佳实践

### 1. 数据查询

- 合理设置 `days_back` 参数，避免查询过多历史数据
- 使用 `min_records_per_room` 过滤数据不足的库房
- 定期检查数据完整性，确保统计表数据及时更新

### 2. 图表生成

- 对于大量库房，考虑分批生成图表
- 使用 `return_figs=True` 获取图表对象进行后续处理
- 根据实际需求选择显示单库房图表或对比图表

### 3. 性能监控

- 监控数据库查询时间，必要时优化索引
- 关注图表渲染性能，特别是多库房对比场景
- 定期清理过期的统计数据，保持表大小合理

## 故障排查

### 常见问题

1. **数据为空**: 检查 `MushroomEnvDailyStats` 表是否有数据
2. **图表不显示**: 确认 Plotly 环境配置正确
3. **批次信息缺失**: 检查 `batch_date` 字段的数据质量
4. **生长阶段异常**: 验证 `is_growth_phase` 和 `in_day_num` 字段

### 调试方法

```python
# 启用详细日志
from utils.loguru_setting import loguru_setting
loguru_setting()

# 检查数据质量
df = get_latest_batch_data(days_back=60)
print(df.info())
print(df.describe())

# 验证特定库房数据
room_data = get_latest_batch_by_room('611')
print(f"库房611数据: {len(room_data)} 条记录")
```

---

本可视化系统提供了完整的环境数据分析和展示功能，支持从数据查询到图表生成的全流程操作，是蘑菇生长环境监控系统的重要组成部分。
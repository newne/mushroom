# 蘑菇种植智能调控系统 - 项目结构

## 项目概述

本项目是一个基于 AI 的蘑菇种植环境智能调控系统，集成了 CLIP 图像识别、LLM 决策生成、环境监控和设备控制等功能。

## 目录结构

```
mushroom_solution/
├── README.md                      # 项目说明文档
├── requirements.txt               # Python 依赖
├── pyproject.toml                # 项目配置
├── uv.lock                       # 依赖锁定文件
├── scheduler.py                  # 主调度器入口
│
├── src/                          # 核心源代码
│   ├── main.py                   # 主程序入口
│   ├── streamlit_app.py          # Streamlit Web 应用
│   │
│   ├── clip/                     # CLIP 模型相关
│   │   ├── clip_app.py          # CLIP 应用
│   │   ├── clip_inference_scheduler.py  # CLIP 推理调度
│   │   ├── clip_inference.py    # CLIP 推理核心
│   │   └── get_env_status.py    # 环境状态获取
│   │
│   ├── decision_analysis/        # 决策分析模块
│   │   ├── __init__.py
│   │   ├── README.md            # 模块说明
│   │   ├── decision_analyzer.py # 决策分析器
│   │   ├── data_extractor.py    # 数据提取器
│   │   ├── clip_matcher.py      # CLIP 匹配器
│   │   ├── template_renderer.py # 模板渲染器
│   │   ├── llm_client.py        # LLM 客户端
│   │   ├── output_handler.py    # 输出处理器
│   │   └── data_models.py       # 数据模型
│   │
│   ├── scheduling/               # 调度系统
│   │   ├── optimized_scheduler.py  # 优化的调度器
│   │   └── add_scheduler_job_legacy.py  # 遗留调度任务
│   │
│   ├── utils/                    # 工具模块
│   │   ├── create_table.py      # 数据库表创建
│   │   ├── get_data.py          # 数据获取
│   │   ├── send_request.py      # 请求发送
│   │   ├── loguru_setting.py    # 日志配置
│   │   ├── exception_listener.py # 异常监听
│   │   ├── minio_client.py      # MinIO 客户端
│   │   ├── minio_service.py     # MinIO 服务
│   │   ├── mushroom_image_encoder.py  # 图像编码器
│   │   ├── mushroom_image_processor.py  # 图像处理器
│   │   ├── recent_image_processor.py  # 最近图像处理
│   │   ├── data_preprocessing.py  # 数据预处理
│   │   ├── dataframe_utils.py   # DataFrame 工具
│   │   ├── env_data_processor.py  # 环境数据处理
│   │   ├── daily_stats_visualization.py  # 日统计可视化
│   │   ├── visualization.py     # 可视化工具
│   │   ├── setpoint_analytics.py  # 设定点分析
│   │   ├── setpoint_change_monitor.py  # 设定点变化监控
│   │   └── setpoint_config.py   # 设定点配置
│   │
│   ├── global_const/             # 全局常量
│   │   ├── const_config.py      # 常量配置
│   │   └── global_const.py      # 全局常量定义
│   │
│   ├── configs/                  # 配置文件
│   │   ├── settings.toml        # 主配置文件
│   │   ├── .secrets.toml        # 敏感配置（不提交）
│   │   ├── static_config.json   # 静态配置
│   │   ├── decision_prompt.jinja  # 决策提示模板
│   │   ├── setpoint_monitor_config.json  # 设定点监控配置
│   │   └── monitoring_points_config.json  # 监控点配置
│   │
│   └── Logs/                     # 日志文件
│       ├── mushroom_solution-critical.log
│       ├── mushroom_solution-error.log
│       ├── mushroom_solution-warning.log
│       ├── mushroom_solution-info.log
│       └── mushroom_solution-debug.log
│
├── scripts/                      # 生产脚本
│   ├── README.md                # 脚本说明
│   ├── mushroom_cli.py          # 命令行工具
│   │
│   ├── analysis/                # 分析脚本
│   │   ├── run_decision_analysis.py  # 运行决策分析
│   │   ├── run_env_stats.py     # 运行环境统计
│   │   └── run_visualization.py # 运行可视化
│   │
│   ├── processing/              # 处理脚本
│   │   ├── process_recent_images.py  # 处理最近图像
│   │   ├── process_recent_hour_images.py  # 处理最近一小时图像
│   │   ├── compute_historical_env_stats.py  # 计算历史统计
│   │   ├── extract_monitoring_point_configs.py  # 提取监控点配置
│   │   └── visualize_latest_batch.py  # 可视化最新批次
│   │
│   ├── monitoring/              # 监控脚本
│   │   ├── monitor_setpoint_changes.py  # 监控设定点变化
│   │   ├── batch_setpoint_monitoring.py  # 批量监控
│   │   └── setpoint_demo.py     # 设定点演示
│   │
│   ├── maintenance/             # 维护脚本
│   │   ├── cache_manager.py     # 缓存管理
│   │   ├── check_data_source.py # 检查数据源
│   │   ├── check_december_data.py  # 检查12月数据
│   │   ├── check_embedding_data.py  # 检查嵌入数据
│   │   └── check_env_stats.py   # 检查环境统计
│   │
│   └── migration/               # 迁移脚本
│       ├── migrate_mushroom_embedding_table.py  # 迁移嵌入表
│       ├── add_image_quality_index.py  # 添加图像质量索引
│       └── add_llama_description_fields.py  # 添加描述字段
│
├── tests/                        # 测试代码
│   ├── README.md                # 测试说明
│   │
│   ├── unit/                    # 单元测试
│   │   ├── test_clip_matcher.py
│   │   ├── test_llm_client.py
│   │   ├── test_template_renderer.py
│   │   ├── test_output_handler.py
│   │   └── test_decision_analyzer.py
│   │
│   ├── integration/             # 集成测试
│   │   ├── test_validate_env_params_integration.py
│   │   ├── test_json_format_integration.py
│   │   └── verify_system_integration.py
│   │
│   ├── performance/             # 性能测试
│   │   ├── test_query_performance.py
│   │   └── verify_task9_performance.py
│   │
│   ├── functional/              # 功能测试
│   │   ├── test_extract_device_changes.py
│   │   ├── test_extract_embedding_data.py
│   │   ├── test_extract_env_daily_stats.py
│   │   ├── test_find_similar_cases.py
│   │   ├── test_get_data_prompt.py
│   │   ├── test_validate_env_params.py
│   │   ├── test_llama_json_format.py
│   │   └── test_llama_json_with_real_images.py
│   │
│   ├── debug/                   # 调试脚本
│   │   ├── debug_llm_json_parse.py
│   │   └── debug_prompt_config.py
│   │
│   └── verification/            # 验证脚本
│       └── verify_task_3_2.py
│
├── docs/                         # 文档
│   ├── README.md                # 文档说明
│   │
│   ├── development/             # 开发文档
│   │   ├── tasks/              # 任务实现文档
│   │   ├── fixes/              # 问题修复文档
│   │   └── optimizations/      # 优化文档
│   │
│   ├── deployment/              # 部署文档
│   │   ├── DEPLOYMENT_GUIDE.md
│   │   ├── DEPLOYMENT_CHECKLIST.md
│   │   └── QUICK_DEPLOY_REFERENCE.md
│   │
│   ├── guides/                  # 使用指南
│   │   ├── LOG_VIEWING_GUIDE.md
│   │   ├── mushroom_processing_guide.md
│   │   ├── setpoint_monitoring_guide.md
│   │   └── minio_setup_guide.md
│   │
│   ├── system_overview.md       # 系统概览
│   ├── system_architecture_flowchart.md  # 架构流程图
│   ├── CLIP_INFERENCE_AND_GLOBAL_WORKFLOW.md  # CLIP 工作流
│   ├── prompt_api_integration_guide.md  # API 集成指南
│   ├── device_monitoring_points_reference.md  # 监控点参考
│   └── archive/                 # 归档文档
│
├── examples/                     # 示例代码
│   ├── README.md                # 示例说明
│   ├── decision_analysis_example.py  # 决策分析示例
│   ├── mushroom_processing_example.py  # 图像处理示例
│   ├── prompt_api_usage_example.py  # API 使用示例
│   ├── minio_example.py         # MinIO 示例
│   └── integrated_minio_example.py  # 集成 MinIO 示例
│
├── notebooks/                    # Jupyter 笔记本
│   ├── README.md                # 笔记本说明
│   ├── clip_test.ipynb          # CLIP 测试
│   ├── env_params_eda.ipynb     # 环境参数探索性分析
│   └── daily_env_stats.html     # 日统计可视化
│
├── docker/                       # Docker 相关
│   ├── README.md                # Docker 说明
│   ├── Dockerfile               # Docker 镜像定义
│   ├── mushroom_solution.yml    # Docker Compose 配置
│   ├── .env                     # 环境变量
│   ├── build.sh                 # 构建脚本
│   ├── run.sh                   # 运行脚本
│   ├── compose.sh               # Compose 脚本
│   ├── deploy_server.sh         # 部署脚本
│   └── secrets/                 # 密钥文件
│
├── models/                       # 模型文件
│   ├── README.md                # 模型说明
│   └── clip-vit-base-patch32/   # CLIP 模型
│
├── data/                         # 数据文件
│   ├── 611_20251219-20260106.csv  # 示例数据
│   └── m1.jpg                   # 示例图像
│
├── output/                       # 输出文件（临时）
│   └── decision_analysis_*.json # 决策分析输出
│
└── .archive/                     # 归档文件（临时）
    └── test_rendered_prompt.txt # 归档的测试文件
```

## 核心模块说明

### 1. 决策分析模块 (src/decision_analysis/)
负责基于历史数据和 CLIP 图像匹配生成环境调控决策。

**主要组件：**
- DecisionAnalyzer: 决策分析主控制器
- DataExtractor: 从数据库提取相关数据
- CLIPMatcher: 使用 CLIP 查找相似历史案例
- TemplateRenderer: 渲染决策提示模板
- LLMClient: 调用 LLM 生成决策
- OutputHandler: 验证和格式化输出

### 2. CLIP 模块 (src/clip/)
负责图像识别和环境状态评估。

**主要组件：**
- clip_inference.py: CLIP 推理核心
- clip_inference_scheduler.py: CLIP 推理调度
- get_env_status.py: 环境状态获取

### 3. 调度系统 (src/scheduling/)
负责定时任务调度和执行。

**主要组件：**
- optimized_scheduler.py: 优化的调度器
- 支持定时任务、延迟任务和周期任务

### 4. 工具模块 (src/utils/)
提供各种工具函数和服务。

**主要组件：**
- 数据库操作
- MinIO 对象存储
- 图像处理
- 数据可视化
- 设定点监控

## 数据流程

```
1. 图像采集 → MinIO 存储
2. CLIP 推理 → 图像特征提取 → 数据库存储
3. 环境数据采集 → 数据库存储
4. 决策分析：
   - 提取当前环境数据
   - CLIP 匹配相似历史案例
   - 渲染决策提示
   - LLM 生成决策建议
   - 验证和格式化输出
5. 设备控制 → 执行调控决策
```

## 配置管理

### 主配置文件 (src/configs/settings.toml)
- 数据库连接
- MinIO 配置
- LLM 配置
- 调度器配置

### 敏感配置 (src/configs/.secrets.toml)
- 数据库密码
- API 密钥
- 其他敏感信息

### 静态配置 (src/configs/static_config.json)
- 设备点位定义
- 枚举值映射
- 设备参数范围

## 日志系统

日志文件位于 `src/Logs/` 目录，按级别分类：
- critical.log: 严重错误
- error.log: 错误信息
- warning.log: 警告信息
- info.log: 一般信息
- debug.log: 调试信息

## 测试策略

- **单元测试**: 测试单个模块功能
- **集成测试**: 测试模块间集成
- **性能测试**: 测试系统性能
- **功能测试**: 测试完整功能流程

## 部署方式

### Docker 部署
```bash
cd docker
./build.sh
./compose.sh up -d
```

### 本地开发
```bash
# 安装依赖
pip install -r requirements.txt

# 运行调度器
python scheduler.py

# 运行 Web 应用
streamlit run src/streamlit_app.py
```

## 维护指南

### 日常维护
- 检查日志文件
- 监控系统性能
- 清理临时文件
- 备份数据库

### 定期任务
- 更新依赖包
- 优化数据库
- 清理旧数据
- 更新文档

## 开发规范

### 代码规范
- 使用 Python 3.10+
- 遵循 PEP 8 代码风格
- 添加类型注解
- 编写文档字符串

### 提交规范
- 清晰的提交信息
- 小而专注的提交
- 提交前运行测试
- 更新相关文档

### 文档规范
- 使用 Markdown 格式
- 保持文档更新
- 添加代码示例
- 记录变更历史

## 获取帮助

- 查看 docs/ 目录下的文档
- 查看各模块的 README.md
- 查看示例代码 examples/
- 联系开发团队

## 版本历史

- v1.0.0: 初始版本
- v1.1.0: 添加决策分析模块
- v1.2.0: 优化 CLIP 推理性能
- v1.3.0: 添加设定点监控
- v1.4.0: 项目结构重组和清理

## 许可证

[项目许可证信息]

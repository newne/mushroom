<!-- Copilot / AI agent 指南（为代码助理量身定制，简洁、可操作） -->
# 快速目标

- 帮助 AI 代码助理快速定位本仓库的「意图、约定、运行与测试」要点。
- 与用户交互时使用中文回复。

# 快速命令 (Cheatsheet)

请在项目根目录运行以下命令：

- **环境安装**: `uv sync` (使用 `uv` 管理依赖，注意 `pyproject.toml` 中的 `cpu`/`cu129` extra)。
- **启动服务**: `python src/main.py` (启动 API 服务并自动运行后台调度器)。
- **CLI 工具**:
  - 健康检查: `python src/scripts/mushroom_cli.py health`
  - 列出图片: `python src/scripts/mushroom_cli.py list --mushroom-id 611`
  - 批量处理: `python src/scripts/mushroom_cli.py process --mushroom-id 611`
  - 路径验证: `python src/scripts/mushroom_cli.py validate -p <path>`
- **测试**:
  - 运行所有单元测试: `pytest tests/unit`
  - 运行集成测试: `pytest tests/integration`

# 核心架构 (Big Picture)

- **核心服务**: `src/main.py` (FastAPI Server + 调度器) 是常驻入口。
- **任务层 (Tasks)**: 系统核心由定时任务驱动，位于 `src/tasks/`。逻辑封装在 `safe_*` 函数中，调用各领域模块。
- **领域层**: `src/decision_analysis/` (LLM决策), `src/vision/` (CLIP处理), `src/monitoring/` (监控)。
- **存储层**: MinIO（对象存储）用于图片，Postgres(+pgvector)用于向量与元数据。配置在 `src/utils/`。
- **CLI**: `src/scripts/mushroom_cli.py` 提供开发维度的命令行工具。

# 项目约定与可复用模式（务必遵循）

- 工厂函数: 创建共享实例的命名约定为 `create_*`（示例: `create_mushroom_encoder`, `create_minio_service`, `create_mushroom_processor`）。AI 修改时应保持该契约，避免出现多个全局隐式单例。
- 路径与文件名模式: MinIO 对象路径遵循 `mogu/{mushroom_id}/{YYYYMMDD}/{filename}`，文件名模式通常为 `{mushroom}_{ip}_{collection_date}_{detailed_time}.jpg`。解析器类位于 `src/utils`（示例: `MushroomImagePathParser`），测试用例也依赖这一格式，修改需保持向后兼容。
- 批处理/统计返回值: 批量/编码函数返回字典结构（包含 `total`, `success`, `failed`, `skipped` 等键）；可按此约定写断言与日志。
- 环境切换: 通过环境变量 `prod` 或配置文件控制（见 `tests` 中的环境切换示例），AI 应尊重代码中对环境的读取方式。
- 决策分析输出: 结构化JSON格式，包含设备参数调整建议，必须符合 `configs/static_config.json` 中的设备规范。

# 关键文件与定位示例（快速打开）
核心服务入口: [src/main.py](src/main.py)
- CLI 工具: [src/scripts/mushroom_cli.py](src/
- 主入口（CLI）: [scripts/mushroom_cli.py](scripts/mushroom_cli.py)
- 决策分析核心: [src/decision_analysis/decision_analyzer.py](src/decision_analysis/decision_analyzer.py)
- 文档索引: [docs/README.md](docs/README.md) 与仓库根 [README.md](README.md)
- 依赖与安装: [pyproject.toml](pyproject.toml) （注意 `uv` 源配置与 extras: `cpu` / `cu129`）
- CLIP 模型目录: [models/clip-vit-base-patch32/](models/clip-vit-base-patch32/)
- Docker部署: [docker/mushroom_solution.yml](docker/mushroom_solution.yml)
- 测试目录: [tests/](tests/) （包含 unit/, integration/, functional/ 等子目录）

# 与外部系统的集成点（重要）

- MinIO: 通过 `src/utils/minio_service` / `src/utils/minio_client` 进行封装与健康检查。MinIO 的端点、桶名等来自配置/环境，修改或 mock 时优先使用 `create_minio_service()`。
- 数据库: 代码假设存在 PostgreSQL + pgvector（未在仓库内包含迁移脚本），任何对数据库模型的修改需同步测试脚本中的统计/断言逻辑。表结构定义在 `src/utils/create_table.py`。
- 模型文件: 本地 `models/` 中包含模型权重/配置，推理代码期望这些文件已就位（CLIP、LLaMA 相关）。
- LLM API: 决策分析模块调用外部 LLaMA API（配置在 `configs/settings.toml`），用于生成调控建议。

# 常见开发流程与调试技巧
rc/scripts/mushroom_cli.py health`
- 本地 MinIO 测试: 运行集成测试 `pytest tests/integration`
- 批量处理示例: `python src/scripts/mushroom_cli.py process --mushroom-id 611 --date 20260119`
- 决策分析测试: `pytest tests/unit/test_decision_analyzer --mushroom-id 611 --date 20260119`
- 决策分析测试: `python test_enhanced_decision_analysis.py`
- Docker部署: `cd docker && docker-compose -f mushroom_solution.yml up -d`

# 修改与 PR 指南（针对 AI 自动改动）

- 保持 `create_*` 工厂契约：若新增构造参数，应向下游传入兼容参数，并在调用处优先传入共享实例。
- 不要改变 `MushroomImagePathParser` 的外部行为；若扩展，新增方法并保持原 API。
- 修改批处理返回结构需同步更新 `scripts/` 与 `tests/` 中的断言与报告逻辑。
- 决策分析输出格式必须向后兼容，设备参数需验证符合 `configs/static_config.json`。
- 新增功能应添加相应测试到 `tests/` 目录的适当子目录。

# 还需要的信息？

如果你需要我补充数据库表字段、配置键的完整列表，或把某个模块（例如 `decision_analyzer.py`）拆解成更小的工厂/接口实现说明，请告诉我要优先详化的目标。

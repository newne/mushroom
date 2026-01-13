<!-- Copilot / AI agent 指南（为代码助理量身定制，简洁、可操作） -->
# 快速目标

- 帮助 AI 代码助理快速定位本仓库的「意图、约定、运行与测试」要点。

# 快速命令（最常用）

- 安装依赖: `uv sync` （项目使用 `uv` 配置多个 torch 源，见 `pyproject.toml`）
- 常用 CLI:
  - `python main.py recent --hours 1`
  - `python main.py batch-all`
  - `python main.py validate --max-per-room 3`
  - `python scripts/mushroom_cli.py --help`
- 测试脚本（独立可运行脚本，非 pytest）：
  - `python test_mushroom_system.py`
  - `python test_minio_setup.py`

# 大体架构 / 为什么这样组织

- 存储层: MinIO（对象存储）用于图片，Postgres(+pgvector)用于向量与元数据。配置和初始化逻辑散布在 `utils/` 中，MinIO 相关入口在 `utils/minio_*` 文件。
- 处理层: CLIP 用于向量化（`models/clip-*`），LLaMA 用于文本描述。两者被封装成共享/可复用的“工厂”对象（`create_*` 风格）。
- 应用层: `main.py` 提供一套 CLI 命令，`scripts/` 放更便捷的脚本接口，`examples/` 包示例用法。

# 项目约定与可复用模式（务必遵循）

- 工厂函数: 创建共享实例的命名约定为 `create_*`（示例: `create_mushroom_encoder`, `create_minio_service`, `create_mushroom_processor`）。AI 修改时应保持该契约，避免出现多个全局隐式单例。
- 路径与文件名模式: MinIO 对象路径遵循 `mogu/{mushroom_id}/{YYYYMMDD}/{filename}`，文件名模式通常为 `{mushroom}_{ip}_{collection_date}_{detailed_time}.jpg`。解析器类位于 `src/utils`（示例: `MushroomImagePathParser`），测试用例也依赖这一格式，修改需保持向后兼容。
- 批处理/统计返回值: 批量/编码函数返回字典结构（包含 `total`, `success`, `failed`, `skipped` 等键）；可按此约定写断言与日志。
- 环境切换: 通过环境变量 `prod` 控制（见 `tests` 中的环境切换示例），AI 应尊重代码中对环境的读取方式。

# 关键文件与定位示例（快速打开）

- 主入口（CLI）: [main.py](main.py)
- 便捷脚本: [scripts/mushroom_cli.py](scripts/mushroom_cli.py)
- 文档索引: [docs/README.md](docs/README.md) 与仓库根 [README.md](README.md)
- 依赖与安装: [pyproject.toml](pyproject.toml) （注意 `uv` 源配置与 extras: `cpu` / `cu129`）
- CLIP 模型目录: [models/clip-vit-base-patch32/](models/clip-vit-base-patch32/)
- 测试脚本: [test_mushroom_system.py](test_mushroom_system.py), [test_minio_setup.py](test_minio_setup.py)

# 与外部系统的集成点（重要）

- MinIO: 通过 `utils/minio_service` / `utils/minio_client` 进行封装与健康检查。MinIO 的端点、桶名等来自配置/环境，修改或 mock 时优先使用 `create_minio_service()`。
- 数据库: 代码假设存在 PostgreSQL + pgvector（未在仓库内包含迁移脚本），任何对数据库模型的修改需同步测试脚本中的统计/断言逻辑。
- 模型文件: 本地 `models/` 中包含模型权重/配置，推理代码期望这些文件已就位（CLIP、LLaMA 相关）。

<!-- Copilot / AI agent 指南（为代码助理量身定制，简洁、可操作） -->

# 目标

- 帮助 AI 代码助理在本仓库中快速上手：理解架构、关键约定、运行命令与常见改动点。

# 快速启动命令（最常用）

- 安装依赖: `uv sync`（使用 `uv` 管理多个 PyPI 源，见 `pyproject.toml`）
- 常用 CLI:
  - `python main.py recent --hours 1`
  - `python main.py batch-all`
  - `python main.py validate --max-per-room 3`
  - `python scripts/mushroom_cli.py --help`
- 常用脚本:
  - `python scripts/process_recent_images.py`（批量处理最近图片）
  - `python scripts/add_llama_description_fields.py`（补充 LLaMA 描述字段）
- 测试脚本（独立脚本，非 pytest runner）:
  - `python test_mushroom_system.py`
  - `python test_minio_setup.py`
  - `python test_final_system.py`

# 高层架构（大体视图）

- 存储层: MinIO 用于图片对象；Postgres (+ pgvector) 存储向量与元数据。相关代码在 `src/utils/minio_*` 与 `src/utils/create_table.py`。
- 模型/推理层: CLIP（在 `models/clip-vit-base-patch32/`）负责向量化，LLaMA 用于文本生成/描述。模型加载与推理被封装在 `src/utils/mushroom_image_encoder.py` 等模块。
- 应用层: `main.py` 和 `scripts/` 提供 CLI/批处理入口；`examples/` 展示如何调用共享工厂和流程。

# 项目约定（必须遵守的实务细节）

- 工厂命名約定: 所有可复用共享实例通过 `create_*` 函数创建，示例：
  - `create_minio_service()` — [src/utils/minio_service.py](src/utils/minio_service.py#L337)
  - `create_minio_client()` — [src/utils/minio_client.py](src/utils/minio_client.py#L455)
  - `create_mushroom_encoder()` — [src/utils/mushroom_image_encoder.py](src/utils/mushroom_image_encoder.py#L846)
  - `create_mushroom_processor()` — [src/utils/mushroom_image_processor.py](src/utils/mushroom_image_processor.py#L456)
  - `create_env_data_processor()` — [src/utils/env_data_processor.py#L298]
  - `create_tables()` — [src/utils/create_table.py#L178]
  - `create_recent_image_processor()` — [src/utils/recent_image_processor.py#L506]

  不要在多个地方直接实例化 heavy resources（模型、数据库连接、MinIO 客户端）；优先复用 `create_*` 返回的单例/共享实例。

- 批处理返回約定: 批量接口返回字典，包含 `total`, `success`, `failed`, `skipped` 等键（测试与 CLI 依赖此结构）。

- 路径与文件命名: MinIO 对象路径遵循 `mogu/{mushroom_id}/{YYYYMMDD}/{filename}`，文件名通常为 `{mushroom}_{ip}_{collection_date}_{detailed_time}.jpg`。解析器/校验依赖 `MushroomImagePathParser`（在 `src/utils/mushroom_image_processor.py` 中）。

- 环境切換: 生产/开发切换通过环境变量 `prod`（或配置文件）控制；测试脚本会修改此 env 来模拟不同环境（见 `test_minio_setup.py`）。

# 关键文件与示例（直接打开定位）

- 主入口: [main.py](main.py)
- 脚本目录: [scripts/](scripts/)
- 工具/实现: [src/utils/minio_service.py](src/utils/minio_service.py), [src/utils/minio_client.py](src/utils/minio_client.py), [src/utils/mushroom_image_encoder.py](src/utils/mushroom_image_encoder.py), [src/utils/mushroom_image_processor.py](src/utils/mushroom_image_processor.py), [src/utils/create_table.py](src/utils/create_table.py)
- 配置: [configs/settings.toml](configs/settings.toml), [configs/static_config.json](configs/static_config.json)
- 日志: [src/utils/loguru_setting.py](src/utils/loguru_setting.py)
- 本地模型权重: [models/clip-vit-base-patch32/](models/clip-vit-base-patch32/)

# 集成點與注意事項

- MinIO: 使用 `create_minio_service()` / `create_minio_client()` 来获取封装后的客户端与高阶操作（健康检查、清单导出等）。不要直接操作底层 `minio` 客户端，除非明确需要。
- 数据库/pgvector: 仓库没有自动迁移脚本；如需初始化数据库，调用 `create_tables()`（会创建表与基础索引）。测试脚本会依赖这些表。
- 模型文件: 许多模块假定模型文件已在 `models/` 下准备好。CI/本地运行前请确认模型目录完整。

# 常见开发流程与调试技巧

- 快速验证环境: `uv sync` -> `python main.py validate`
- 本地 MinIO 测试: `python test_minio_setup.py`（脚本会演示 dev/prod 切换）
- 批量处理示例: `python scripts/process_recent_images.py`（可传入时间窗口参数）
- 启动单元式调试: 在需要观察共享实例的场景，先在脚本中调用 `create_*`，并传入到下游 `create_recent_image_processor(shared_encoder, shared_minio_client)`。

# 修改与 PR 指南（针对 AI 自动改动）

- 保持 `create_*` 工厂契约：若新增构造参数，应向下游传入兼容参数，并在调用处优先传入共享实例。
- 不要改变 `MushroomImagePathParser` 的外部行为；若扩展，新增方法并保持原 API。
- 修改批处理返回结构需同步更新 `scripts/` 与 `test_*.py` 中的断言与报告逻辑。

# 还需要的信息？

如果你需要我补充数据库表字段、配置键的完整列表，或把某个模块（例如 `mushroom_image_encoder.py`）拆解成更小的工厂/接口实现说明，请告诉我要优先详化的目标。

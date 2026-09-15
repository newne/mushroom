# `src/` 包结构重构方案（ADR-0014 第 ③ 步）

**状态：方案待拍板，尚未动代码。** 这一步是全仓库最不可逆的一次改动，ADR-0014 已经把它排在
最后；本文把"要动什么、动多少、怎么验、哪几件事必须你定"一次说清。

---

## 1. 为什么值得做

今天 `src/` 是**路径注入**式布局：跑起来靠 `PYTHONPATH=src`（或 `global_const/paths.py` 里的
`sys.path.insert`），于是 18 个顶层名字直接占用全局命名空间。三个具体代价：

| 代价 | 具体表现 |
| --- | --- |
| 名字太通用，随时会撞 | 顶层有 `utils/`、`tasks/`、`logs/`、`core/`、`api/`、`storage/`——任何一个第三方包叫同名，导入就变成抽奖（`import utils` 拿到谁取决于 sys.path 顺序） |
| 部署靠约定，不靠打包 | `Dockerfile` 只能 `COPY dist/src/` + `PYTHONPATH`；`uv` 装不了本项目自身（没有 build-system），"装好了"与"路径对了"是两件事 |
| 取证/审计不友好 | 站点上分不清"这个 `utils` 是我的还是依赖的"；混淆（`build.sh` 的 `obfuscate src`）也只能整目录来 |

目标形态（**建议**）：`src/mushroom/` 下按域分子包，`src/` 保持只放这个包和入口脚本。

```
src/
├── mushroom/
│   ├── __init__.py
│   ├── vision/  decision/  scheduling/  storage/  utils/  ...   ← 现有的域目录整体搬进来
│   └── ...
├── main.py            ← 入口脚本（薄，只做 sys.path/配置，然后 from mushroom... import）
└── scripts/           ← 运维脚本（见 §5 问题 2）
```

## 2. 现状（实测数字，2026-09-15）

| 项 | 数量 | 出处 |
| --- | --- | --- |
| `src/` 顶层目录 | 18（其中 4 个是产物/配置目录：`configs`、`logs`、`reports`、`core`） | `ls src/` |
| 真正的包（有 `__init__.py`） | 11：`api` `decision_analysis` `environment` `global_const` `monitoring` `scheduling` `segmentation` `storage` `tasks` `utils` `vision` | 同上 |
| 非包的代码目录 | `scripts/`（51 文件）、`data_collection/`、`web_app/` | 同上 |
| **跨包 import 行数** | **339**（含 `import a.b` 与 `from a.b import c` 两种写法） | 全量正则统计 |
| 依赖最重的几对 | `scripts→global_const` 63、`scripts→utils` 51、`vision→utils` 34、`utils→global_const` 17、`decision_analysis→utils` 16 | 同上 |
| **动态导入（字符串模块名）** | **10 处**，例如 `import_module("global_const.config_loader")`、`importlib.import_module("segmentation.temporal")`；另有 1 处 `import_module(module_name)` 变量形式（`vision/mushroom_image_encoder.py:32`，需逐个看清） | `grep import_module` |
| `sys.path` 注入 | 8 处：`global_const/paths.py`（`ensure_src_path`）、`scripts/mushroom_cli.py`、`scripts/reprocess_48h.py`、`scripts/run_decision_analysis_with_storage.py`、`scripts/run_offline_analysis.py`、`query_db.py`、`streamlit_app.py` 等 | `grep sys.path` |
| 部署耦合 | `docker/build.sh` 里 ~20 处引用 `src`（`codeenigma obfuscate src --output dist`、pyarmor `-r src/`、`cp -r src dist/`、`dist/src/codeenigma_runtime`…）；`docker/Dockerfile` 的 `COPY dist/src/ ./`；`src/configs/` 是 dynaconf 的配置目录（按路径找，不是包） | `grep -n src docker/*` |
| 现场入口 | `PYTHONPATH=/mnt/d/code/mushroom/src uv run python src/main.py`（README） | `README.md` |

> ADR-0014 里写的是 383 行；这次按同样的口径重数是 **339 行**（少了 `scripts` 里若干
> `import` 连续写法的重复计数）。数字下降不影响结论：**这是一次机械但全量的重写**。

## 3. 迁移步骤（建议按这个顺序，每步都能单独回退）

1. **先清障**：`tests/unit/test_clip_matcher.py` 那 9 个失败先修（测试引用了实现里已不存在的
   `_distance_to_similarity`）。理由：重构后要能一眼看出"是我改坏的"还是"本来就坏的"，
   而当前基线是脏的。
2. **建包**：`git mv` 11 个包目录进 `src/mushroom/`，加 `src/mushroom/__init__.py`，
   补 `[build-system]`（hatchling）+ `[tool.hatch.build.targets.wheel] packages = ["src/mushroom"]`，
   让 `uv sync` 能真正装上本项目（而不是靠 `PYTHONPATH`）。
3. **改导入**：用 **AST codemod**（不是正则）改写 339 行跨包导入 + 10 处动态导入：
   - `from utils.x import y` → `from mushroom.utils.x import y`
   - `import global_const.config_loader` → `import mushroom.global_const.config_loader as ...`
   - `import_module("segmentation.temporal")` → `import_module("mushroom.segmentation.temporal")`
   - 变量形式的 `import_module(module_name)` 必须人工看清（见 §5 问题 3）。
   - 包**内部**的相对导入（`from .x import y`）保持不动——那部分天然不受影响。
4. **改入口与脚本**：`src/main.py`、`streamlit_app.py`、`src/scripts/*` 去掉 `sys.path` 注入，
   改成 `from mushroom... import ...`；`global_const/paths.py` 的 `ensure_src_path()` 退役。
5. **改部署**：`build.sh`（`obfuscate src/mushroom`、`dist/src/mushroom` 的路径）、
   `Dockerfile`（`COPY dist/src/ ./` 之后 `PYTHONPATH` 指向哪）、README 的启动命令。
   `src/configs/` 留在原地（dynaconf 按路径找），但要在打包时明确带出去。
6. **验证**（下一节），然后**一次现场发版**验证（ADR-0014 要求它排在最后，就是因为它连着发版）。

## 4. 验证清单（缺一不可）

| 层 | 怎么验 | 现状能否在开发机做 |
| --- | --- | --- |
| 导入 | `python -c "import mushroom; ..."`、全部 11 个包逐个导入 | ✅（副本 venv 即可） |
| 单测 | `pytest tests/unit`（当前基线 73 passed / 9 failed，先清障） | ✅ |
| 调度器 | `TASK_REGISTRARS` 6 个、`register_patrol_jobs` 可导入 | ✅ |
| 启动 | `python src/main.py`：建表校验 + 迁移检查 + FastAPI/scheduler 起得来 | ⚠️ 会连现场 DB/Redis（见 `dependency-lock-refresh-2026-09.md` 的记录） |
| 视觉 job | 编码/推理跑通 | ❌ 需要模型与 GPU |
| 构建 | `build.sh`（混淆 + 镜像）与现场 `docker compose pull/up` | ⚠️ 会推镜像；发版动作 |
| 巡检侧 | `uv run --frozen pytest`（607 项）在**根目录改动后仍全过** | ✅ |

## 5. 已拍板（2026-09-15）

| 问题 | 决定 |
| --- | --- |
| 包名 | **`mushroom_solution`**（与 `pyproject.toml` 的 project 名一致） |
| `scripts/`、`web_app/`、`data_collection/` | **一起进包**（`mushroom_solution.scripts` 等）——否则它们仍得靠 `sys.path` 或 `python -m` 的特殊处理，重构不彻底 |
| 清障（9 个既有失败） | **已做**：见 `tests/unit/test_clip_matcher.py` 与下一节的发现 |

于是目标结构定为：

```
src/
├── mushroom_solution/
│   ├── __init__.py
│   ├── vision/  decision_analysis/  scheduling/  storage/  utils/  ...
│   ├── scripts/  web_app/  data_collection/        ← 三个非包目录也搬进来
│   └── ...
├── main.py            ← 入口脚本（薄：读配置 → from mushroom_solution... import）
└── configs/           ← 留在原地（dynaconf 按路径找），打包时显式带出去
```

### 清障时发现的两处实现分歧（顺带记录，**未擅自改行为**）

`decision_analysis/clip_matcher.py` 里同一个概念有三份规则，其中两份是死代码或与测试不一致：

1. `find_similar_cases` 曾经**内联**了一份"80/50"的置信档阈值，而 `_calculate_confidence_level`
   （带 `Requirements: 4.6` 注释）是"60/20"——同一个 70 分会被一处叫 high、另一处叫 medium。
   **已统一**为调用那个纯函数（测试里钉住两者必须一致）。
2. `_apply_multi_image_boost`（图数 + 质量 + 一致性）是**死代码**，真正跑的
   `find_similar_cases_multi_image` 用的是另一份"只按图数"的内联公式，且里面有**第三套**
   置信档阈值（85/50）。这一处**没动**——哪份是想要的语义需要你确认，之后再合并成一处。
   顺带一提：现有公式里单图也有 1.1 的"一致性"加成，即基线不是 1.0。

## 6. 仍未定的一件事

**`vision/mushroom_image_encoder.py:32` 的 `import_module(module_name)`**：那个变量从哪来？
（配置里的模块名？）——动态名字进不了 codemod，必须人工处理，否则会在运行期才炸。
这是重构开始前唯一还需要你回答的问题。

## 7. 与其它两条线的关系

- 第 ② 步（依赖锁）已完成且**零版本变化**，重构可以放心在这个基线上做。
- 巡检侧（`patrol/`、`deploy/`、`measure/`、`analysis/`）**完全不受影响**：它们是独立的 uv 项目，
  且本来就以真正的包形式安装（`patrol-workspace` 的 workspace 成员）。
- 现场发版前，`docs/patrol/prod-deploy/container-cutover.md` 的三条注意事项（控制器单会话、
  避开 `:01–:02` 采图窗口、`room.yaml` 是门禁真值）与这次重构无关，但仍适用。

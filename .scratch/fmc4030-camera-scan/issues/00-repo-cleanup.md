# 00 — 仓库治理：git 化、目录重组、workspace 骨架

**Spec:** ../plan.md（本票是 plan §2/§3 的执行票）

**What to build:** 让工作区从"无版本控制的多工程混杂"变为"单一 git 仓库 + uv workspace，新代码各就各位"。端到端行为：克隆/拉取本仓库后，`uv sync` 可安装 patrol/measure/analysis 三个包并跑通各自的 hello 级测试；旧工程不再干扰新开发。

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] `git init` + `.gitignore`（.venv/__pycache__/.mimosa/*.pt/datasets/saved_models/.scratch/_ref）+ 首次提交（移动前快照，保证可回退）
- [x] 删除 `spaceroom/`（空壳，项目名与 mushroom 冲突，plan 决策 D1）
- [x] `mushroom/` 整体移入 `legacy/mushroom-cls/` 冻结（决策 D2），README 注明"已过时，权重/数据供票 05 复用"
- [x] `mushroom_cli-main/mushroom_cli-main/` 内层迁为 `capture/`，删除嵌套层与 `mushroom_cli-main.zip`（决策 D4），补部署说明
- [x] 建 uv workspace：根 pyproject（members: patrol, measure, analysis）+ 三个包骨架（各含 pyproject、src 布局、一个可跑测试）
- [x] ruff + pytest 基线配置，三包各通过一个 smoke test
- [x] 提交结构化 commit（init / move legacy / vendor capture / workspace 各一笔）

## Comments

- 2026-08-30 完成。提交：`52b0431` 快照 → `56ea9c5` spaceroom 删除 + mushroom 归档 → `da32809` capture vendor → `98e77c5` workspace 骨架。验证：`uv run pytest` 3 passed；`uv run ruff check .` 全过（legacy/capture 已排除）。
- 仓库本地身份设为 `niucg1 <niucg1@users.noreply.local>`（全局未配置），可按需修改。
- 备注：uv workspace 下多包测试文件需唯一命名（`test_smoke.py` 同名会在 importlib 导入模式下把仓库根目录当命名空间包遮蔽真实安装包），已按 `test_<包名>_smoke.py` 约定命名。

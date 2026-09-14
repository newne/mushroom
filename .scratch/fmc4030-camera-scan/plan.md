# 实现计划 — 代码结构梳理与巡检系统落地

日期：2026-08-30
依据：`spec.md`（第三轮澄清版）+ 全工作区代码盘点
状态：待确认后按票执行

---

## 1. 现状盘点

| 目录 | 是什么 | 状态 | 判定 |
| --- | --- | --- | --- |
| `mushroom/` | 上一阶段 YOLO 周龄分类（train.py + make_dataset.py + 4 个 .pt 权重） | 逻辑已过时但**数据与权重有复用价值**（票 05 模型的起点） | **归档冻结** |
| `spaceroom/` | uv 空壳脚手架：`src/` 全空、README 空文件，**pyproject 项目名也叫 `mushroom`**（复制粘贴产物），依赖 opencv/pybind11/setuptools 暗示曾打算 pybind11 包 C++ SDK | Kiro 半成品残留，无一行可保留代码 | **删除** |
| `mushroom_cli-main/` | XCloud 相机截图服务（xcloudsdk_py），已验证可用的现成组件 | 外部下载的 GitHub zip 解压：**嵌套双层同名目录 + 留存 .zip** | **规范化为 vendored 目录** |
| `mushroom_cli-main.zip` | 上述服务的压缩包 | 冗余 | 删除（规范化后） |
| `.scratch/` | 本地 tracker（spec + 8 票）+ PDF 提取文本 | 正常 | 保留 |
| `AGENTS.md`、`docs/agents/` | tracker/标签/领域文档配置 | 正常 | 保留 |
| `.mimosa/` | 安全扫描插件状态 | 工具自管 | 忽略（进 .gitignore） |

### 1.1 关键问题清单

1. **整个工作区没有 git**——一切"梳理/重构/归档"都不可回退，必须先建版本控制。
2. **Kiro 半成品证据**：工作区内无 `.kiro` 目录，但 `spaceroom`（空壳 + 项目名冲突 + 无意图的 pybind11 依赖）和 `mushroom/src/make_dataset/make_dataset.py`（**同一逻辑两份近乎复制的实现** `main()` 与 `make_dataset()`、重复 import 三遍、注释掉的残块）是典型 AI 生成残留。
3. **两个工程 pyproject 项目名都叫 `mushroom`**，未来装包/导入必冲突。
4. **mushroom 项目杂质**：`main.py` 是 uv 的 hello-world 桩；wandb/comet 装了但代码里显式关闭；dev 依赖里混入 `pyarmor`（混淆工具，来路不明）；`src/` 目录布局但未配打包，`global_const` 靠 cwd 碰巧能 import；无测试、无 lint。
5. **没有承载新系统的空位**：票 01–08 的代码（FMC4030 客户端、巡检调度、测量管线、分析服务）目前无处安放。

---

## 2. 目标结构（单仓 uv workspace）

```
D:\code\mushrooms\               ← git 仓库根
├── pyproject.toml               ← uv workspace（members: patrol, measure, analysis）
├── AGENTS.md / docs/agents/     ← 现有配置
├── .scratch/                    ← tracker（spec + issues）
├── patrol/                      ← 库房主机应用（票 01/02/04/06/07）
│   ├── pyproject.toml           ← httpx, pyyaml, typer, pytest；不依赖 torch
│   └── src/patrol/
│       ├── fmc/                 ← FMC4030 ctypes 客户端（票 01）
│       ├── capture_client.py    ← 截图服务 HTTP 客户端（票 02）
│       ├── stations.py          ← 站位表加载/示教（票 04）
│       ├── scheduler.py         ← 时刻表/看门狗/重连/同步 prod（票 06）
│       └── elo/                 ← .elo 生成与下发（票 07）
├── capture/                     ← vendored 截图服务（原 mushroom_cli-main 内层原样迁入）
│   └── ...                      ← API 不变 :7003/pool_capture；仅加部署说明
├── measure/                     ← 测量管线（票 05）
│   ├── pyproject.toml           ← ultralytics/torch（重依赖隔离在此）
│   └── src/measure/             ← 标定、检出、聚合、quality gate
├── analysis/                    ← prod 侧（票 03/08）
│   ├── pyproject.toml           ← fastapi, pandas, apscheduler
│   └── src/analysis/            ← env_ingest（03）、对齐分析/区间表/报表 API（08）、
│                                 ← measurements 同步接收端（供 06 推送）
├── legacy/
│   └── mushroom-cls/            ← 原 mushroom 项目整体移入（代码冻结不改）
│       └── （datasets/saved_models 留原地路径引用，不入 git）
└── .gitignore                   ← .venv/__pycache__/.mimosa/*.pt/datasets/saved_models/.scratch/_ref
```

划分逻辑：**按部署边界分包**——`patrol` 跑库房主机（轻依赖，x86_64 Ubuntu）、`capture` 随它部署但独立进程、`measure` 重依赖 torch 可单独升级、`analysis` 跑 prod（10.77.77.39）。四者通过明确定义的数据契约（站位表 YAML、元数据 JSON、`measurements`/`environment` 表）衔接，对应 spec §5.3/§6。

---

## 3. 票件落位与新增

**新增票 00（仓库治理）**，发布到 `.scratch/fmc4030-camera-scan/issues/00-repo-cleanup.md`，并作为 01/02/03 的直接前置：

| 票 | 落位包 | 代码动作要点 |
| --- | --- | --- |
| **00 仓库治理（新）** | 根 | git init + .gitignore + 首次提交；删 `spaceroom/`；`mushroom/` → `legacy/mushroom-cls/`（冻结）；`mushroom_cli-main/mushroom_cli-main` → `capture/`，删嵌套层与 .zip；建 uv workspace 骨架 + ruff/pytest 基线 |
| 01 FMC4030 封装 | `patrol/fmc/` | ctypes 加载 .so；TDD 切面：状态结构体解析、错误码→异常、两段速 goto 的参数计算做纯函数测试；ctypes 薄层在测试中 mock |
| 02 单站采图 | `patrol/capture_client.py` | httpx 调 `:7003/pool_capture`（超时/重试/错误分类）；站位表 schema 定稿；与 fmc 串成 capture_at |
| 03 环控入库 | `analysis/env_ingest.py` | 先落 `environment` DDL + 拉取器骨架 + CSV 转储适配器；现场确认接口后只换 adapter |
| 04 全库巡检 | `patrol/stations.py` + `scheduler.py` | 示教 CLI（typer）；一轮巡检 = 票 02 的单站循环 |
| 05 测量管线 | `measure/` | 标定文件格式、检出模型（以 legacy 权重/数据为起点重标分割）、聚合与门控；`measurements` DDL 在 analysis 侧定义、measure 侧只写契约 |
| 06 常驻调度 | `patrol/scheduler.py` | 时刻表 + 心跳看门狗 + 重连回零 + 推送 measurements 到 analysis API（决策 D3） |
| 07 .elo 兜底 | `patrol/elo/` | 站位表→.elo 行文本生成器（**纯文本变换，重点 TDD 对象**）+ SDK 下发 |
| 08 对齐分析 | `analysis/` | 批处理 + 区间表 + FastAPI 报表 |

执行顺序即前沿顺序：**00 → (01 ∥ 03) → 02 → 04 → 05 → 06 → 08，07 随时可插**（blocked by 04）。

---

## 4. 决策点（执行前需拍板）

| # | 决策 | 推荐 |
| --- | --- | --- |
| D1 | `spaceroom/` 删除还是留档 | **删除**——空壳无代码可留，项目名冲突是实害 |
| D2 | `mushroom/` 归档方式 | **整体移入 `legacy/mushroom-cls/` 冻结**，权重与数据集路径在 plan/spec 引用；不花精力清理其内部坏味道 |
| D3 | 主机→prod 的数据通道 | **prod 起 FastAPI（analysis 包），`POST /measurements` 推送 + 失败本地缓存补传**；备选 rsync+SQLite |
| D4 | `capture/` 是否改造 | **原样 vendor，只加部署说明与配置样例**——它是验证过的厂商集成，不动其内部 |
| D5 | 测试与静态检查基线 | patrol/measure/analysis 均 ruff + pytest；patrol 全量 TDD（纯逻辑多），measure 以数据验收为主 |

---

## 5. 风险与边界

- 本工作区**未发现** `.kiro` 目录；若 Kiro 半成品另存他处（如 `D:\Documents\`），确认后一并按 §1 判定处理。
- `measure` 的验收（≤2 mm）依赖真机标定（票 04 之后），模型开发可提前但验收后置——已在票 05 写明。
- 所有移动/删除动作只在**票 00 内、git 首次提交之后**执行，保证可回退。

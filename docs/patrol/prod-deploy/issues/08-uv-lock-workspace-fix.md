# 08 — `patrol-workspace/uv.lock` 重生成失败：根因、最小修法、六条证据

**归属：** 锁 = 发版官（共享 artifact）；两个 pyproject 的改动经 Chief 2026-10-14 批 **(ii)**
**状态：** 已批准并实施（本票与改动同一提交）
**背景票：** §8.3（诊断）、§8.5（先用旧锁发了 analysis）

## 0. 结论一句话

仓库根 `/w` 是一个**非 workspace 的 uv 工程**（自带 `pyproject.toml` + `uv.lock`），uv 判定四个包被"包含"在它里面，
于是 `patrol-workspace` 的成员身份失效 ⇒ `deploy` 的 `patrol = { workspace = true }` 解析不了 ⇒ **`uv lock` 直接失败**。
修法 = 把 `patrol-workspace` 从 **workspace** 改成**普通工程 + path 源**，并去掉 `deploy` 自己那份 `[tool.uv.sources]`。
**另**：测试用的 **Pillow 一直是"锁外手工装"的**（`uv.lock` 里没有 pillow 条目）⇒ 锁描述不了真实测试环境；本次一并声明进 dev 组。

## 1. 根因（`uv lock -v` 原文，不是猜）

```
DEBUG Found workspace root: `/w/patrol-workspace`
DEBUG Adding discovered workspace member: `/w/patrol-workspace/../patrol`      ← 四个成员都认出来了
DEBUG Adding discovered workspace member: `/w/patrol-workspace/../deploy`
DEBUG Adding discovered workspace member: `/w/patrol-workspace/../measure`
DEBUG Adding discovered workspace member: `/w/patrol-workspace/../analysis`
DEBUG Found workspace configuration at `/w/patrol-workspace/pyproject.toml`
DEBUG Project is contained in non-workspace project: `/w`                       ← 决定性两行
DEBUG No workspace root found, using project root
  ╰─▶ `patrol` references a workspace in `tool.uv.sources` … but is not a workspace member
```

**对照实验**（最小工程，`uv 0.12.10`）：members 在根**之内**、members 指向根**之外**（`../x`）——**两种都能 lock 成功**
⇒ 与"`..` 成员"无关，**就是仓库根那个非 workspace 工程把成员抢走了**。这与 ADR-0012 的"两个独立 uv 项目"设计是硬冲突。

**证伪过的两个嫌疑**（都不是原因）：未跟踪的 `patrol/uv.lock`（10-09 遗留，已 park 到 `.scratch/prod-deploy/` 当证物）；
已跟踪的 `analysis/uv.lock`（临时移出后仍失败，已原样还原）。两种 uv 版本（宿主 0.12.20 / 镜像同版 0.12.10）都复现。

## 2. 改动（最小）

```diff
--- patrol-workspace/pyproject.toml
-[tool.uv.workspace]
-members = ["../patrol", "../deploy", "../measure", "../analysis"]
-
 [tool.uv.sources]
-patrol = { workspace = true }
-measure = { workspace = true }
-analysis = { workspace = true }
-deploy = { workspace = true }
+patrol = { path = "../patrol", editable = true }
+measure = { path = "../measure", editable = true }
+analysis = { path = "../analysis", editable = true }
+deploy = { path = "../deploy", editable = true }
 
 [dependency-groups]
-dev = ["pytest>=8.0", "ruff>=0.6"]
+dev = ["pytest>=8.0", "ruff>=0.6", "pillow>=12.2"]

--- deploy/pyproject.toml
-[tool.uv.sources]
-patrol = { workspace = true }
-
```

## 3. 六条证据

### 证据 1｜解析包集合逐条等价

| | 旧锁 | 新锁（含 pillow） |
| --- | --- | --- |
| 包数 | 34 | 37 |
| 仅旧锁有 | — | **0** |
| 仅新锁有 | — | **`psycopg 3.3.6` / `psycopg-binary 3.3.6` / `pillow 12.3.0`** |
| 版本变化 | — | **0** |
| 同名同版本交集 | — | 34 |

（psycopg 来自环境官在 `analysis/pyproject.toml` 加声明；pillow 来自本次第 6 条。）

### 证据 2｜339 删除行归类

```
删除 339 行分类：依赖/URL/hash 行 300 ｜ source: registry 写法 29 ｜ 结构/members/空行 10
旧锁 URL 主机：pypi.org 29 + files.pythonhosted.org 270
新锁 URL 主机：mirrors.aliyun.com 347
```
⇒ **339 行 = 270 条 wheel URL + 29 条 registry 行被改写成阿里云镜像地址**，不是"依赖变了"。
**hash 多重集**：旧 270（去重 270）/ 新 316；**仅旧锁有 0**（一个都没丢）；仅新锁有 46 = psycopg / psycopg-binary 的多平台 wheel。

### 证据 3｜冻结安装 + import

一次性容器（`uv 0.12.10` / py3.12）：`uv sync --frozen --no-dev --no-editable` → **rc=0**；
`import psycopg, patrol, deploy, measure, analysis` → **`imports OK; psycopg 3.3.6`**。

### 证据 4｜用新锁构建镜像 + 镜像内自查

见 §4。

### 证据 5｜全量门禁（**在锁派生出来的干净环境里跑**，不是拿现成 venv 顶）

见 §4。

### 证据 6｜测试用依赖显式声明 + "从零建环境"复现

- **扫描**：四包的 `tests/**`+`src/**` 里 import 的第三方包，与锁对照 ⇒ **真正缺的只有 `PIL`**（其余 `fmc_fakes`/`verify_round_integrity` 是仓库内模块，`global_const`/`utils`/`sqlalchemy` 是 A′ 那条惰性 import）。
- **Pillow 是有意可选**：`deploy/src/deploy/framing_wire.py:47` 是**函数内惰性 import**（注释写明"没装就明确拒绝"）；两个测试都用 `pytest.importorskip("PIL")`。⇒ **镜像不需要 Pillow**，只有测试环境需要。
- **已声明**：`patrol-workspace/pyproject.toml` 的 dev 组加 `pillow>=12.2`，锁里解析为 **`pillow 12.3.0`**。
- **从零复现**：一次性容器 `rm -rf /tmp/cleanvenv` → `uv sync --frozen --no-editable` → **rc=0**；
  `import PIL, pytest, numpy, pandas, psycopg` → **`PIL 12.3.0 | pytest 9.1.1 | numpy 2.5.2 | pandas 3.0.5 | psycopg 3.3.6`**。

## 4. 证据 4 / 5 的实测值

### 证据 4｜用新锁构建镜像 + 镜像内自查（**只建不部署**）

| 项 | 值 |
| --- | --- |
| tag | `registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0-20261014120223-5dfdf79` |
| image ID | `sha256:241cecd3d714633cd8a28e1bde21dc0288d7309e790c9c0f9ac3b2c8cfc7db20` |
| 体积 | **768,994,600 B**（上一版 `46ca2f2` = 748,057,905 B ⇒ **+20,936,695 B ≈ +20.9 MB**） |
| 构建 | 34 s（层缓存） |
| `import psycopg` | **3.3.6 OK** ✓（这次成功——§8.5 那版是 `ModuleNotFoundError`） |
| 四个包 | `patrol / deploy / measure / analysis` import 正常 ✓ |
| `PIL` / `pytest` | 镜像内**不存在** ✓（声明在 dev 组，`--no-dev` 不该带） |
| 信封代码 | `never_computed` / `computed_at` / `ranges_state` / `RANGES_AT` 全部命中 ✓ |
| revision 归属 | 4 个样本逐文件核对：**镜像内去 `\r` = `5dfdf79` 的 blob，4/4 一致** ✓ |

### 证据 5｜新锁全量门禁

**跑法（经 Chief 2026-10-14 裁定）**：在**锁派生出来的干净环境**里跑，且用**默认 editable**（与常规门禁 / dev venv 口径一致）；
并且**在"要提交的那个 commit 点"上跑**——仓库每分钟都在动，全量门禁只对当时那个 HEAD 有效。

**第一次跑（2026-10-14 12:0x，commit 前的 HEAD）**：**1084 passed / 1 failed / 3 skipped / 298 s**。
那一红是 `measure/tests/test_record.py::test_record_fields_are_the_analysis_contract`（measure↔analysis 字段契约）。
**它不是锁引起的，且运行期间已被后续提交修掉**：随后直接在工作区算两边清单 —— `RECORD_FIELDS` **20** / `MEASUREMENT_FIELDS` **20**，**对称差为空**。
计数从 Chief 基线的 1076 → 1084，同因（期间新增用例）。

**⚠️ 为什么不用 `--no-editable` 跑（写清依据，别再踩）**：在**仓库根**用 `uv sync --no-editable` 会按 wheel 装包，
而 **hatchling 会读仓库根 `.gitignore`**，其中 `env/`（第 44 行）这条规则会把 `analysis/src/analysis/env/` 排除
⇒ wheel 里没有 `analysis.env` ⇒ 3 个测试模块**在收集阶段就红**（`ModuleNotFoundError: No module named 'analysis.env'`）。
**生产镜像不受影响**（实测：镜像的 `site-packages/analysis/env/__init__.py` 在，因为 Dockerfile 只 COPY 子树、`/srv` 下没有 `.gitignore`）。
⇒ 这是**测试方法的假阳性**，不是锁的问题，也不是镜像的问题。

**最终数字（在本次提交的 commit 上重跑）**：见 `## 8. 提交点上的最终门禁`。

## 5. 回滚点

- 旧锁副本：**`.scratch/prod-deploy/uv.lock.bak-20261014-114542`**（sha256 `4bdbde216ee10eab2ac8bf99b0fde2c609f221d708e938f3ff3b192737cb4d0b`，原样保留，未删）。
- 两个 pyproject 的改动就是上面那几行，`git revert` 即可。

## 6. 事故与教训（自曝）

**这次 venv 损坏是我造成的。** 做"证据 3"的第一次尝试时，我在容器里跑了
`uv sync --frozen --no-dev --no-editable` 但**漏了 `UV_PROJECT_ENVIRONMENT`**，
于是它同步到了工作区那个**共享的 Windows venv**（Linux 侧 uv 是"精确同步"，直接卸了 dev 组、numpy 也留成半装状态），
**挡住了所有人的 pytest**。我先单独 `--reinstall-package numpy` 修好 numpy，又暴露出 `python-dateutil` 也是坏的，
最后用 `uv sync --frozen --reinstall` 把整环境按锁还原。**规格：在任何 `uv sync` 前必须显式指定环境**，
默认行为会打到工作区 venv（它对人人是共享的）。

## 7. 后续

- psycopg 进镜像 → 容器内自检 → **才谈把 05:20 搬进容器**（Step 3）。
- 宿主源码拷贝 `/opt/mushroom-analysis/src`（`batches.py` sha `98f39f52…`）**Step 3 落地后才消失**，现在保持不动。

## Comments

- 2026-10-14 发版官写票。证据全部来自实测（`uv lock -v` 原文、锁 diff 归类脚本、hash 多重集对照、一次性容器）。
- Chief 2026-10-14 批 (ii)："接受 churn，但把'最小且只增'换成'等价性证明'"，并追加第 6 条（测试依赖显式声明 + 从零复现）。

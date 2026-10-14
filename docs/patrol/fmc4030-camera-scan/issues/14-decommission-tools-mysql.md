# 14 — 退场业务库：`deploy-fetch-room` 的取数通路改走 pgsql（**方案票，先不动手**）

**裁定依据：** 用户 2026-10-14 —— `tools_mysql` 是**业务数据库，禁止使用**；算法侧取数走
pgsql（`mushroom_algorithm`）。Chief 派单（`deploy/**` 归巡检手）。

**归属：** `deploy/**` = 巡检手；`src/**` = 测量师（要改走票）；镜像/依赖/网络 = 发版官；
ADR-0020 = Chief/测量师；`analysis/**` 的同类通路 = 环境官。

**Status:** 方案待批（**不动手**）。

---

## 1. 现状：要下线的那条通路（全在 `fetch_room.py`）

| 位置 | 内容 |
| --- | --- |
| `deploy/src/deploy/fetch_room.py:48-49` | `DEFAULT_CONTAINER = "tools_mysql"`、`DEFAULT_DB = "mogu"` |
| `:52-56` | `SQL` 查 `mo_gu_batch`（列顺序即契约） |
| `:234-254` | `_run_mysql()`：`docker exec tools_mysql mysql …`（口令只从容器 env 读） |
| `:159-185` | `render_room_yaml()` 写 `room_id` / `entry_date` / `packages` / `batch_no` / `source`；`:166-167` 的 source 文案写 `mo_gu_batch id=…` |

它只做**取数 + 落盘**两件事（`room.yaml`），准入判定仍归 `patrol.room`（fail-closed 不复制）。

## 2. 目标数据源（算法侧，只读）

- **pgSQL `mushroom_algorithm`**：`src/configs/settings.toml:[production.pgsql]`（host `postgres_db`、
  db `mushroom_algorithm`），引擎 `global_const.global_const.pgsql_engine`。
- **现成接口**（`src/utils/batch_yield_service.py`，只读引用，**不在我领地**）：
  - `get_batch_ranges(db, room_ids=None, in_date=None, stat_date=None) -> [{room_id, in_date,
    min_date, max_date}]`（合并 IoT 与 `ImageTextQuality` 两路，`:105-130`）；
  - `find_batch_date_range(batch_ranges, room_id, in_date) -> {min_date, max_date}`（`:186-192`）。
- 语义映射：`room_id` = 库房号（611/612/7/8）；`entry_date` = 该库房**最新** `in_date`。
- ⚠️ **pgSQL 侧没有** `packages`（入库包数）与 `mo_gu_batch.id` 两个字段 ⇒ 见 §4 的降级口径。

## 3. 方案对比（**推荐 A′，待批**）

| 方案 | 做法 | 代价 | 结论 |
| --- | --- | --- | --- |
| **A** | deploy 直连 pgsql（SQLAlchemy + `psycopg`），自己查 `mushroom_batch_yield`/`ImageTextQuality` 的 `(room_id, in_date)` | 依赖最少（不加 `src/**`）；但**两处各写一份取数**，口径会漂 | 备选 |
| **A′** | deploy 直连 pgsql，**复用** `src/utils/batch_yield_service.get_batch_ranges` | 口径唯一；但 patrol 镜像要带 `src/**`（import 路径、依赖树、`settings.toml`）⇒ **需发版官评估镜像影响** | **推荐** |
| **B** | 走算法侧 HTTP 只读端点 | 多一条跨服务依赖 + 鉴权/端口/可用性 | 若无现成端点则成本更高 |
| **C** | 过渡期手填 `room.yaml` | —— | **不可取**：正是这个脚本要消灭的"手填错日期"路径 |

## 4. 口径与 fail-closed（与现状一致）

- 读不到 / 解析失败 / **未来日期** ⇒ **不写文件**（沿用 `FetchError` 语义），日志说清原因。
- `entry_date` 变化**必须打印旧→新**（`write_room_yaml` 现有行为保留）。
- `source` 文案改为指向新来源（`mushroom_algorithm.mushroom_batch_yield`），不再出现 `mo_gu_batch`。
- **`packages` / `batch_no` 需要裁定**（见 §8）：pgSQL 无对应字段 ⇒
  ① `packages` 留空/null（**不编值**）；② `batch_no` 改由 `(room_id, in_date)` 派生，或留空。
  ⚠️ **ADR-0020 现在把 `batch_no` 定义为 `mogu-<mo_gu_batch.id>`**（`:13`），与新数据源冲突，
  需要 Chief/测量师加修订注。

## 5. 引用点清单（含"照抄做法要标作废"）

| # | 位置 | 内容 | 动作 | 归属 |
| --- | --- | --- | --- | --- |
| 1 | `deploy/src/deploy/fetch_room.py:48-56,166-167,234-254` | mysql 容器/库/ SQL/`docker exec`/source 文案 | **改** | 巡检手 |
| 2 | `deploy/tests/test_fetch_room.py:27,123` | TSV 形状注释 + `mo_gu_batch id=100` 断言 | **改** | 巡检手 |
| 3 | `deploy/README.md:196,205,213` | 照抄 `tools_mysql` / `mo_gu_batch` 的做法 | **改 + 标"已作废"** | 巡检手 |
| 4 | `docs/patrol/prod-deploy/container-cutover.md:1356-1357` | `docker exec tools_mysql …` 取数示例 | **标作废**（保留为历史记录） | 发版官 |
| 5 | `docs/adr/0020-…:13,17,21,30` | 批次身份来源写 `mo_gu_batch` | **加修订注**（新来源 = pgsql `mushroom_batch_yield`） | Chief/测量师 |
| 6 | `analysis/src/analysis/batches.py:1,17,20,58`、`batch_ingest.py:1,9`、`api.py:308` | analysis 侧**同样**走 `docker exec tools_mysql mysql` | **同一裁定下的另一条通路**，需处置 | 环境官 |
| 7 | `analysis/tests/test_batches.py:36,38` | 断言 `docker exec tools_mysql` / `mo_gu_batch` | 随 #6 | 环境官 |
| 8 | `docs/patrol/env-analysis/spec.md:93,97,183`、`prod-deploy/gap-list.md:718,752` | 以 `mo_gu_batch` 为权威来源的描述 | 加注（新来源） | 环境官/发版官 |
| 9 | `pyproject.toml:26`、`uv.lock` | 根项目 `pymysql` 依赖 | 评估是否仍需要 | 发版官/Chief |
| 10 | `docs/patrol/prod-deploy/issues/01-…:72,109,218`、`env-analysis/issues/13-…`、`12-…` | 旧方案"改直连 MySQL 只读账号" | **作废/改写**（用户裁定禁业务库 ⇒ 改 pgsql） | 环境官/Chief |

## 6. `deploy/**` 里其它 MySQL 依赖：**没有**

`grep -rn 'mysql\|pymysql\|tools_mysql' deploy/` 只命中 `fetch_room.py`、`deploy/tests/test_fetch_room.py`、
`deploy/README.md` 三处（运行时只有 `fetch_room.py` 一条）。`analysis/**` 那两条不属我领地，已列 #6。

## 7. 验收（实施时）

- [ ] `grep -rn 'tools_mysql\|mo_gu_batch\|pymysql' deploy/` 只剩测试里的**历史注记**（或 0 命中）
- [ ] `deploy/tests` 全绿，含新用例：pgsql 取数形状、读不到**不写文件**、未来日期拒绝、
      `entry_date` 变更打印旧→新
- [ ] **本地（不连生产）**：注入假 Session/假 `get_batch_ranges` → `render_room_yaml` 产出
      与旧格式同构的 `room.yaml`（字段名不变，只有 `source` 换来源）
- [ ] 现场真连 pgsql 只读由发版官/现场执行

## 8. 需要裁定（**先不动手的原因**）

1. `packages` / `batch_no` 在 pgSQL 无对应 ⇒ 留空，还是按 `(room_id, in_date)` 派生？（牵动 ADR-0020）
2. 走 **A / A′ / B** 哪条？——A′ 需要发版官评估"patrol 镜像带 `src/**`"的代价（依赖树、
   `settings.toml`、镜像体积）。
3. `analysis/**` 的 #6 是否**同批**处理？否则"禁业务库"只落实一半。

## Comments

- 2026-10-14 巡检手：立项。**未改任何代码**；本票只做侦察与方案。侦察命令：
  `grep -rn 'tools_mysql|mysql|pymysql|mo_gu_batch' deploy/ docs/patrol analysis/src`、
  读 `deploy/src/deploy/fetch_room.py`、`src/utils/batch_yield_service.py`（只读）、
  `src/configs/settings.toml`（只读）。**未连业务库、未动生产。**

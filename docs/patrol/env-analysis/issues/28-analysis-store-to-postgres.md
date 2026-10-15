# 28. analysis 业务存储：删除 SQLite 依赖，改用本机 PostgreSQL

Status: ready-for-agent
归属：后端（+ 运维配合发布）
依据：**用户 2026-10-15 裁定**「删除对 sqlite 的依赖，使用本机 pgsql」；ADR-0026（源在 TDengine 只读、业务数据在 PG / Redis）
依赖：—（但与票 27 回填**同一条关键路径**：回填写哪儿由本票决定）

## 0. 现状（实测）

- 生产 `mushroom_analysis` 的数据层是 **SQLite 单文件**：compose 里 `ANALYSIS_DB=/opt/mushroom-analysis/mushrooms.db`，宿主卷 `/opt/mushroom-analysis` 挂进容器。
- 代码侧 `analysis/db.py` 通篇 `sqlite3`：`connect()` 返回 `sqlite3.Connection`，所有 `upsert_*` / `fetch_*` 都吃这个连接，API、jobs、测试全挂在上面。
- **PG 目前只用在批次台账**那一处：惰性 `psycopg`，DSN 由 `PGSQL_DSN_FILE=/etc/mushroom/pgsql.dsn` 提供（值不进 compose、不进 docker inspect）。

## 1. 为什么这是「宽重构」而不是一条纵向切片

一次改动会打穿整个调用面（数据层 + API + jobs + 测试），任何一条纵向切片都**不可能单独保持绿**。
所以按 **expand → migrate → contract** 排：

1. **expand**：新增数据访问门面（PG 实现），与现有函数**同名同签名**；两套实现并存，由开关选择。此步不删 SQLite。
2. **migrate（按模块分批，每批一个提交、CI 保持绿）**：环境 → measurements → rounds / images → operations → ranges → batches_state。
3. **搬迁**：把现有 `mushrooms.db` 的行一次性导入 PG，**幂等 + 可对账**（行数、小时数、抽样逐行比对）。
   注意：**环境原始序列的权威仍是 TDengine（ADR-0026）**，PG 只收小时聚合行。
4. **contract**：删除 `sqlite3` 依赖与 `ANALYSIS_DB` 开关；compose 去掉 `ANALYSIS_DB`、改注入 DSN；测试改用**临时 PG**。

## 2. 验收（缺一条不算过）

- [ ] 迁移后 `analysis/tests` 全绿（**对着 PG 跑**，不再对着临时 SQLite）
- [ ] `/environment`、`/ranges`、`/batches`、`/setpoints/daily` 的**响应形状与状态词逐字段一致**（迁移前后各存一份样本对比）
- [ ] `mushrooms.db` **不再被写入**（可断言文件 mtime 不变）
- [ ] DSN 走**文件**，不进 compose 明文、不进 `docker inspect`
- [ ] 有**回滚路径**（切回 SQLite 开关 + 旧镜像）
- [ ] **不写 TDengine**（ADR-0026 硬门）

## 3. 不做什么 / 待定

- **不写或改 TDengine**。
- **Redis 本期不引入**：现在没有任何热路径证据说明需要它；等出现明确需求再加，不为架构而架构。（用户提到 PG + Redis，此处把 Redis 记为**可选、未触发**。）
- 不动 patrol / console 的存储。
- 不在迁移里顺手改接口语义——**先等价搬迁，再谈优化**。

## Comments

### 2026-10-15 第 1 步（expand）落地：门面 + PG 实现并存，由开关选择

实现者 worktree `store-pg`（分支 `feat/store-pg`，基线 `3526125`，代码提交 `2bc8e7d`）。
**只做展开：没删 SQLite、没搬数据、没改任何调用方。**

代码（全在 `analysis/**`）：

| 文件 | 作用 |
| --- | --- |
| `analysis/src/analysis/store.py` | **数据访问门面**：`ANALYSIS_STORE` 缺省 `sqlite`、设 `postgres` 走 PG；与 `analysis/db.py` **同名同签名**的 20 个读写函数 + `connect`/`migrate`（共 22 个入口），按连接类型转发。开关值不认识 ⇒ `StoreConfigError`（**不静默退回 SQLite**）。 |
| `analysis/src/analysis/pg_store.py` | PG 实现。9 张表建在**独立 schema**（缺省 `analysis`，`ANALYSIS_PG_SCHEMA` 可改），列与 `db.SCHEMA` 逐列一致；写入前按 `information_schema` 的列类型对齐（布尔→0/1、数字→文本），把 SQLite 列亲和性一直在替写入方做的事补齐。 |
| `analysis/src/analysis/store_config.py` | 连接串解析的唯一实现（显式参数 → `PGSQL_DSN_FILE` → `PGSQL_DSN`）+ `StoreConfigError`；`analysis/batches.py` 改用它（错误类型照旧翻成 `BatchFetchError`）。 |
| `analysis/tests/test_store_switch.py` | 开关、转发、守卫、连接串优先级（12 用例，不需要 PG）。 |
| `analysis/tests/test_store_postgres.py` | PG 用例（13 用例）：**没有 `ANALYSIS_TEST_DSN` / `ANALYSIS_TEST_DSN_FILE` 就整组 skip**，故意不复用 `PGSQL_DSN*`（那两个是应用跑生产用的）；每个用例一个 `analysis_test_<随机>` schema，跑完 `DROP SCHEMA … CASCADE`。核心是一条**两套实现的等价性**用例：同一场景在 SQLite 与 PG 各跑一遍，读回来的行逐字段相等。 |

实测（2026-10-15，本机）：

- 不带测试 DSN：`245 passed, 13 skipped`（原有 233 条全绿 + 新增 12 条）。
- 带测试 DSN（`ANALYSIS_TEST_DSN_FILE` 指向 dev DSN 文件）：`258 passed`。
- PG 上建过的对象**只有**每次用例一个 `analysis_test_<随机>` schema（各 9 张表），跑完全部 drop；跑完复查目标库只剩 `information_schema` / `public` —— **没有 `analysis` schema、没有残留测试 schema、没碰 public 与遗留表、没碰农场库、没碰 TDengine**。连接串只从环境变量里的 DSN 文件读，值不进仓库。

§2 验收逐条（本步只可能满足其中三条）：

- [x] DSN 走**文件**：沿用票 13 的 `PGSQL_DSN_FILE`，没有新增任何明文注入。
- [x] 有**回滚路径**：回滚 = `ANALYSIS_STORE` 切回 `sqlite`（缺省值）配旧镜像，不改代码。
- [x] **不写 TDengine**：本步不 import 任何 TDengine 通路。
- [ ] 迁移后 `analysis/tests` 全绿（对着 PG 跑）——**未做**：套件缺省仍对着 SQLite；PG 侧是**新增**的一组等价性用例。
- [ ] `/environment`/`/ranges`/`/batches`/`/setpoints/daily` 响应逐字段一致——**未做**：本步只到存储层（读回来的行逐字段相等），HTTP 端点样本对比留给 migrate。
- [ ] `mushrooms.db` 不再被写入——**未做**：SQLite 仍是缺省后端。

给第 2 步（migrate）的现场勘测（读代码得来，逐条都会挡路）：

1. `analysis/api.py:110` 对连接跑 `PRAGMA journal_mode=WAL`（SQLite 专有）。
2. `analysis/api.py:265-267` 直接写裸 SQL `SELECT DISTINCT box_id FROM images WHERE room_id = ?`（`?` 占位符 + SQLite 方言）——要收进门面。
3. `analysis/align.py:302` 用 `pd.read_sql(..., conn)` 读 `environment`：pandas 不吃 psycopg 连接（要么 SQLAlchemy，要么经门面取行自己建 DataFrame）。
4. `analysis/align.py:300`、`refresh_ranges(db_path)`、`jobs.py:157` 到处按**路径**重连（`connect(db_path)`）；PG 下没有"路径"这个概念，要么改成传连接，要么让门面自己按 DSN 建。
5. `analysis/env/ingest.py:86`、`audit_ingest.py:57`、`batch_ingest.py:105`、`batch_map.py:32` 都是 `connect(args.db)`（CLI `--db` 路径）——PG 下这些 CLI 参数要换成 `--dsn`。
6. 票 27（回填）写的就是这份存储：先等第 2/3 步定下"写哪个 schema、哪张表"，再回填，否则要搬两次。


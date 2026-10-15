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

## 4. 用户追加裁定（2026-10-15）：一次走完 migrate + contract，并**删除 SQLite 依赖**

原话：「迁移并删除 sqlite 依赖，后续严禁使用本地文件数据库。」⇒ 本票不再是"先 expand、其余延后"：

1. **contract 步必须做完**：删 `import sqlite3` 与 SQLite 实现、删 `ANALYSIS_DB`、删"缺省退回文件库"的分支；
   **缺省即 PostgreSQL**，开关随之删除（ADR-0026 补充裁定第 3–5 条）。
2. **测试改为 PG 依赖**：对着独立测试 schema 跑；**无测试 DSN 时显式 skip 并说明原因**，不得回退临时文件库。
3. **一次性数据搬迁脚本**（幂等、可重跑、带对账），搬迁基准＝下表（2026-10-15 15:1x 生产盘实测）。
4. **顺手补索引**：现状 **0 个索引**；至少 `images(ts)`、`environment(zone, ts)` 一类（按实际查询口径定）。
5. 迁移确认后，宿主 `/opt/mushroom-analysis/mushrooms.db` 及遗留 `src/`、`.venv/` 一并清理。

### 搬迁对账基线（生产 `mushrooms.db`，2026-10-15 实测）

| 表 | 行数 | 时间跨度 |
| --- | ---: | --- |
| `images` | 3,925 | 2026-09-13T10:00:20 → 2026-10-15T12:27:22 |
| `environment` | 2,712 | 2026-09-17T10:00:00 → 2026-10-15T15:00:00 |
| `rounds` | 82 | 2026-09-13T09:05:03 → 2026-10-15T12:27:45 |
| `operations` | 62 | 2026-10-12T10:00:00 → 2026-10-15T10:15:00 |
| `batches` | 27 | — |
| `batches_state` | 1 | refreshed_at 2026-10-15T13:01:14 |
| `ranges_state` | 1 | computed_at 2026-10-15T13:01:13 |
| `measurements` | 0 | — |
| `ranges` | 0 | — |
| **合计** | **6,810** | 文件 6.97 MB、0 索引 |

> 注：`environment` 仍在增长（入库作业每 300s 一次）⇒ 搬迁时**以搬迁当刻实测为准**，上表是量级与形状的基线，
> 不是"必须等于 2712"。搬迁脚本要能把"搬完之后的实时增量"与"搬迁开始前的快照"分开说清楚。

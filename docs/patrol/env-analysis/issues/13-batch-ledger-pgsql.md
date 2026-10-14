# 13 — 批次台账取数改走 pgsql（MySQL 目标作废；事实盘点 + 代码口径）

**Spec:** ../spec.md §2.6、ADR-0020（批次身份 = 库房号 + 入库日期）

**归属：** 代码（`analysis/**`）= 环境官；pgsql 只读盘点与凭据 = 发版官；`src/**` 只读，
要改走票给测量师。

**Status:** ready-for-agent（代码已落地；§1 的现场事实与 §3 的未决项待回填/裁定）

## 0. 口径变更（Chief/用户 2026-10-14）

**`tools_mysql` 是业务数据库，禁止使用。** 算法侧取数一律走 **pgsql**
（compose 里的 `postgres_db`，`POSTGRES_DB=mushroom_algorithm`）。因此：

- 本票原先的"直连 MySQL 只读账号"**作废**：不建账号、不连 MySQL、compose 里不加 MySQL 通路。
- 旧实现（`analysis/batches.py` 的 `docker exec tools_mysql mysql …`，连带
  "给 API 容器挂 docker.sock"的方案）**已下线**。

## 1. 事实盘点

### 1.1 仓库里确证的（只读 `src/**`，未连任何库）

| 事实 | 出处 |
| --- | --- |
| pgsql 里带 `(room_id, in_date)` 的表至少两张：`mushroom_batch_yield`、`image_text_quality` | `src/utils/create_table.py:138-208`（`__tablename__` + 列定义） |
| `mushroom_batch_yield` 唯一键 `(room_id, in_date, stat_date)`，`fresh_weight`/`dried_weight` 可空 | 同上 `:178-201` |
| `image_text_quality` 每次采集一行，`in_date` = 进库日期，另有 `collection_datetime` | 同上 `:138-170` |
| 批次范围的既有算法：`get_batch_ranges` = **IoT 面板来源** ∪ **`image_text_quality`**，去重后按 `(room_id, in_date)` | `src/utils/batch_yield_service.py:105-130`；IoT 来源在 `:21-51`（走 `environment.processor.get_room_mushroom_info`，即面板 `INFO01` 的 InYear/InMonth/InDay） |
| `mushroom_batch_yield` 的行是**被灌出来的**：`init_batch_yield_records` 用上面那个并集建模板行（`fresh_weight` 全 NULL） | 同上 `:161-183` |
| 连接配置：`database_type = "postgresql"`、`driver = "psycopg"`、`host = "postgres_db"` | `src/configs/settings.toml:111-113` |
| 算法侧依赖里有 `psycopg[binary]>=3.3.2` | 仓库根 `pyproject.toml:25` |

⇒ **重要含义**：`mushroom_batch_yield` 是**派生表**，覆盖取决于那个作业有没有跑；
`image_text_quality` 是采集侧写入的原始表。单看一张都可能漏批，所以代码取**并集**。
（这也意味着它与 `mogu.mo_gu_batch` 那种"业务权威台账"不是同一性质 —— 见 §3 未决 1。）

### 1.2 待发版官只读回填（**我不连生产**）

```sql
-- ① pgsql 里到底有哪些表
\dt
-- ② 两张候选表的规模与时间跨度
SELECT 'yield' AS src, count(*) AS n, min(in_date), max(in_date),
       count(DISTINCT room_id) AS rooms FROM mushroom_batch_yield
UNION ALL
SELECT 'text_quality', count(*), min(in_date), max(in_date),
       count(DISTINCT room_id) FROM image_text_quality;
-- ③ 四个库房各自的覆盖（611/612/607/608）
SELECT room_id, count(DISTINCT in_date) AS n_dates, min(in_date), max(in_date)
FROM mushroom_batch_yield GROUP BY room_id ORDER BY room_id;
SELECT room_id, count(DISTINCT in_date) AS n_dates, min(in_date), max(in_date)
FROM image_text_quality GROUP BY room_id ORDER BY room_id;
-- ④ 两表差异（并集比单表多出哪些批次）
SELECT room_id, in_date FROM image_text_quality
EXCEPT SELECT room_id, in_date FROM mushroom_batch_yield
ORDER BY room_id, in_date;
-- ⑤ 最近批次（"当前批次"要用的）
SELECT room_id, max(in_date) FROM mushroom_batch_yield GROUP BY room_id;
```

**要回答的三个问题**：① 表清单（除这两张还有没有别的批次来源）；
② 四个库房的批次覆盖区间与最近入库日；③ 缺口（哪间没有数据、两表差在哪、`image_text_quality`
是否已停更 —— 它绑着老的 CLIP/LLaMA 链路，`mushroom_embedding` 最后一行是 2026-03-27）。

### 1.2 盘点结果（发版官只读实测，2026-10-14，`postgres_db` / `mushroom_algorithm`）

**表**：public 18 张；相关的有 `mushroom_batch_yield`、`image_text_quality`、
`mushroom_embedding`、`mushroom_image_quality`、`mushroom_env_daily_stats`、
`mushroom_text_encoding`。

**"是不是还在写"要看 `created_at`/`create_time`，不是看 `in_date`**：

| 表 | 行数 | `in_date` 跨度 | 最后被写 | 结论 |
| --- | --- | --- | --- | --- |
| `mushroom_batch_yield` | 27 | 2025-11-21 → 2026-10-10 | `create_time`=`update_time`=`2026-10-13 05:00:00` | **还在被灌** |
| `image_text_quality` | 19,615 | 2025-11-27 → 2026-10-10 | `created_at`=`2026-10-14 08:19:02`，`collection_datetime` 今晨 07:11:30 | **没有停更**（与"它绑着老链路所以停了"的猜测相反） |
| `mushroom_embedding`（`vector(512)`） | 890 | — | `last_coll=2026-03-27 09:01:30` | **确实停在 3 月** |

⇒ 精确说法：**老的 CLIP/LLaMA-embedding 链路停了，但 `image_text_quality` 这条仍在写**
—— 两者已经**脱钩**。`image_text_quality.max(mushroom_embedding_id)=1125` 而 embedding 只有
890 行，且**两表之间没有 FK 约束**（只有 btree index），所以悬空引用不会报错。
另：`mushroom_image_quality` **0 行**、`mushroom_text_encoding` **0 行**（想拿它们当源会是空）。

**覆盖（四库房最近入库日，两张表一致）**：607→**2026-10-10**、608→**2026-09-27**、
**611→2026-09-30**（与试点批次 `mogu-107` / `entry_date=2026-09-30` 一致）、612→**2026-09-30**。
每库房 `count(distinct in_date)`：5–8（batch_yield）/ 4–7（image_text_quality）。

**差集**：`mushroom_batch_yield` 有、`image_text_quality` 缺的仅 6 个 `(room_id, in_date)`，
全落在 **2025-11-21…2026-02-09**（607×2、608×3、612×1）；**反向差集 0**
⇒ 就 `in_date` 而言 `image_text_quality` **完全覆盖** `mushroom_batch_yield`。
**代码保留取并集**（多这 6 行老批次、零成本）；但"有效权威"实际是 `image_text_quality`。

## 2. 代码口径（已落地）

- `analysis/src/analysis/batches.py`：`fetch_batches()` 走 pgsql，
  SQL = 两张表的 `UNION`（`room_id`/`in_date` 非空，按库房+日期排序）。
- **惰性 import `psycopg`**：API 启动链不许依赖驱动；批次作业在容器里跑时才需要它。
- DSN 优先级：`--dsn` → **`PGSQL_DSN_FILE` 文件内容（推荐）** → 环境变量 `PGSQL_DSN`；
  **没有 DSN 就报错**，绝不退回 `docker exec`、也绝不退回"按断档切段"。
  为什么推荐文件：`env_file` 是 compose 创建容器时读文件并**注入成环境变量**的，
  值会出现在 `docker inspect` 的 `Config.Env`（发版官实测）——放文件、由进程运行时读
  才真的不进 inspect。
- `rows_to_batches()`：`date`/`datetime`/ISO 字符串 → `YYYY-MM-DD`，不合法即抛
  `BatchFetchError`；`batch_id` = `库房号|入库日期`（spec §2.6 第三种写法）。
- 测试 `analysis/tests/test_batches.py`：并集 SQL、形状与非法值、缺 DSN 报错、
 窗口切分，另有一条**"旧通路不许复活"**的用例（断言 `docker`/`tools_mysql`/
  `MYSQL_ROOT_PASSWORD` 都不在代码里）。

## 3. 未决 / 待办

1. ~~**`mushroom_batch_yield` 能不能当"批次权威"**~~ **已定**（2026-10-14 盘点后）：
   它是派生表，有效权威实际是 `image_text_quality`（就 `in_date` 而言完全覆盖前者）；
   代码取**并集**（保留那 6 行 2025-11…2026-02 的老批次，零成本）。
   "当前批次"仍按 spec §2.6「该库房最近一条入库日期不晚于今天」判。
2. **`batch_no` 显示名**：`mogu-<id>` 来自业务库主键，pgsql 这两张表没有对应 id ⇒ 现在写 NULL
   （不编值），`batch_id` = `库房号|入库日期`。界面若需要显示名，得选"用 `库房号|入库日期`"
   还是"让生产侧在 pgsql 补一列"——**待裁定**。
3. **镜像缺 `psycopg`**（发版官实测：`PATROL_IMAGE` 的 site-packages 里没有）⇒ Step 3 的硬前置：
   `analysis/pyproject.toml` **已声明** `psycopg[binary]>=3.3.2`（2026-10-14 补，版本区间与根
   `pyproject.toml:25` 一致）；**镜像构建以 `patrol-workspace/uv.lock` 为准**（`Dockerfile.patrol`
   是 `uv sync --frozen`），所以**锁要由发版官统一重生成**（共享 artifact，我不动锁），
   再重建镜像。**在此之前不迁 batch 作业。**
4. **DSN 提供方式**：已实现 `PGSQL_DSN_FILE`（文件优先）；生产建议宿主 `0600` 文件 bind 进容器 `:ro`。
   （顺带记一条实况：现役 `EMS_*` 是走 `env_file` 注入的，**在 `docker inspect` 里可见** ——
   与"值不进 inspect"的原口径不符，是否一并迁到文件形态由 Chief/发版官定，不在本票范围。）
5. **⚠️ 生产还在跑旧通路**（发版官发现）：宿主 `/opt/mushroom-analysis/src` 停在 10-12 19:07，
   其 `batches.py` 仍是 `docker exec tools_mysql`，`mushroom-batch-ingest.timer` 每天 05:20 还在跑
   （2026-10-14 05:20 已访问过一次，`ExecMainStatus=0`）⇒ **这是对"禁止使用业务库"红线的持续违反**，
   需要立即停掉该 timer 或尽快换到 pgsql 版；宿主 venv 没有 psycopg，**不能只拷文件**。

## 4. 验收

- [x] 代码不再出现 `docker exec` / `tools_mysql` / mysql 口令（有用例钉住）
- [x] 没有 DSN 时报明确错误，**不返回空表**（空表会把"当前批次"静默变成"没有批次"）
- [x] DSN 文件优先（`PGSQL_DSN_FILE`），文件读不到即报错、**不退回环境变量**（有用例钉住）
- [x] §1.2 盘点结果回填（表清单、四库房覆盖、差集、按 `created_at` 判停更）
- [ ] 镜像装 `psycopg[binary]`（发版官的 `Dockerfile.patrol`）后重建
- [ ] 停掉宿主那份每天 05:20 跑旧 `docker exec` 通路的 timer（违反红线，见 §3.5）
- [ ] 过渡期结束后补跑一次 `python -m analysis.batch_ingest --db <db> --once`：`batches` 行数与盘点一致（四库房最近入库日 607=10-10 / 608=09-27 / 611=09-30 / 612=09-30）
- [ ] `GET /environment/batches` 的"当前批次"与盘点结论一致（611 = `mogu-107` / 2026-09-30）

## Comments

- 2026-10-13 开票（原为"直连 MySQL 只读账号"）。
- 2026-10-14 **改口径**（用户裁定）：MySQL 全禁用，改 pgsql；代码已按 §2 落地
  （`fetch_batches` 重写、旧通路下线、测试重写）。**我不连生产**，§1.2 的盘点交给发版官
  只读执行后回填。

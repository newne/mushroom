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

## 2. 代码口径（已落地）

- `analysis/src/analysis/batches.py`：`fetch_batches()` 走 pgsql，
  SQL = 两张表的 `UNION`（`room_id`/`in_date` 非空，按库房+日期排序）。
- **惰性 import `psycopg`**：API 启动链不许依赖驱动；批次作业在容器里跑时才需要它。
- DSN 取 `PGSQL_DSN`（环境变量或 `--dsn`）；**没有 DSN 就报错**，
  绝不退回 `docker exec`、也绝不退回"按断档切段"。
- `rows_to_batches()`：`date`/`datetime`/ISO 字符串 → `YYYY-MM-DD`，不合法即抛
  `BatchFetchError`；`batch_id` = `库房号|入库日期`（spec §2.6 第三种写法）。
- 测试 `analysis/tests/test_batches.py`：并集 SQL、形状与非法值、缺 DSN 报错、
 窗口切分，另有一条**"旧通路不许复活"**的用例（断言 `docker`/`tools_mysql`/
  `MYSQL_ROOT_PASSWORD` 都不在代码里）。

## 3. 未决（要裁定或回填）

1. **`mushroom_batch_yield` 能不能当"批次权威"**：它是派生表，若灌数作业没跑，
   批次身份就会缺。建议口径：**以 `image_text_quality` 的 `(room_id, in_date)` 为底，
   与 `mushroom_batch_yield` 取并集**（现实现），并**把"当前批次"的判定显式依赖
   `in_date ≤ 今天`**；若 §1.2 的盘点显示两表差异很大，需要 Chief 裁一个权威源。
2. **`batch_no` 显示名**：`mogu-<id>` 来自业务库主键，pgsql 这两张表里没有 ⇒ 现在写 NULL
   （不编值）。界面若需要显示名，要么用 `库房号|入库日期`，要么由生产侧在 pgsql 里补一列 ——
   这是**用户可见的取舍**，需裁定。
3. DSN 与账号由发版官提供（走 `/etc/mushroom/` 的挂载文件，凭据不进仓库、不进 compose 明文）。
4. analysis 容器/镜像里是否有 `psycopg`（宿主 venv 没有；`PATROL_IMAGE` 待核）。

## 4. 验收

- [x] 代码不再出现 `docker exec` / `tools_mysql` / mysql 口令（有用例钉住）
- [x] 没有 `PGSQL_DSN` 时报明确错误，**不返回空表**（空表会把"当前批次"静默变成"没有批次"）
- [ ] §1.2 的盘点结果回填（表清单、四库房覆盖、缺口）
- [ ] 过渡期结束后补跑一次 `python -m analysis.batch_ingest --db <db> --once`：`batches` 行数与盘点一致
- [ ] `GET /environment/batches` 的"当前批次"与盘点结论一致（611 = 当前批次 mogu-107 的事实基准）

## Comments

- 2026-10-13 开票（原为"直连 MySQL 只读账号"）。
- 2026-10-14 **改口径**（用户裁定）：MySQL 全禁用，改 pgsql；代码已按 §2 落地
  （`fetch_batches` 重写、旧通路下线、测试重写）。**我不连生产**，§1.2 的盘点交给发版官
  只读执行后回填。

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
   （不编值），`batch_id` = `库房号|入库日期`。**已按 Chief 2026-10-14 的三步走落地**：
   ① 从**我们自己库里既有行**反推映射（`analysis/data/batch_no_map.json`，22 条，
   `generated_at=2026-10-14T10:47:12`，生成器 `python -m analysis.batch_map`；
   只读快照由发版官取，提交 `e6b2b56`）；② 映射缺失写 NULL + **"缺映射"可见计数**；
   ③ 长期解（生产侧在 pgsql 补真批次 id）待协调，不归我们单方面决定。
3. **⚠️ 库房号身份不一致（2026-10-14 发现；Chief 已裁定口径，机制已落地）**：
   映射文件里 607/608 两个库房的键是 **`7|…` / `8|…`**，不是 `607|…` / `608|…` ——
   即 `batches` 侧的库房号对这两间用**旧编号 `7`/`8`**，而 `environment.zone`、
   `measurements.room_id`、`audit.ROOM_BY_DEVICE_PREFIX`（`audit.py:28-33`）、
   `env.tdengine.ROOM_DEVICE_CODES`（`env/tdengine.py:33-38`）用 **`607`/`608`**。
   ⇒ 「库房号+入库日期」这个身份**跨域 join 会静默对不上**：`/environment/batches?zone=607`
   原先会说"还没有批次台账"，而台账里其实有 —— 只是挂着 `7`（试点 611/612 两侧一致，所以没炸）。

   **Chief 2026-10-14 四条口径 + 已落地的机制**（`analysis/src/analysis/rooms.py`、
   `analysis/data/room_alias.json`，提交见 Comments）：
   1. 别名表落仓库、带 `source`/`generated_at`、**只写确证过的对应**；
      **代码只读 `map`，每条必须有 `evidence`，缺 evidence 一律跳过并报警** ⇒ "不许猜"由结构保证。
   2. **join 不上 ⇒ `room_id_canonical = NULL` + 计数 + 打印样例**（哪几条、原始值是什么）；
      `resolve_rooms()` 是这条的落点，**原始 `room_id` 列一律不改**。
   3. **显示层不许洗白**：`display_room()` 给「`607`（原始 `7`）」/「`7`（原始编号，未登记别名）」，
      `/environment/batches` 每批带 `room_label`，页面不必自己拼。
   4. **长期解 = 生产侧统一编码**（待协调）；在那之前别名表是唯一过渡口径，
      **任何人不得在代码里再写一份映射** —— `rooms.py` 只有"读文件 + 比对"，无内联表。
   **已确证并落地（2026-10-14）**：`map` 里 `7→607`、`8→608`，每条带 `evidence` + `confirmed_by`。
   证据由发版官只读查询取得，是**零矛盾对应**而非"看着像"：
   - pgsql `607` 的 `in_date` 7 个 ⊃ SQLite `batches` 里 `7` 的 `entry_date` 6 个（100% 包含）；
     另外 611/612 的日期集合与 `7` **零重叠** ⇒ 四个库房里**只有 607 能容下 `7`**（排它性）；
   - `8` ⊂ `608`（5/7）同理，611/612 与 `8` 零重叠；
   - pgsql 侧只有 3 位编码（10 张带 `room_id` 的表全库没有一个同时带两种编码，也没有库房对照表）；
   - **溯源**：那 11 行 1 位编码是**旧 `docker exec tools_mysql` 通路**留下的历史编码
     （宿主旧源码 sha `98f39f52…`、每天 05:20 的 timer，就是映射文件里那 22 行的来源）；
     该 timer 已于 2026-10-14 10:28:35 停用 ⇒ **不会再产生新的 1 位编码行**。
   实测：`batch_no_map` 的 22 个键经别名表解析 **22/22 解出、0 未解**，
   11 行旧编码全部得到 `room_id_canonical`，显示标签是「`607`（原始 `7`）」。
   **顺带修掉一处同类坑**：两份数据文件（`room_alias.json`、`batch_no_map.json`）的默认路径
   原先按 **cwd** 解析 ⇒ 换个工作目录就"文件不存在 ⇒ 一律解不出 / batch_no 全 NULL"，
   而那个失败长得跟"还没确证/还没生成映射"一模一样。已改为**按包位置**解析（有用例钉住）。
4. **镜像缺 `psycopg`**（发版官实测：`PATROL_IMAGE` 的 site-packages 里没有）⇒ Step 3 的硬前置：
   `analysis/pyproject.toml` **已声明** `psycopg[binary]>=3.3.2`（2026-10-14 补，版本区间与根
   `pyproject.toml:25` 一致）；**镜像构建以 `patrol-workspace/uv.lock` 为准**（`Dockerfile.patrol`
   是 `uv sync --frozen`），所以**锁要由发版官统一重生成**（共享 artifact，我不动锁），
   再重建镜像。**在此之前不迁 batch 作业。**
5. **DSN 提供方式**：已实现 `PGSQL_DSN_FILE`（文件优先）；生产建议宿主 `0600` 文件 bind 进容器 `:ro`。
   （顺带记一条实况：现役 `EMS_*` 是走 `env_file` 注入的，**在 `docker inspect` 里可见** ——
   与"值不进 inspect"的原口径不符，是否一并迁到文件形态由 Chief/发版官定，不在本票范围。）
6. ~~**⚠️ 生产还在跑旧通路**（发版官发现）~~ **已停**（发版官 2026-10-14 10:28:35，
   `systemctl disable --now mushroom-batch-ingest.timer`；LAST=10-14 05:20:06 status=0，
   首个缺失窗口 10-15 05:20，窗口幂等可回补）——对"禁止使用业务库"红线的持续违反已终止。
7. **取集合还没同源**（`batches.py` 现在是 `mushroom_batch_yield ∪ image_text_quality` 的
   **临时 SQL**，已在文件头标注"待替换、不再扩展"）。权威 = `get_batch_ranges` 的实时并集，
   切换路线（Chief 2026-10-14 收窄后）：① A′ **实测失败**（发版官票 06：镜像里
   `ModuleNotFoundError: No module named 'utils'`；代价 7 依赖 24 MB + mlflow 闭包 610 MB
   ⇒ 破"Dockerfile.patrol 不背 mlflow"的底线）→ ② **让算法侧既有作业把实时并集物化成表/文件**
   （`mushroom_batch_yield` 就是这个物化产物：把刷新做成"入库即刷" + **新鲜度可见**），
   两侧读同一份物化结果 → ③ 才是新增只读端点。**选哪条由 Chief 定**；三条都不许自己写第二份 SQL。

## 3.5 物化缓存：整体替换 + 新鲜度 + 只读端点（2026-10-14 落地）

**为什么 upsert 不够**：`batches` 是 pgsql 并集的**物化缓存**，而 `run_once` 原先只 upsert、
**永不 prune** ⇒ 源侧编码或我们的归一方式变一次，陈旧行永久留下。实测形状：换口径后
同一批次同时挂 `7|2026-10-10` 与 `607|2026-10-10`，`window_of` 当成两个窗口 ⇒
页面上**两张一样的批次卡**、环境汇总重复计入。Chief 2026-10-14 裁定：**不做手删，
改由整体替换自然 prune**。

1. **`db.replace_batches`**：一个事务里 `DELETE` + 插入全部 + 写状态；被删掉的键**逐条打印 + 计数**；
   取数失败在写之前就抛错（`fetch_batches_with_report` 先跑），所以不会把表清空成"没有批次"。
2. **新表 `batches_state`**（单行，与 `ranges_state` 同款）：`refreshed_at` / `n_rows` / `n_pruned` /
   `pruned`(JSON) / `detail`(JSON)。**不给 `batches` 加列**：刷新是"一次作业事件"的属性，
   加到每行等于把同一事实重复 N 遍，且老行 NULL 表达不了"从没刷过"。
3. **`GET /batches?room_id=<库房>`**（只读、复用同一进程/端口，无写副作用）：
   ```json
   {"ok": true, "room_id": "611", "room_id_requested": "611", "room_label": "611",
    "batches": [{"room_id":"611","room_id_raw":"611","room_label":"611",
                 "entry_date":"2026-09-30","batch_id":"611|2026-09-30",
                 "batch_no":"611_20260930","display_name":"mogu-107","until":null}],
    "n_batches": 1, "ledger_refreshed_at":"2026-10-14T13:59:55",
    "ledger_n_rows":2,"ledger_n_pruned":0,
    "stale": false, "stale_after_h": 26.0,
    "room_alias": {...}, "duplicates": {...}}
   ```
   - `batch_no` = **派生显示名** `<规范库房号>_<YYYYMMDD>`（与 `batch_yield_service` 同式，
     与 `fetch_room.BatchRow.batch_no` 同式）；业务名 `mogu-*` 单独一列 `display_name`，
     **缺映射 ⇒ NULL（不编）**。**两个名字不混用**：一个是派生身份、一个是业务显示名。
   - `batch_id` = `<规范库房号>|<入库日期>`（ADR-0020，与 `fetch_room.BatchRow.batch_id` 同式）。
   - `ledger_refreshed_at` = `batches_state.refreshed_at`；`stale` = 超过 `stale_after_h`
     （默认 26h，`BATCH_STALE_AFTER_H` 可调）；**从没刷过 ⇒ `stale=true`**（不假装新鲜）。
   - 别名归一沿用 `analysis.rooms`；解不出的编号**点名 + 计数**（`room_alias`），不猜。
4. **读取侧去重**（`batches.dedupe_by_identity`）：同一 `(规范库房号, 入库日期)` 只留一条
   （优先原始编号==规范号那条），`/batches` 与 `/environment/batches` 都带上 `duplicates` 计数。

**⚠️ 缺口：现在没人按时刷**（这一条必须在端点被当准入依据之前定下来）

- 宿主 `mushroom-batch-ingest.timer` **已停**（2026-10-14 10:28:35，见 §3.6）；
- 容器里 `default_jobs` 只在设了 `BATCH_AT` 时才装配批次作业，而 **compose 现在没设** ⇒ 不跑；
- ⇒ 物化表最后刷新 = **2026-10-14 05:20:06**（宿主旧通路那次，22 行）。**26h 后 `stale` 即为真**，
  这正是端点要表达的事实。**先定"谁刷"再让它当准入依据**：建议容器设 `BATCH_AT=05:20`
  （票 12 设计的形态，与 `RANGES_AT` 同一步加进 compose；`docker/` 归发版官）。

## 4. 验收

- [x] 代码不再出现 `docker exec` / `tools_mysql` / mysql 口令（有用例钉住）
- [x] 没有 DSN 时报明确错误，**不返回空表**（空表会把"当前批次"静默变成"没有批次"）
- [x] DSN 文件优先（`PGSQL_DSN_FILE`），文件读不到即报错、**不退回环境变量**（有用例钉住）
- [x] §1.2 盘点结果回填（表清单、四库房覆盖、差集、按 `created_at` 判停更）
- [ ] 镜像装 `psycopg[binary]`（发版官的 `Dockerfile.patrol`）后重建
- [x] 停掉宿主那份每天 05:20 跑旧 `docker exec` 通路的 timer（2026-10-14 10:28:35，见 §3.6）
- [ ] 过渡期结束后补跑一次 `python -m analysis.batch_ingest --db <db> --once`：`batches` 行数与盘点一致（四库房最近入库日 607=10-10 / 608=09-27 / 611=09-30 / 612=09-30）
- [ ] `GET /environment/batches` 的"当前批次"与盘点结论一致（611 = `mogu-107` / 2026-09-30）
- [x] 物化缓存改**整体替换**（自然 prune + 逐条打印）+ `batches_state` 新鲜度（§3.5）
- [x] 只读端点 `GET /batches`（§3.5，供巡检 `fetch_room` 走 A′ 失败后的替代路）
- [ ] **定"谁按时刷"**（§3.5 缺口）：容器 `BATCH_AT=05:20`（compose，归发版官）——在此之前 `stale` 会为真

## Comments

- 2026-10-13 开票（原为"直连 MySQL 只读账号"）。
- 2026-10-14 **改口径**（用户裁定）：MySQL 全禁用，改 pgsql；代码已按 §2 落地
  （`fetch_batches` 重写、旧通路下线、测试重写）。**我不连生产**，§1.2 的盘点交给发版官
  只读执行后回填。

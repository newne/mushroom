# 17 — `deploy-fetch-room` 取数改走 pgsql（下线旧通路；**A′：复用权威源**）

**用户裁定（2026-10-14）:** 业务库禁止使用；算法侧取数一律走 pgsql（compose 的 `postgres_db`，
库 `mushroom_algorithm`）。
**Chief 裁定（2026-10-14）:** ① `batch_no` **走权威源派生**（`(room_id, in_date)` 口径），
**不许用业务库主键**；`packages` pgsql 侧没有、无法可靠派生 ⇒ **NULL（未知），绝不写 0**。
② **走 A′**：deploy 直连 pgsql + **复用** `src/utils/batch_yield_service.get_batch_ranges`；
**A 否**（自写一份 `(room_id, in_date)` 就是"第二套口径"）；A′ 的前提是**发版官评估
"patrol 镜像带 `src/**`"的代价**，代价过大才退 **B**（算法侧现成只读 HTTP 端点，不许新增服务/端口）；
C 否。③ analysis 侧环境官已做（`b1e29a4`），引用点清单按 HEAD 复核。
**依据:** 票 14（方案票/引用点盘点）、`analysis/src/analysis/batches.py`、ADR-0020（批次身份）、
`src/utils/batch_yield_service.py:105-130/266`、`docker/patrol-entrypoint.sh:29`（在巡检容器内跑）。

**Status:** 已实现（票 + 码同一提交）。**不碰 `analysis/**`；不连生产；不连业务库。**

---

## 0. 结论（四条）

1. **取数换源 + 口径唯一**：删掉"借别人的容器 + 从容器环境读 root 口令 + 容器内跑查询客户端"
   整条路（旧 `fetch_room.py:48-49`、`:233-254`、`:265-274`、`:287`），改调**权威源**
   `utils.batch_yield_service.get_batch_ranges`。本模块**不写 SQL、不解析连接串**——连接串与
   口径都归权威源（`global_const.pgsql_engine` / `get_batch_ranges`）。
2. **显示名派生**：`batch_no = <room_id>_<YYYYMMDD>`（如 `611_20260930`）。格式不是我编的，
   是权威源自己在用的写法（`batch_yield_service.py:266` 的 `batch_id = f"{room_id}_{in_date:%Y%m%d}"`）。
   **`packages` → `null`（未知，不是 0）**（pgsql 侧没有入库包数）。
3. **对外契约与 fail-closed 一字不变**：YAML 字段名/形状、`patrol.room` 可读回、取不到/解析失败/
   未来日期/库房不存在 ⇒ 报错且**一个字节都不写**、原子落盘、`entry_date` 变更打印旧→新、
   `--today`/`--print` 语义。
4. **下游可见变化（需环境官/界面师同步）**：`rounds.batch_no` 从 `mogu-<业务库主键>` 变成
   `<room>_<YYYYMMDD>`，`rounds.packages` 类信息不再有值（本来就是 null 形状）。
   **筛选/展示不能再按 `mogu-…` 串匹配**，见 §5。

---

## 1. 字段口径（逐列）

| `room.yaml` 字段 | 旧来源（业务库 `mogu.mo_gu_batch`） | 新来源（pgsql `mushroom_algorithm`） | 变不变 |
| --- | --- | --- | --- |
| `room_id` | `code_num` | `room_id` | **不变** |
| `entry_date` | `in_time`（截日期） | `in_date` | **不变** |
| `packages` | `in_num`（9792） | **无对应、无法可靠派生** | → `null`（未知，**不写 0**） |
| `batch_no` | `mogu-{业务库主键}` | **派生** `<room_id>_<YYYYMMDD>` | **值变**（口径按裁定，格式照权威源） |
| `source` 里的设备号 | `info_code` | 无 | 去掉 |
| `source` 里的系统天数 | `in_day_num` | 由日期派生（`day_on + 1`） | 值同、来源变 |
| `source` 里的身份 | — | `room_id|entry_date`（ADR-0020） | 新增（可对账） |

## 2. 方案对比（Chief 裁定记录，留证）

| 方案 | 判定 | 理由 / 代价 |
| --- | --- | --- |
| A：deploy 自写 SQL + 自解析 DSN | **否** | 那是第二套 `(room_id, in_date)` 口径，正是要消灭的东西 |
| **A′：复用 `batch_yield_service.get_batch_ranges`** | **采用** | 口径唯一；代价＝巡检镜像要带 `src/**` 与 sqlalchemy（该函数还依赖 `loguru`、`global_const`、`utils.create_table`，并有惰性 IoT 分支）⇒ **发版官评估体积/依赖** |
| B：算法侧现成只读 HTTP 端点 | 备选（A′ 代价过大时） | 不许新增服务/端口；替换点只有 `fetch_ledger_rows(runner=…)` 一处 |
| C：别的自造通道 | 否 | — |

**实现上为 A′/B 都留了门**：`ledger_rows_via_service()` 是默认实现（惰性 import + 注入 `session`），
`fetch_ledger_rows(room_id, *, runner=…)` 是唯一替换点；import 不到时**报一条指名道姓的错**
（"镜像要带 `src/**` 与 sqlalchemy"），**绝不退回自写取数**。

⚠️ **一条必须报的口径差异**（不属我改的范围）：权威源 `get_batch_ranges` 的集合是
**IoT ∪ `image_text_quality`**；而 `analysis/batches.py` 的 SQL 是
**`mushroom_batch_yield` ∪ `image_text_quality`**。两者不是同一集合（`mushroom_batch_yield`
是被 `init_batch_yield_records` 从前者灌出来的，可能滞后或缺行）。"巡检侧用权威源、分析侧用产量表"
是不是刻意的？需要 Chief/环境官定。

## 3. 变更清单

| # | 位置 | 改什么 |
| --- | --- | --- |
| 1 | `deploy/src/deploy/fetch_room.py` | 删旧取数通路（容器检视/容器内查询/`subprocess`/TSV 解析的 7 列契约）；改为 `ledger_rows_via_service`（调权威源）+ `fetch_ledger_rows(runner=)`；`BatchRow.batch_no` 变**派生属性**、`packages` 固定 None；`--from` 改成**离线两列 TSV**（演练/无库环境）；`--dsn` 不提供（连接串归权威源） |
| 2 | `deploy/tests/test_fetch_room.py` | 假权威源模块（注入 `session`）证明调用点与下游；runner 注入证明"无数据库也能取到批次与房间"；缺 `src/**`/缺依赖/权威源抛错三条 fail-closed；派生 `batch_no` 断言；**源码里不许出现 `subprocess`/`tools_mysql`/`docker`/`mysql`/`MYSQL_ROOT_PASSWORD`** 的守门用例 |
| 3 | 本票 | 裁定记录、口径表、部署前置、下游影响 |

## 4. 部署前置（发版官；不做完生产取不到数）

1. **巡检镜像带 `src/**`**（`utils.batch_yield_service`、`utils.create_table`、`global_const`）
   以及 `sqlalchemy` / `loguru`。**这就是 A′ 的前提**；评估不通过 ⇒ 改走 B（改一处调用点）。
2. `src` 的 pgsql 连接串沿用权威源自己的配置路径（`global_const.pgsql_engine`），
   本模块**不新增** DSN 环境变量，也不把口令搬进容器环境。
3. 通路上**不再需要 `docker.sock`**（附带收益：少一处特权挂载面）。
   `docker/patrol-entrypoint.sh:29` 的 `fetch-room` 入口本身不用改。

## 5. 下游影响（各归属方）

| 影响 | 谁 |
| --- | --- |
| `rounds.batch_no` 值形态从 `mogu-<id>` 变 `<room>_<YYYYMMDD>` | 环境官（列语义/回填口径）、界面师（展示与筛选）、发版官（线上逐字段复验） |
| `deploy/src/deploy/console.py:463`、`:998`、`deploy/src/deploy/patrol_serve.py:297` 只是透传该字段 | 界面师 |
| `ADR-0020` 的"巡检侧 batch_no 来自 `mo_gu_batch.id`"需修订注 | Chief 已转测量师 |
| `container-cutover.md:1356-1357` 等照抄旧做法的文档标作废 | 发版官（Chief 已转） |
| `env-analysis/spec.md:97`、`gap-list.md`、`prod-deploy/issues/01`、`issues/12/13` 旧方案 | 环境官（Chief 已转） |

**已按 HEAD 复核的 analysis 侧残留（报 Chief，不替它改）**：
`analysis/src/analysis/batch_ingest.py:9-10` 的文档串仍写着"台账在宿主容器里、用容器内查询读
（与 `deploy-fetch-room` 同一套做法）"；`analysis/src/analysis/api.py:308` 的文档串仍写
"台账由 `analysis.batch_ingest` 从业务库同步"。**代码通路已不在**（`git grep` 只命中这两处注释
与 `batches.py` 里"为什么不再走"的历史说明），属文档收尾，归环境官。

## 6. 验收（命令 + 判据）

- `cd D:\code\mushroom\patrol-workspace && .\.venv\Scripts\python.exe -m pytest -q ../deploy/tests/test_fetch_room.py`
  → **42 passed**（含"假权威源注入 session 取到批次与房间""runner 注入无数据库""源码守门"三类）。
- `ruff check` 干净。
- 生产侧（发版官，待部署前置完成）：`docker exec mushroom_patrol python3 -m deploy.fetch_room
  --room 611 --print` → `entry_date` 与权威源该库房最新 `in_date` 一致，`batch_no` = `611_YYYYMMDD`，
  `packages: null`，全程不出现任何业务库访问。

## 7. 待裁

1. 权威源（IoT ∪ 文本质量表）与分析侧（产量表 ∪ 文本质量表）的**集合差异**要不要统一（§2 末）。
2. A′ 的镜像代价评估结论（发版官）：可接受 ⇒ 保持 A′；不可接受 ⇒ 我按 B 换那一个调用点。
3. 显示名格式最终以权威源为准（本票已照 `batch_yield_service.py:266`），要改名由测量师定。

## Comments

- 2026-10-14 巡检手：立项 + 实现（同一提交）。Chief 三条裁定逐条落地：① 派生 `batch_no`
  + `packages` NULL（§1）；② A′（§2，含 A 否、B 备选、唯一替换点）；③ analysis 侧按 HEAD 复核（§5 末）。
- 旧通路下线证据：守门用例逐字扫源码里的 `subprocess` / 容器名 / 查询客户端名 / 口令变量名，
  任一出现即红；旧版本里那四段代码已整段删除。

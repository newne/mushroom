# 17 — `deploy-fetch-room` 取数改走**算法侧只读端点**（下线业务库通路）

**用户裁定（2026-10-14）:** 业务库禁止使用。
**Chief 裁定（同日，两次）：** ① 先走 pgsql / A′；② **读端点形状已定并落地**（analysis `3c30feb`）⇒
`fetch_room` 改 **HTTP 读 `GET /batches?room_id=…`**，**仍不许自己写 SQL / 解析连接串**。
**依据:** 票 14（引用点盘点）、ADR-0020（批次身份）、`src/utils/batch_yield_service.py`、
`analysis/src/analysis/batches.py`、`docker/patrol-entrypoint.sh:29`（在巡检容器内跑）。

**Status:** 已实现（票 + 码同一提交）。**不碰 `analysis/**`；不连生产；不连业务库。**

---

## 0. 结论（五条）

1. **取数 = 只读端点**：`GET {base}/batches?room_id=<库房>`。`base` 取
   `--analysis-url` → `PATROL_ANALYSIS` → 默认 `http://172.17.0.1:8000`（与 console 访问算法侧同一套路：
   容器里走宿主网桥网关）。本模块**不写 SQL、不解析连接串、不 import 任何数据库驱动**。
   ⇒ **A′ 那套前置（镜像带 `src/**` + sqlalchemy/loguru + `psycopg[binary]`）全部作废**，
   连 `uv lock` 都不用动（`httpx` 本来就是 deploy 的依赖）。
2. **三个名字分开**（Chief 裁定，端点已给全）：
   * `batch_id` = `<规范库房号>|<入库日期>`（ADR-0020）——本模块与 `(room_id, entry_date)` 推出的值
     **逐字核对**，不一致即报错；
   * `batch_no` = **派生名** `<规范库房号>_<YYYYMMDD>`——**直接用端点给的，不自己拼**；
   * `display_name` = 业务名 `mogu-*`，缺映射 ⇒ NULL，**不编**。
   ⚠️ 库里那列虽然叫 `batch_no`、装的却是 `mogu-*`；端点上两者已分开命名，**别混用**。
3. **准入 fail-closed 加一条**：**`stale=true` ⇒ 明确报"台账陈旧"、保留旧 `room.yaml`**，
   绝不静默用旧值；端点**没给 `stale`** 也拒绝继续（判断不了就不动）。阈值由端点给（当前 26h）。
4. **其余契约不变**：取不到/`ok≠true`/结构不对/未来日期/库房不存在 ⇒ 报错且**一个字节都不写**；
   原子落盘；`entry_date` 变更打印旧→新。
5. **台账元数据必须可见**：每次取数打一行"刷新于 / 行数 / 剪除 / 去重 / stale"；端点没给这些字段
   **也打告警**（"没日志"不许被读成"没有差异"）。

---

## 1. 端点返回（实测形状，`3c30feb`）

```json
{"ok":true,"room_id":"611","room_label":"611",
 "batches":[{"room_id":"611","room_id_raw":"611","entry_date":"2026-09-30",
             "batch_id":"611|2026-09-30","batch_no":"611_20260930",
             "display_name":"mogu-107","until":null}],
 "n_batches":1,"ledger_refreshed_at":"2026-10-14T13:59:55","ledger_n_rows":2,
 "ledger_n_pruned":0,"stale":false,"stale_after_h":26.0,"room_alias":{…},"duplicates":{…}}
```

| `room.yaml` 字段 | 来源 | 变不变 |
| --- | --- | --- |
| `room_id` | 端点 `room_id`（规范号） | 值口径可能被规范（别名归一），**这是好事** |
| `entry_date` | 端点 `entry_date` | 不变 |
| `batch_no` | 端点 `batch_no`（**派生名**） | **值变**：`mogu-107` → `611_20260930` |
| `display_name` | 端点 `display_name` | **新增**（缺 ⇒ null） |
| `packages` | 台账无此量 | `null`（未知，**不写 0**） |
| `source` | 本模块 | 含身份 `room|date` + 准入判定 |

## 2. 方案对比（留证）

| 方案 | 判定 | 理由 |
| --- | --- | --- |
| A：deploy 自写 SQL + 自解析 DSN | 否 | 第二套口径 |
| A′：deploy 直连 pgsql + 复用 `batch_yield_service` | **已被 B 取代** | 需镜像带 `src/**` + sqlalchemy/loguru + `psycopg`；端点把同一口径搬到服务侧后，这些前置全免 |
| **B：HTTP 读 analysis 只读端点** | **采用** | 零新依赖（httpx 已在）、零新密钥、零新驱动；口径唯一（端点自己管台账成员与刷新） |
| C 别的自造通道 | 否 | — |

**替换点**：`fetch_ledger_payload(room_id, *, url=None, runner=None, log=None)` ——
`runner` 可注入，测试与将来换通道都只改这一处。

## 3. 变更清单

| # | 位置 | 改什么 |
| --- | --- | --- |
| 1 | `deploy/src/deploy/fetch_room.py` | 删旧业务库通路与 A′ 的 pgsql/驱动代码；改为 `_http_get_batches` + `fetch_ledger_payload`（结构/`ok`/`stale` 校验）+ `rows_from_payload`（三个名字逐条核对）；`BatchRow` 增 `display_name`、`batch_no` 取自端点；`render_room_yaml` 增 `display_name:` 行；CLI 增 `--analysis-url`；离线 TSV 保留（演练用，旁路本机派生 `batch_no` 并注明） |
| 2 | `deploy/tests/test_fetch_room.py` | 54 条：三个名字分开/取自端点/缺 `display_name` 不编、`stale=true` 拒写且保留旧文件、缺 `stale` 拒继续、`ok≠true`/结构错、`batch_id` 不一致、HTTP 层（URL/参数/非 200/非 JSON）、元数据日志与缺失告警、离线旁路、落盘/CLI、**源码守门** |
| 3 | 本票 | 端点形状、命名裁定、`stale` 规则、部署与下游影响 |

## 4. 部署前置（运维）

1. **端点先在生产生效**：要等 **analysis 下次重建**（`3c30feb` 尚未在线上）。
2. **台账定时刷新**：宿主 timer 已停、容器里还没设 `BATCH_AT` ⇒ **明天 07:20 起 `stale` 会变 true**；
   运维在下次 analysis 重建时加 `BATCH_AT=05:20` 恢复容器内定时（Chief 已派）。
3. **巡检容器要能访问算法侧**：`PATROL_ANALYSIS`（建议显式设，与 console 同值）或 `--analysis-url`；
   默认 `http://172.17.0.1:8000` 走宿主网桥网关，与 `PROD_INGEST` 同一套路。
4. **不再需要**：`docker.sock`、任何 MySQL/pgsql 通路、镜像里的 `src/**`/sqlalchemy/`psycopg`。

## 5. 下游影响（各归属方）

| 影响 | 谁 |
| --- | --- |
| `room.yaml.batch_no` 从 `mogu-107` 变 `611_20260930`，业务名移到新字段 `display_name` ⇒ `rounds.batch_no`/页面展示与筛选口径要跟 | 环境官（列语义）、前端/界面（展示与筛选）、运维（线上逐字段复验） |
| ADR-0020 的"巡检侧 batch_no 来自 `mo_gu_batch.id`"需修订注 | Chief 已转测量师 |
| `container-cutover.md` 等照抄旧做法的文档标作废 | 运维（Chief 已转） |
| `analysis/**` 的旧文档串（`batch_ingest.py:9-10`、`api.py:308`） | 环境官（Chief 已转，我不动） |

## 6. 验收（命令 + 判据）

- `pytest -q ../deploy/tests/test_fetch_room.py` → **54 passed**；
  `pytest -q ../deploy/tests` → **323 passed**；`ruff check` 干净。
- 生产侧（运维，端点生效后）：`docker exec mushroom_patrol python3 -m deploy.fetch_room --room 611 --print`
  → `entry_date` 与端点一致、`batch_no` = 端点派生名、`display_name` = 业务名或 null、
  `packages: null`；日志里有"台账：刷新于 …"。`stale=true` 时必须返回 **2** 且 **room.yaml 不变**。

## 7. 待裁

1. `until`（下一批入库日）要不要也落进 `room.yaml`（本票暂不落，只透传不存）。
2. 巡检容器的 `PATROL_ANALYSIS` 是否显式写进 compose（建议写，别依赖默认值）。
3. `display_name` 缺映射时页面上显示什么（`room|date` 还是留空）——前端/环境官口径。

## Comments

- 2026-10-14 巡检手：**按端点形态重写并实现**（Chief 同日裁定）。要点：零新依赖（httpx 已在）、
  **不碰数据库/驱动**；`stale=true` fail-closed（保留旧 room.yaml）；三个名字逐条核对；
  元数据必须可见。源码守门用例扩到 10 个 token：`subprocess`/`tools_mysql`/`docker`/`mysql`/
  `MYSQL_ROOT_PASSWORD`/`psycopg`/`sqlalchemy`/`PGSQL_DSN`/`batch_yield_service`/`SELECT `。
- 2026-10-14 巡检手：前一版（A′ 复用 `batch_yield_service`）已随端点落地被取代，代码与用例整段替换，
  不保留双通道（双通道＝第二套口径）。

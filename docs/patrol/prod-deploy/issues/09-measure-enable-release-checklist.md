# 09 — 生产开启 `--measure` 的发布前置与动作清单（等测量师）

**归属：** 发布动作 = 发版官；参数清单 = 测量师；裁决 = Chief
**状态：** **已开启（2026-10-20）—— `--measure on` 落 prod 巡检，执行记录见 §6**
（2026-10-14 15:0x 用户给定算法 Plan `docs/patrol/growth-algo/plan-2026-10-14.md`，Chief 据此裁定：本期主交付改为**离线像素级簇趋势**，`--measure` 生产开启与 mm 列写入**暂停**；本票由"准备中"转为"待 P6"记账。2026-10-20 用户直接要求"measure 接入 prod 巡检"，本票据此恢复并执行，见 §6）

## 0. 一句话

`--measure` 从 off → on 是一次**行为开启**，不是一次普通重建：判据 = **运行态 argv** + **开启前后 `measurements` 行数** + **回滚路径**；
**采集侧（patrol 的取景/采图参数、console、preview、capture）一律不动**。

## 1. 前置（缺一不开启）

1. **测量师的开启清单**：`--measure on` + **尺度来源** + `--room` + `emit_empty` 等**整组显式参数** —— 不接受"默认值差不多"，要逐项写死；
2. **门禁全绿**在该 commit 点（按既定口径：锁派生干净环境 + 默认 editable）；
3. **本地 `/measurements` 非零行证据**（测量师在本地跑通、有行，而不是"应该能出"）。

## 2. 我开启时的动作（逐条留证）

1. **开前**：记 `measurements` 行数、`GET /api/measurements?room_id=611` 的 `rows/n`、**patrol 容器运行态 argv**（`/proc/1/cmdline`，不看 compose 猜）；
2. 按清单改**生效参数**（按清单指定落在 `.env` / compose / 命令行），改动文件带时间戳备份；
3. **只重建 `mushroom_patrol`**（`docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_patrol`）；**console / analysis 不动**；
4. **开后**：**从运行态**核 argv、记 `measurements` 行数变化、`/api/measurements` 的 `rows/n`、页面 200、六容器 healthy；
5. **回滚路径**：恢复备份文件 + 只重建 patrol（与开启对称，一步可退）。

## 3. 不做的事

- **不动采集链路**：不重建 console、不动 preview/capture 参数、不碰取景（`--framing` 仍 off、`--verify-content` 仍 off）；
- **不借这次顺手改别的**：一次只开一个开关，单写者制。

## 4. 当前状态

- **2026-10-14 15:0x：挂起（待 P6）。未开启 `--measure`，未改任何生效参数，未重建任何容器。**
- 前置一条都没变（测量师清单 + 该 commit 点门禁 + 本地 `/measurements` 非零行证据），**只是不再排期**。

## 5. 本次挂起范围（2026-10-14 15:0x 用户/Chief 裁定，发版官记账）

| # | 动作 | 状态 | 说明 |
| --- | --- | --- | --- |
| 1 | `--measure` 生产开启 | **挂起，待 P6** | 等 Plan P6（标定与生产适配）；本票 §1 三条前置同时转为 P6 前置 |
| 2 | mm 列写入（`mean_cluster_h_mm`/`mean_cluster_w_mm`） | **挂起，待 P6** | 同上；像素值不写毫米列（Plan §12.2） |
| 3 | 现场作业：θ 测定 / 朝向确认 / 竖直标尺抓图 | **挂起，现场不排期** | 采集链路用现版；「目标已就位」原本也只有用户能说 |
| 4 | 采集链路（patrol/console/preview/capture 的取景与参数） | **不动，保持现版** | Chief 明确"容器不动" |
| 5 | 重建 analysis（`2b24ead`/`58bb197`/`3c30feb` + `BATCH_AT=05:20` + 显式 `PATROL_ANALYSIS`）+ ④ `analysis.batch_ingest` | **一并按住**（按"容器不动"执行，等 Chief 明确是否放行） | 该次改动**已规划未执行**：构建点、compose 两行 diff、④ 判据（27 行 / `n_pruned=11` / `n_missing_batch_no=5` / `batch_no` 非空 22 / `batches_state.refreshed_at`）都留档在本节，随时可接 |
| 6 | 票 04 `/ranges` 代理缺口、票 08 锁修法等已交付项 | 已交付，不在挂起范围 | — |

### 5.1 挂起带来的一处**副作用**（Chief 2026-10-14 15:3x 裁定：**接受陈旧**，但有硬边界）

容器不动 ⇒ **容器内没有 `BATCH_AT`**，而宿主 `mushroom-batch-ingest.timer` 已于 2026-10-14 10:28:35 停用
⇒ **从 2026-10-15 07:20 起 `batches_state` 会被判 stale（物化最后刷新停在 2026-10-14 05:20:06）**。

**① 陈旧必须"可见"——`stale` / `stale_after_h` 的真实状态（我核过代码与线上，两级不同，别混）**

| 层级 | 状态 | 证据 |
| --- | --- | --- |
| **仓库代码/用例** | **已具备** ✅ | 生产者：`analysis/src/analysis/api.py:53`（`_stale_after_h()`，默认 **26h**，`BATCH_STALE_AFTER_H` 可调）、`:457` 算 `stale`、`:473-474` 出 `stale`/`stale_after_h`；用例：`analysis/tests/test_batches_endpoint.py`（`stale_after_h == 26.0`、"从没刷过不许假装新鲜"）。消费端：`deploy/src/deploy/fetch_room.py:122-126`（`stale=true` ⇒ **报"台账陈旧"、保留旧 `room.yaml`**）、缺字段**直接红**（`deploy/tests/test_fetch_room.py:147`） |
| **线上运行态** | **尚未具备** ❌ | 线上 analysis = `…c3dcbc5`（13:07 镜像）**不含该端点**：2026-10-14 15:3x 实测 `GET http://127.0.0.1:8000/batches?room_id=611` → **404**（patrol 容器内同路径 14:16 也实测 404） |

⇒ **"页面显示 stale" 的硬前置 = 先做一次 analysis 重建**（不是可选的美化项）。重建后 `stale`/`stale_after_h` 才在线上；前端接显示时**不要自己算 26h**，按响应字段用（阈值可由 `BATCH_STALE_AFTER_H` 调）。

**② 恢复动作的"就绪清单"（Chief 要求随时一条命令可放行；我这边全部预置）**

1. **compose diff**：analysis 服务加两行 —— `BATCH_AT=05:20`（与既有 `RANGES_AT=05:10` 同形）+ `PATROL_ANALYSIS=http://172.17.0.1:8000`（显式写，不靠代码默认）；档案在 `/home/sysadmin/algorithm/mushroom_service/mushroom_solution.yml`，改前带时间戳备份；
2. **构建点与门禁**：构建点要干净 → 在**该 commit 点**跑全量门禁（锁派生干净环境 `UV_PROJECT_ENVIRONMENT=/tmp/gatevenvN`，默认 editable）→ 记录 N passed / 0 failed；
3. **镜像内自查**：`import psycopg` OK、四包 import、信封代码在、`python -c "import measure.contract"` 能过、DSN 可读（`/etc/mushroom/pgsql.dsn` 同路径 `:ro` 已挂）、revision 归属按"工作区字节/去 `\r`"核；
4. **发布动作**：只重建 `mushroom_analysis`（`--no-deps`，patrol/console 不动）；先核**无轮次窗口**；
5. **迁移核 + 健康检查 + 复验**：`rounds`/`images` 行数不变、`ranges_state` 仍在、`batches_state` 一行、七容器 healthy；patrol 容器内真调 `GET {PATROL_ANALYSIS}/batches?room_id=611` 应 **200**（现 404）；
6. **④ 手动 `analysis.batch_ingest` 一次**：判据 **27 行 / `n_pruned=11`（逐字等于预登记 11 键）/ `n_missing_batch_no=5` / `batch_no` 非空 22 / `batches_state.refreshed_at` = 本次时间**；实际 ≠ 集合就原样发打印、不硬掰。

**③ 硬边界（Chief 定的那条我照记账，并补一条我实测到的缺口）**

- Chief：`room.yaml` 的 `entry_date` 固定 `2026-09-30`、**day 25 ≈ 2026-10-25**，之后准入窗口关掉、**巡检会停摆**；**最迟 2026-10-20 之前要放行一次 analysis 重建**（只重建 analysis、只加那两行）。
- **我补的实测缺口（重要，别把"重建"当"救活"）**：2026-10-14 15:3x 只读复查 ——
  - **没有任何调度器在跑 `deploy-fetch-room`**：`systemctl list-timers --all | grep -iE 'mushroom|fetch'` → **none**；`crontab -l` → **no crontab for root**；
  - `room.yaml` 仍是 **2026-10-06 21:01:10 / 683 B**；patrol 容器 19 小时内日志里 `fetch-room|room.yaml` 命中 **0** 次；
  - ⇒ **放行 analysis 重建只解决"端点可用 + 台账新鲜"，不解决"room.yaml 被刷新"**——后者还需要**有人给 `deploy-fetch-room` 一个调度**（`docker/patrol-entrypoint.sh:29` 有 `fetch-room` 角色，但没有 service/定时器在调它）。
  - **归属提醒**：`deploy/**` 是别的写者的领地，我不动；这条要 Chief 指定归属并排期，否则 10-20 的硬边界照旧破。

**在 Chief 明确之前我不动生产**（当前容器状态与 15:3x 复查一致：analysis/console = `c3dcbc5`、patrol = `89e4a54`、其余另五个 mushroom 容器未动）。

### 5.2 同窗口结案/未结的现场动作（诚实记账）

- **补光灯开/关对照帧：两腿有效、收尾腿未取得。**
  - 关灯：`20261014/B101_S101_level0_132531.jpg`，**354,410 B**，sha256 `739b45a1c7b6e09e4f4aa47e2369326ae610a2ed32d43a77f0cf082827309d19`；
  - 开灯：`20261014/B101_S101_level0_132812.jpg`，**396,331 B**，sha256 `a8be300d63a6dac3944f400021bab3f7c54f81ed2b8d52f6a5c8346c27471ce6`；
  - 差 **+41,921 B（+11.8%）**，同站位 S101、走 `no_lamp`、未 `goto`、未动 OUT0；本地副本 sha256 与远端一致；
  - 收尾那条"关"（14:04:52）**失败**：`ok=False | 采图失败: Failed to capture screenshot (connection pool) (code=-1239510)`，
    采图侧 `GET /pool_capture?...filename=20261014%2FB101_S101_level0_140452` → **500**；当时 `inflight=None`、`estop=False`、`machine=IDLE`、`capture /healthz` 全绿
    ⇒ **记为"未取得"**（不重试、不构造），随本票挂起。
- 结论（"灯到底进没进画面"）**留给 Chief/成像侧判定**；本票只记原始字节与时间。

---

## 6. 2026-10-20 执行记录（已开启）

**触发**：用户当日要求"measure 尚未接进巡检，查看待办并接入 prod 巡检"。

### 6.1 前置（§1）逐条

| # | 前置 | 结果 |
| --- | --- | --- |
| 1 | 整组显式参数 | ✅ 运行态 argv：`--measure on --measure-emit-empty`（`--room /app/configs/room.yaml` 沿用）；**`--mm-per-px` 本期不传**（v6 口径：菌包标准尺寸当标尺，`bag_standard`，票 15 §17.2） |
| 2 | 门禁全绿 | ✅ 本地：`deploy` 348 passed、`patrol` 439 passed / 2 skipped、`measure` 234 passed；`ruff` 对改动文件全绿 |
| 3 | 本地 `/measurements` 非零行证据 | ✅ 19:35 轮本机路径已产出行（48 帧），只是轮末因形状不匹配未落库（见 6.3） |

### 6.2 开前 / 开后

| 项 | 开前（19:29） | 开后（20:01） |
| --- | --- | --- |
| patrol argv | `patrol-serve`（无测量） | `patrol-serve --measure on --measure-emit-empty` |
| 镜像 | `0.1.0-…`（`89e4a54` 系） | `0.1.0-20261020194913-measurewire` |
| `analysis.measurements` | 0 行 | **48 行**（`room_id=611`，ts `19:53:25–20:00:40`） |
| `/measurements?room_id=611` | `[]` | 非零（每帧一行，含 `quality`/证据列） |
| 轮次同步 | 49 条（18:20 轮） | **146 条**（19:35 轮遗留 49 + 20:00 轮 97） |
| 页面 | `growth`/`ranges` 200 | 200（状态 `no_measurements` / `no_prev`，见 6.4） |

动作：备份 `mushroom_solution.yml` 与 `.env`（时间戳 `20261020-193347`；第二次换镜像前又备份了
`.env.bak-20261020-195142`）→
compose 的 patrol `command` 加两个参数 → `.env` 换 `PATROL_IMAGE` → **只重建 `mushroom_patrol`**
（`--force-recreate --no-deps`）；console / analysis / preview / console_web / solution 未动。

### 6.3 开启时踩到的两个阻塞项（都已修，属装配侧）

1. **镜像里没有 Pillow**：`pillow` 只在 workspace 的 dev 组，`uv sync --no-dev` 的巡检镜像里
   `import PIL` 失败 ⇒ `--measure on` 只会以"解不了图"拒绝开启。修复：把 `pillow>=12.2`
   落进 `deploy` 的运行时依赖（`uv.lock` +2 行）。
2. **行形状不匹配**：`measure.production.make_measure_fn` 返回 dict，`patrol.daemon` 的缝只调
   `to_row()` ⇒ 19:35 轮末抛 `AttributeError`，整轮不落库、不 flush（49 条留在 outbox，
   下一轮补同步，数据未丢）。修复：`deploy/measure_wire.py` 加 `WireRow` 包装，
   装配处做形状转换；新增 `test_rows_satisfy_the_daemon_to_row_seam`。

### 6.4 开启后的真实状态（必须说清）

- **行有、值没有**：48/48 行 `n=0`、`scale_source=none`、菌簇四列 NULL，
  quality = `no_detection+no_scale+no_theta+height_uncorrected+no_angle_profile+level_view+estimate`。
- 本地两帧真机图复核（10-11 样本、10-20 帧）：`find_bags` 27/2、果体候选 51/35，
  但 `filter_above_bag_top` **kept=0**（全部判为 `inside_bag_body`）⇒ **接线没丢行，是 L3 在当前取景下没有"袋面以上的簇"**。
- 归算法侧（票 15 §17、票 18 真机对照）；修好后**不需要再动装配**。
- `--measure-emit-empty` 是这次能看见"48 行全空"的原因；不开它这轮在库里会毫无痕迹。

### 6.5 回滚（一条命令，对称）

恢复 `mushroom_solution.yml.bak-20261020-193347` + `.env.bak-20261020-193347`（或把
`PATROL_IMAGE` 指回上一版）→ `docker compose -f mushroom_solution.yml --profile patrol up -d
--force-recreate --no-deps mushroom_patrol`。测量行已入 `analysis.measurements`，回滚**不删**它们；
若要停写，只回滚 argv 即可。

---

## Comments

- 2026-10-14 发版官写票。依据 Chief 当日裁定（单主线、动作排在测量师之后、只做准备）。
- 2026-10-14 15:0x 发版官转为"待 P6"，并记挂起范围与 `batches_state` stale 副作用（§5）。
- 2026-10-20 发版官按用户当日指令恢复并执行：`--measure on` 落 prod 巡检；
  §1 三条前置当时的状态与动作证据见 §6。**行已入 `measurements`，值为空**（L3 检出问题，
  归算法侧，不在本票范围）。

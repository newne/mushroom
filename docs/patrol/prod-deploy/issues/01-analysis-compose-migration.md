# 01 — analysis 侧 6 个 systemd 单元 → compose（迁移方案）

**归属：** compose 定义 / 镜像 / 发布动作 / 回滚 = 发版官；启动**语义** = 环境官（票 12）
**形态红线（用户 2026-10-13）：** 发版一律走 **docker compose**，**严禁 systemd**。
**用户追加约束（2026-10-13）：** **非必要不要增加 compose service。**
**Blocked by:** 票 12（`../env-analysis/issues/12-analysis-service-startup-contract.md`）
**Status:** approved-for-implementation（Chief 2026-10-13 评审通过；§3 已由"建议"改为**裁定**，
§4 顺序按"batch 暂留 systemd"调整）

## 0. 结论一句话

把线上 6 个单元承载的能力，搬进 **净新增 1 个 compose service**（`mushroom_analysis`），
复用现役巡检镜像与**已有的 `api` 角色**——不新增镜像、不新增 entrypoint 角色、不新增机制。

## 1. 为什么是 1 个（不是 0 个，也不是 4 个）

- **0 个不可行**：`:8000` 必须离开 systemd。唯一"不持控制器"的现成容器是 `mushroom_console`
  （操作者看结果的唯一界面），把数据入库路径塞进它 ⇒ 分析侧一崩就崩掉巡检台；
  `mushroom_solution` 是算法侧且镜像经 codeenigma 混淆，更不该碰。
- **4 个没必要**：那是照搬 systemd 的形态（一个单元一个容器）。四个作业共享同一个 SQLite、
  同一套凭据、同一条 import 链，拆开只增加挂载面与失败面。
- ⇒ **1 个 `mushroom_analysis`**：uvicorn 主进程 + 容器内常驻循环（语义见票 12 §1）。

## 2. 目标形态

| 旧形态（6 个单元） | 新形态 |
| --- | --- |
| `mushroom-analysis.service`（宿主 uvicorn :8000） | 容器主进程（`command: ["api"]`，**角色已存在**） |
| `mushroom-env-ingest.service`（常驻 300 s） | 容器内常驻循环，300 s |
| `mushroom-audit-ingest.{service,timer}`（每 10 min，实际 60 s） | 容器内常驻循环，600 s；**60 s 缺陷在迁移中消失** |
| `mushroom-batch-ingest.{service,timer}`（每天 05:20 CST） | 容器内常驻循环，`--daily-at 05:20`（票 12 新加 loop 模式） |

```yaml
  mushroom_analysis:
    <<: *base                      # TZ=Asia/Shanghai / restart / logging 轮转沿用基模板
    profiles: ["patrol"]
    image: "${PATROL_IMAGE:-registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0}"
    container_name: mushroom_analysis
    command: ["api"]               # ← 复用 patrol-entrypoint.sh 已有的 api 角色，零改动
    ports:
      - "${ANALYSIS_PORT:-8000}:8000"   # 与今天同址同端口 ⇒ .env 三处一个字不改
    environment:
      - ANALYSIS_DB=/opt/mushroom-analysis/mushrooms.db
      - TDENGINE_URL=http://172.17.0.1:6041
      - EMS_ENV_FILE=/run/ems.env
      - ENV_INGEST_INTERVAL=300
      - AUDIT_INTERVAL=600
      - BATCH_AT=05:20
    volumes:
      - /opt/mushroom-analysis:/opt/mushroom-analysis:rw   # DB 路径不变 ⇒ 回滚不用搬数据
      - /etc/mushroom/ems.env:/run/ems.env:ro               # 0600 原样带入，值不进 compose
    healthcheck:
      test: ["CMD", "curl", "-fsS", "-o", "/dev/null", "http://localhost:8000/healthz"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    mem_limit: 1g
    cpus: 1.0
```

**入口怎么把凭据交给进程**：**用 compose 的 `env_file: /etc/mushroom/ems.env`**（宿主机上那份
0600 文件），`environment` 里只放非凭据的键。**不用"挂载 /run/ems.env + entrypoint source"**
那条路——Chief 裁定 entrypoint 不加角色，而现有 `api` 角色不会 source 任何文件；票 12 §2 也把
`env_file:` 列为等价物。代价：`docker inspect` 的 `Config.Env` 里可读到，前提是宿主 root，
与 systemd 时代 `EnvironmentFile` 同级；真要更严用 docker secret，另开票。

## 3. 裁定（2026-10-13 Chief，已生效——本节不再是"建议"）

| # | 事项 | **裁定** |
| --- | --- | --- |
| 1 | ~~batch-ingest 的 **MySQL** 通路~~（旧实现：`analysis/batches.py` 走 `docker exec tools_mysql mysql …`） | **⚠️ 2026-10-14 更正（用户裁定）：`tools_mysql` 是业务数据库、禁止使用。** `batch_ingest` 不再走 `docker exec`，改为 **pgsql 取数**（`postgres_db` / 库 `mushroom_algorithm`）。所以"目标 (b)"里的"直连"= **pgsql**：compose 里**不要**加任何 MySQL 通路、**不要**挂 `docker.sock`。取数口径与事实盘点见 `../env-analysis/issues/13-batch-ledger-pgsql.md`。<br>**过渡期 (c)：暂不迁 batch-ingest**（它现在仍跑在宿主 systemd 上）。<br>⚠️ **永远不挂 `docker.sock`**：那个容器同时是对外 `:8000` API，挂 sock 等于给对外端口配 root 等价权限。 |
| 2 | **端口 8000 的对外方式** | **(a) `publish 8000:8000`**，`.env` 的 `PROD_INGEST`/`PATROL_ANALYSIS`/`PATROL_INGEST` **零改动**；(b) 走 compose DNS 单开后续票。 |
| 3 | **`mushrooms.db` 落点** | **(a) 挂宿主原目录 `/opt/mushroom-analysis`**（DB 路径不变 ⇒ 回滚不搬数据）。 |
| 4 | **采图服务**（见 §5） | **C1**：`FROM xcloudsdk-py:0.1.0` + 仓库 `capture.py` + Pillow；**基础镜像与衍生镜像都推 registry**；现场只换**现有**服务的 image tag、删两条 `levels-fix` 挂载与 `PYTHONPATH`。C2 不够——漂移可见了，改动还是到不了生产。 |

**healthcheck 依赖已核**：现役 `PATROL_IMAGE` 由 `docker/Dockerfile.patrol:43-49` 显式
`apt-get install … curl`，且容器内实测有 `/usr/bin/curl` ⇒ §2 里的 `curl -fsS … /healthz`
探活可用，不会假红。（不装 curl 的镜像才需要退回 `python -c` 探活。）

## 4. 迁移顺序（原子切换，窗口 ≈30 s）

1. 前置：`wal_checkpoint(TRUNCATE)` + 备份 DB（含 `-wal`/`-shm`）；备份 `.env` 与
   `mushroom_solution.yml`；本地构建/推送新 tag（**若需**）。
2. `systemctl disable --now mushroom-analysis mushroom-env-ingest \
   mushroom-audit-ingest.service mushroom-audit-ingest.timer`
   —— **只停这 4 个**（Chief 评审原文写"5 个"但列了 4 个，按列的 4 个执行）。
   **`mushroom-batch-ingest.{service,timer}` 留着继续跑**，理由见 §4.1。
   只停不删，unit 文件留盘作回滚点；`disable` 保证机器重启不复活。
3. `docker compose -f mushroom_solution.yml --profile patrol up -d mushroom_analysis`
4. 验收（缺一不可）：
   - 容器 `healthy`；`:8000/healthz` 200
   - **`/rounds`、`/images` 行数与切前逐条相等**（"还是那一份 DB"的判据）
   - `ss -lntp` 里 8000 的属主由 `uvicorn` 变 `docker-proxy`
   - 经 `:8002` 的 `/api/environment`、`/api/images`、`/api/room` 仍 200
   - env-ingest 出小时聚合日志；**audit 的环控登录 ≤ 1 次/10 分钟**（票 12 §7）
   - `data/runs/` 无新增（巡检侧零接触）
5. 回滚：`docker compose … stop mushroom_analysis` → `systemctl enable --now <被停的那几个>`
   （硬前提：同一时刻只有一边在写 DB）

### 4.1 为什么 `mushroom-batch-ingest.{service,timer}` 这一轮**不动**

**裁定（Chief 2026-10-13）**：因为 batch-ingest 走过渡期 (c)，**Step 2 不停它**——
它继续在 systemd 下每天 05:20 跑，直到 (b) 直连只读账号落地那天。

理由（照抄裁定，写清楚免得后人以为是漏做）：

- 停它就断**批次台账同步**（`analysis.batches` 会停在旧数据）；
- 而想手动补跑又绕不开 `docker exec tools_mysql` ⇒ 只剩"挂 sock"那条路，而那条**已被红线否掉**；
- **存量 systemd 留着不是我们新引入的形态**——它本来就在跑，比"断数据"或"给对外端口 root
  等价权限"都划算。

**因此**：(b) 落地日 = **最后一个 systemd 单元退场日**。那天要
`systemctl disable --now mushroom-batch-ingest.service mushroom-batch-ingest.timer`，
把 batch 也切进 §2 的那个容器，并补跑一次把窗口补齐（全量覆盖 upsert、按
`(room_id, entry_date)` 幂等，缺几天可回补）。
**在此之前，本票实施完成 ≠ systemd 全退场**——这句话要留在票里。

## 5. 采图服务：**不在仓库**那条（并进本方案）

**现状（2026-10-13 只读实测）**：

- 线上采图跑厂商镜像 `xcloudsdk-py:0.1.0`（337 MB，**2026-01-20 构建，`RepoDigests` 为空**
  ——从没推过 registry；唯一另一份是宿主离线包里的 `image.tar.gz` 127 MB）。
- 运行期用 **10 条宿主 bind-mount** 覆盖，其中两条是补丁：
  `levels-fix/capture.py → /usr/local/lib/python3.10/site-packages/xcloudsdk_py/capture.py`、
  `levels-fix/pylib → /app/levels-fix/pylib`（+ `PYTHONPATH`）。
- `levels-fix/capture.py` sha256 **`c65a1aa9…`**，**逐字节等于仓库 `3e84e2c` 那版**；
  仓库 HEAD 已是 **`782c04bd…`**（`63f35ee`：修 `cloud_error` 缩进）⇒ **`63f35ee` 从未上机**。
- 构建路径**其实在仓库里**：`third_party/capture/Dockerfile` + `scripts/make_offline_bundle.sh`
  （线上目录名 `xcloudsdk_py_offline_20260120_175307` 就是它 `OUT_DIR` 的产物）。
- 覆盖层 `docker-compose.override.yml`（宿主 `58a0d7de…`）与仓库
  `docs/patrol/prod-deploy/capture-fix/docker-compose.override.yml` **逐字节相同**。
- 老单元 `xcloud-capture.service` 仍 `disabled`/`inactive`（指向 `/home/sysadmin/algorithm/image_capture`，
  抢同一个 `:99`/`:7003`，**别 enable**）。

**判断：仓库对采图侧的改动确实到不了生产，而且已经漏过一次。**

**修法（**不动 compose service 数量**，符合用户红线）**

- **C1（推荐）**：加 `docker/Dockerfile.capture` =
  `FROM xcloudsdk-py:0.1.0` + `COPY third_party/capture/src/xcloudsdk_py/capture.py` 到 site-packages
  + `pip install pillow==12.2.0`；构建并推 registry（**顺带把基础镜像 `xcloudsdk-py:0.1.0` 也推一次**，
  否则别处无法重建）；现场只改**现有**那个 compose 服务的 image tag，并删掉两条 `levels-fix` 挂载与
  `PYTHONPATH`。以现役镜像为 base ⇒ 不重装依赖、不换 base OS，绕开 SDK 运行库那条老坑。
- **C2（备选）**：把 `levels-fix/`（capture.py + Pillow）纳入仓库 + 备份宿主离线包目录 +
  在仓库登记离线包清单与 sha256。**仓库改动仍然到不了生产**，只是把漂移变成可见。

## 6. 附件：线上 6 个单元文本（**历史形态，已废弃，仅作迁移参照**）

> 这些单元**只存在于生产上**，落盘进票据是为了抢救唯一的副本；**它们不是部署路径**，
> 迁移后不部署、不 enable。抓取时间 2026-10-13 14:4x–16:0x（只读）。
> 语义以票 12 为准；本附件不做语义解释。

**`mushroom-analysis.service`**（sha256 `8dc100c9…`，905 B，LF）与仓库
`deploy/systemd/mushroom-analysis.service`（`b9f4a403…`，926 B，CRLF）**逐行相同**（仅行尾差）。

**`mushroom-env-ingest.service`**（`36dd960c…`，553 B）
```ini
[Unit]
Description=蘑菇房环境数据入库（TDengine → analysis.environment）
Documentation=file:/opt/mushroom-analysis/README.md
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/mushroom-analysis
Environment=PYTHONPATH=/opt/mushroom-analysis/src
ExecStart=/opt/mushroom-analysis/.venv/bin/python -m analysis.env.ingest \
    --db mushrooms.db --source tdengine --url http://127.0.0.1:6041 \
    --interval 300
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

**`mushroom-audit-ingest.service`**（`3748fef7…`，543 B）——**缺陷就在这两行**
```ini
[Unit]
Description=环控平台操作日志入库（命令审计 → analysis.operations）
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/mushroom-analysis
Environment=PYTHONPATH=/opt/mushroom-analysis/src
# 凭据走 EnvironmentFile（gap-list D8 的建议），不进仓库、不进命令行历史
EnvironmentFile=/etc/mushroom/ems.env
ExecStart=/opt/mushroom-analysis/.venv/bin/python -m analysis.audit_ingest \
    --db mushrooms.db --once --lookback 180
Restart=always
RestartSec=60
```
（没有 `[Install]`；`--once` + `Restart=always` ⇒ 退出码 0 也每 60 s 重启一次，
24 h 内 Started/Deactivated **2466** 次，NRestarts 实测 **1232**。）

**`mushroom-audit-ingest.timer`**（`f20e075a…`，150 B）
```ini
[Unit]
Description=每 10 分钟拉一次操作日志

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
AccuracySec=30s

[Install]
WantedBy=timers.target
```

**`mushroom-batch-ingest.service`**（`0b7a497b…`，338 B）
```ini
[Unit]
Description=批次台账同步（mogu.mo_gu_batch → analysis.batches）
After=network-online.target docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/mushroom-analysis
Environment=PYTHONPATH=/opt/mushroom-analysis/src
ExecStart=/opt/mushroom-analysis/.venv/bin/python -m analysis.batch_ingest --db mushrooms.db --once
```
（没有 `[Install]`）

**`mushroom-batch-ingest.timer`**（`e2ed4109…`，133 B）
```ini
[Unit]
Description=每天同步一次批次台账
[Timer]
OnCalendar=*-*-* 05:20:00
Persistent=true
[Install]
WantedBy=timers.target
```

> ⚠️ `Persistent=true`（开机补跑）容器循环**没有**等价物。票 12 把 batch 的循环设计成
> "启动先跑一次，之后按 05:20 对齐"，**部分**覆盖了它；差异记在这里，别当成完全等价。

## 7. 不做的事

- 不落 systemd 单元进 `deploy/systemd/`；不 enable/新增任何宿主 systemd 单元。
- 不引容器内 cron/supercronic（票 12 §1 已选常驻 loop）。
- 不增加采图侧 service（见 §5，改的是镜像来源）。
- 不在同一时刻让同一作业的新旧两种形态并存（SQLite WAL 多写者）。

## 8. 裁决与实施记录

**2026-10-13 Chief 六条裁决（全部批准，按此执行）**

1. **迁移方案：批**。形态 = 净新增 1 个 service（`mushroom_analysis`），复用现役 `PATROL_IMAGE`
   与已有 `api` 角色；**定时落在 `analysis.api` 的 lifespan**（环境官代码，票 12）；
   **entrypoint 不加角色**；DB 原地挂载、`:8000` 同址发布、`.env` 三处零改动。
2. **`batch-ingest`：目标 = 从 pgsql 取数**（**2026-10-14 用户裁定后更正**；原文"直连 MySQL 只读账号"已作废）。
   `tools_mysql` 是**业务数据库、禁止使用**；算法侧取数一律走 **pgsql**（`postgres_db` / 库 `mushroom_algorithm`）。
   ⇒ compose 里**不加任何 MySQL 通路**、**不挂 `docker.sock`**；取数口径与事实盘点见
   `../env-analysis/issues/13-batch-ledger-pgsql.md`。
   **过渡期 (c)：迁移当天不迁它**（见 §4.1）；**原 (a) fallback 已删除，不留活口**；
   (b) 落地后补跑一次把窗口补齐（`batch_ingest` 按窗口幂等，缺几天可回补）。
3. **采图：走 C1**（`docker/Dockerfile.capture` = `FROM xcloudsdk-py:0.1.0` + 仓库 `capture.py`
   + Pillow；**基础镜像与衍生镜像都推 registry**；现场只换**现有**服务的 image tag、删两条
   `levels-fix` 挂载）。C2 不够——漂移可见了，改动还是到不了生产。
4. **6041 收紧：开票**，见 `03-tdengine-6041-exposure.md`；**不动 `tools_taos`**（不属我们的
   compose 项目）。
5. 实施顺序：**audit-ingest → env-ingest → analysis API →（batch 先不动）**，每步
   `disable --now` → `up -d` → 验收 → 通过才下一步，**每步报 Chief**。
6. 切前 `PRAGMA wal_checkpoint(TRUNCATE)` 再备份。

**实施前置依赖（未满足就不能开始）**：定时循环要在 **`analysis.api` 的 lifespan** 里跑，
那是环境官票 12 的代码；她落地并被构建进 `PATROL_IMAGE` 之前，容器里只会有 API、三个 ingest
不会跑。⇒ 迁移第一步的**真正前置**是「票 12 代码进镜像」，不是 compose 文件。

2026-10-13 16:2x 复核（写票时点）仍未满足，证据：

| 检查 | 结果 |
| --- | --- |
| `analysis/src/analysis/api.py` 里 `lifespan` / `scheduler` / `threading` / `asyncio` | **0 处**（16:17:04 那次改动是别的内容，+19/−6） |
| `analysis/src/analysis/` 下是否存在调度模块 | **没有**（只有 `db/align/api/batch_ingest/…`） |
| 环境官是否仍在改 | **是**：`db.py` 16:24:33、`align.py` 16:22:59（都在我 16:2x 提交之后） |
| 镜像能不能现在建 | **不能**：`Dockerfile.patrol` 会 `COPY patrol/ deploy/ measure/ analysis/`，当前工作区混着环境官 + 测量师 + 界面师的在飞改动，烤进镜像等于把半成品发到生产 |

⇒ **在 `api.py` 出现 lifespan 定时、且她能给出一个干净的构建点（提交或明确的文件集）之前，
本票不实施**。这不算拖延：切成现状会让三个 ingest 全部停摆。

### 8.1 ⚠️ 迟到的实施记录（发版官 2026-10-14 补记）

上面"§8 16:2x 结论"是**当时的**判断。**当晚前置满足了，Step 1 与 Step 2 都已执行**，票面一直没回写
（Chief 2026-10-14 因此看到的是"批准未实施"）：

| 步 | 时间 | 做了什么 | 判据（实测） |
| --- | --- | --- | --- |
| **Step 1**（API + audit 同容器） | 2026-10-13 **16:43** | 停 `mushroom-analysis.service` + `mushroom-audit-ingest.{service,timer}`；起 `mushroom_analysis` 容器（只设 `AUDIT_INTERVAL`） | 行数与切前逐条相等（67/3203/0/2524/27/22）；`:8000` 属主变 docker-proxy；三个单元 disabled/inactive |
| **Step 2**（+ env） | 2026-10-13 **17:0x** | 停 `mushroom-env-ingest.service`；加 `ENV_INGEST_INTERVAL=300` 重建容器 | `analysis.environment` **2524 → 2528**、游标推进；容器日志 `[ingest] 启动作业 env（每 300.0s）` |
| **Step 3**（batch 迁 compose） | **未做** | 等 (b)…**且 2026-10-14 目标形态已改为 pgsql** | 见上 |

**2026-10-14 09:26 只读复核（当前态）**：`mushroom-analysis.service`、`mushroom-audit-ingest.{service,timer}`、
`mushroom-env-ingest.service` **全部 inactive**；`analysis.audit_ingest` / `env_ingest` 进程数 **0 / 0**；
容器内 audit **600 s 一轮**（不是旧单元的 60 s）。
**唯一残留的 systemd 存量 = `mushroom-batch-ingest.timer`**（enabled+active；LAST **2026-10-14 05:20:06**、
`ExecMainStatus=0`；NEXT 2026-10-15 05:20）。

⚠️ **合规缺口（2026-10-14 裁定带出的，需裁定）**：batch 那条 timer 跑的是**宿主源码拷贝**
`/opt/mushroom-analysis/src`（整目录停在 **10-12 19:07–19:19**），其中 `batches.py`（sha `98f39f52…`）
**仍是旧的 `docker exec tools_mysql` 通路**；而**仓库版已经改成 pgsql + 惰性 `psycopg`**。
⇒ **用户明令禁止的那个业务库，今天 05:20 还被这个定时任务访问过一次，明天 05:20 还会再访问。**
且**不能只拷文件**：宿主 venv **没有 `psycopg`**（新代码会直接抛"环境没有 psycopg"）。
三条路（**未定，等裁定**）：(a) 立刻 `systemctl disable --now mushroom-batch-ingest.timer`（合规优先，
台账停更、可回补）；(b) 给宿主补源码 + 装 `psycopg[binary]`（快，但又是"宿主打补丁"）；
(c) 走 Step 3 搬进容器（正确，依赖 `psycopg` 进镜像 + pgsql 两张表有数）。

**已落盘的相关票**：`02-preview-channel-stability.md`（归属巡检手）、
`03-tdengine-6041-exposure.md`（平台侧，我们不动）。

### 8.2 2026-10-14 10:28 执行记录（Chief 裁定：合规优先 + 清隐患）

**① batch timer 已停** —— `systemctl disable --now mushroom-batch-ingest.timer`

| 项 | 值（实测） |
| --- | --- |
| 最后一次成功 | **2026-10-14 05:20:06 CST**，`ExecMainStatus=0`，日志 `[batch_ingest] 完成，22 个批次` |
| 停之前 | `enabled` + `active`，NEXT = **2026-10-15 05:20:00** |
| 停之后 | **`disabled` + `inactive`**；`systemctl list-timers --all` 里已无任何 mushroom timer |
| **停更时段** | 起 = **2026-10-14 10:28:35 CST**（停用时刻）。今天 05:20 已跑过 ⇒ **第一个会缺的窗口 = 2026-10-15 05:20** |
| **不会自己起来** | 三条都实测：`/etc/cron*` 无引用、`/etc/systemd/system/timers.target.wants/` 的软链已被 `disable` 移除、无其它 unit 引用它 |
| **回补路径** | 代码**按窗口幂等**（全量覆盖 upsert）⇒ Step 3 落地后跑一次即可补齐缺失日期；**验** = `batches` 行数恢复 + `GET /environment/batches` 的"当前批次"正确（票 12 §7 D 两条） |
| 回滚 | Step 3 落地前**两个 batch 单元文件留在盘上**；要临时顶一下 = `systemctl enable --now mushroom-batch-ingest.timer`（走的是旧通路，只在 Step 3 受阻时用） |

**② 4 个 60 s 缺陷单元文件已清**（备份 → 移出 `/etc/systemd/system` → `daemon-reload`）

| 单元 | 原 sha256 | 处理 |
| --- | --- | --- |
| `mushroom-analysis.service` | `8dc100c9e0ba54706ef6f8cd135d4e32776bc6cc1e1424114d5f6bd09ff8e40f` | 备份后移除 |
| `mushroom-audit-ingest.service` | `3748fef7e936b570d86fdc3c7bc47431cb5524fd3e3e47f71d3f13f6673df750` | 备份后移除 |
| `mushroom-audit-ingest.timer` | `f20e075acdfca5e9fb85e4053291700f0077ae3a90c9ac545934eb8234419cc6` | 备份后移除 |
| `mushroom-env-ingest.service` | `36dd960cbfb0e6ded43e009bfe420f90bcadd80bb7d7989293a2c76032d269b0` | 备份后移除 |

- 备份目录：**`/opt/mushroom-analysis/backups/unitfiles-20261014-102835/`**（4 个文件，逐个 sha256 与原件核对一致）
- 处理后复验：四个 `systemctl is-enabled` 全 **`not-found`**；`list-unit-files | grep mushroom` 只剩 batch 两个
- **单元文本另在票 01 §6 逐字留存** ⇒ 回滚 = 把备份拷回 `/etc/systemd/system/` + `systemctl daemon-reload`
- 隐患消除：不会再出现"谁手一抖 `systemctl start` 一次 ⇒ 60 s 环控鉴权风暴 + 与容器内 audit **重复写 `operations`**"

**③ Step 3 前置三条**（Chief 2026-10-14 定）

1. `analysis/pyproject.toml` 加 `psycopg[binary]>=3.3.2` —— **环境官已完成**（提交 `eddfcf0`）；
2. **`patrol-workspace/uv.lock` 由发版官统一重生成**（共享 artifact，别人不改锁）；
3. 新镜像内自检：`python -c "import psycopg; print(psycopg.__version__)"`。

**④ 宿主源码拷贝 `/opt/mushroom-analysis/src` 必须在 Step 3 落地后消失**

它整目录停在 **10-12 19:07–19:19**（`batches.py` sha `98f39f52…` 仍是 `docker exec tools_mysql`），
是今天**第四次**"仓库 ≠ 生产"、且这次是**合规面**。
**"宿主为什么有一份源码拷贝"这件事本身记在本节**——它是一个未被版本管理、未随镜像走的影子副本。

**⑤ 批次身份映射文件（`analysis.batch_map` 第 1 步，Chief 2026-10-14 派单）**

| 项 | 值 |
| --- | --- |
| 落点 | 仓库 `analysis/data/batch_no_map.json`（= `analysis/batches.py:DEFAULT_MAP_PATH`） |
| 生成方式 | **只读快照 + 跑他们的 CLI**：先 `sqlite3` backup API 从 `file:…?mode=ro` 备份到 `/tmp/mushrooms-snap.db`，再 `python -m analysis.batch_map --db <快照> --out …` |
| **为什么必须走快照** | `analysis/db.py:connect()` 会 `executescript(SCHEMA)` + `migrate()` ⇒ **直接对生产库跑 CLI 会改生产库 schema**，与"只读"和部署节奏都冲突 |
| 结果 | 快照里 `batches` **22 行 / 带 `batch_no` 22 行** ⇒ 映射 **22 条**；`generated_at=2026-10-14T10:47:12`、`source` 自证在文件里 |
| sha256 | `9f69b9bfed6285186a2760341fac4aa61c49da00c8cbcf3d200b33d1b517e6a0`（远端 `cs` 与本地**逐字节一致**） |
| ⚠️ 观察到的事实 | 映射键里有两组是 **`7\|…` 与 `8\|…`**（不是 `607`/`608`）——`batches` 表里这两个库房的 `room_id` 就是 `7`/`8`。**已报环境官**，别当成笔误 |
| ⚠️ `.gitignore` | 仓库根 `.gitignore:93` 的通用 `data/` 会连它一起挡掉 ⇒ 已加**窄例外**（`!analysis/data/` + `!analysis/data/batch_no_map.json`）；实测 `.dockerignore` 的 `data` **不挡**子目录，镜像侧无需改动 |

### 8.3 ⛔ 2026-10-14 analysis 重建**启动即阻塞**：`patrol-workspace/uv.lock` 重生成不了

Chief 2026-10-14 批准"重生成锁 + 只重建 analysis"。**卡在第一步：锁生成失败。**

**复现（两种工具/两套版本都试过）**

```
cd patrol-workspace && uv lock
→ error: Failed to build `deploy @ file:///…/deploy`
     cause: Failed to parse entry: `patrol`
     cause: `patrol` references a workspace in `tool.uv.sources`
            (e.g., `patrol = { workspace = true }`), but is not a workspace member
```

| 试过的组合 | 结果 |
| --- | --- |
| 宿主 `uv 0.12.20` + CPython 3.14.4 | 同样报错 |
| **与镜像同版本** `uv 0.12.10` + Python 3.12.14（一次性容器，挂载仓库） | **同样报错** |

**排除过的两个嫌疑（都实测证伪）**

| 嫌疑 | 处置 | 结果 |
| --- | --- | --- |
| `patrol/uv.lock`（**未跟踪**，10-09 10:05 遗留，17 762 B，sha256 `a259de46…`）让 uv 把 `patrol` 当成独立 workspace 根 | 已挪到 `.scratch/prod-deploy/stray-patrol-uv.lock-20261009-100545`（**保留、未删**） | **仍然报错**，不是它 |
| `analysis/uv.lock`（**已跟踪**，`5658570` 引入）让 `analysis` 变成独立根 | 临时移出后重跑，再原样还原 | **仍然报错**，不是它 |

**现状**：`[tool.uv.workspace] members = ["../patrol", "../deploy", "../measure", "../analysis"]`
（根 `pyproject.toml` **没有** `[tool.uv.workspace]`，四个成员目录里只有 `analysis/` 自带锁）。
`deploy/pyproject.toml:11-12` 有 `patrol = { workspace = true }`。
现有锁里明明有 `source = { editable = "../patrol" }` ⇒ **它是能用的（`uv sync --frozen` 在镜像构建里一直成功），但已经生成不出来了。**

**⇒ 影响**：`analysis/pyproject.toml` 里的 `psycopg[binary]>=3.3.2` **进不了 `patrol-workspace/uv.lock`**，
所以"镜像装 psycopg"这条前置在锁修好之前**做不到**。需要 workspace 布局的归属方（或 Chief）定：改 `members` 写法 / 把 workspace 根挪到仓库根 / 让成员目录不再各自留锁。

**同日另一条实测（与 8.3 无关但影响"镜像内字节"判据）**：工作区文件是 **CRLF**、git blob 是 **LF**，
而镜像一直是从**工作区**构建的 ⇒ **镜像内文件字节 = 工作区字节 ≠ git blob**。
实证：`patrol/src/patrol/round.py` 工作区 `5dc9a4cb…`（14 547 B）= **镜像内** `5dc9a4cb…`，而 blob 是 `87a1b31d…`（14 288 B）。
⇒ 以后核"镜像内 = 仓库"**要么比工作区、要么先把 `\r` 去掉**，直接比 blob sha256 一定不等。

### 8.4 票 12 §7 验收清单留痕（发版官 2026-10-14 10:26 只读实测）

> 口径说明：Chief 口头说的"票 12 §8 清单"，在文件里实际是 **`env-analysis/issues/12` §7. 验收**。

**A. 容器活着**

- [x] `mushroom_analysis` **healthy**；`:8000/healthz` = **200**
- [ ] `GET /ranges` = **`[]`** —— **旧镜像的口径**；§8.3 的重建落地后才该是信封，届时按新口径重验
- [x] `/rounds?limit=1` = 200（返回 10-14 09:27 那轮）；`rounds` **73** 行 / `images` **3491** 行，逐轮未见跳变

**B. 作业真的在跑**

- [x] 容器日志有 `[ingest] 启动作业 env（每 300.0s）`、`[ingest] 启动作业 audit（每 600.0s）`、`[audit_ingest] 0 条`（上游静默）
- [x] `environment` **2596 行**，`MAX(ts) = 2026-10-14T10:00:00`（推进到当前小时）
- [x] `operations` **50 行**，`MAX(ts) = 2026-10-14T09:55:00`
- [x] 环控登录：容器日志里 `EMS|login|鉴权` 命中 **0 行**（对照旧缺陷 2466 次/24 h）
- [ ] ⚠️ **不满足**：**环境变量含明文口令** —— `docker inspect` 的 `Config.Env` 里 `EMS_URL` / `EMS_ACCOUNT` / `EMS_PASSWORD_MD5` 都在（`env_file:` 在**创建时**注入的必然代价）；`/etc/mushroom/` **没有**挂进容器。
  与 §2 与 §7 里"值不进 compose、不进 `docker inspect`"的写法冲突 ⇒ 要么明确接受（宿主 root 可见，与 systemd 时代 `EnvironmentFile` 同级），要么另开票走 docker secret。

**C. 幂等与重启**

- [~] 重启幂等：本轮**没有重启**容器，未单独验证；`environment` 以 `(ts, zone)` 为 PK，未见重复行
- [-] "未装 pandas 的镜像里 API 能启动"：**不适用** —— `PATROL_IMAGE` 里**有** pandas（容器内 `import pandas` 成功）。这条针对的是宿主 venv 形态

**D. 过渡期（batch 暂停）**

- [x] 容器里**没有** batch 作业（日志里 `batch` 只有 3 条 `GET /environment/batches` 的 HTTP 访问记录，无 `batch_ingest` 运行行）
- [x] 补跑一次：待 Step 3（见 §8.2 ③ / §8.3）

**同日补测（支撑票 06 的 A′ 判决）**：容器内 `psycopg` = `ModuleNotFoundError`；
`sqlalchemy` / `redis` / `loguru` / `dynaconf` / `pgvector` / `sqlalchemy_utils` / `tenacity` / `mlflow` **全部不存在**。

### 8.5 2026-10-14 11:34–11:36：analysis 重建落地（Chief 批 (c)：用现有锁先发）

| 项 | 值 |
| --- | --- |
| revision | **`46ca2f2`**（门禁跑在 `3925e89`；两者是**快进**，中间只有 1 个 docs-only 提交） |
| 门禁 | **1069 passed / 0 failed**，6 分 04 秒（`patrol-workspace` 的 `.venv`，构建点干净） |
| 新镜像 | `registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0-20261014112224-46ca2f2` |
| image ID | `sha256:65f433472d72153fead97466585a913e11511c10cf7d881771279bd8f6e945e8`，748,057,905 B |
| registry digest | `sha256:c1e3ded70b03ffec9d95ad243d78d43395fbb6a8035f567fdf3448799419bac1` |
| 构建耗时 | **29 s**（层缓存命中，只有 COPY + `uv sync` 段重跑） |
| 锁 | `patrol-workspace/uv.lock` sha256 `4bdbde21…`，**本次未重生成**（重生成被 §8.3 阻塞） |

**镜像内自查（"生效"判据）**

- `import psycopg` → **`ModuleNotFoundError`**（**如期**，锁里没有它）✓
- 信封代码在：`never_computed` / `computed_at` / `ranges_state` 命中 `analysis/src/analysis/{api,db,align}.py`；`RANGES_AT` 命中 `jobs.py` ✓
- **revision 归属**：逐文件比对 5 个样本（`analysis/…/api.py`、`db.py`、`deploy/…/console.py`、`patrol/…/round.py`、`measure/…/production.py`），
  **镜像内字节去掉 `\r` 后 = `46ca2f2` 的 blob**，**5/5 一致** ✓（CRLF 口径见 §8.3 末段）

**发布动作**（11:34:51 起；动手前 `GET /api/status` = `patrol.active=false` / `machine.state=IDLE` ⇒ 无轮次在飞）

1. DB 备份：`PRAGMA wal_checkpoint(TRUNCATE)` → `(0, 0, 0)`；落 `/opt/mushroom-analysis/backups/pre-ranges-20261014-113451/mushrooms.db`（6,971,392 B，sha256 `fbda5ef6a22894c68a67429ffbcd7c2fe2121fad62c3c865dbcb14c6d99b2f34`）
2. 备份 `mushroom_solution.yml`（`811da3c6…`）与 `.env`（`375b8d35…`）→ `*.bak-ranges-20261014-113451`
3. compose 加 `RANGES_AT=05:10`（插在 `AUDIT_INTERVAL=600` 之后，带注释；`docker compose -f … config` = **OK**）
4. `.env` 的 `PATROL_IMAGE` → 新 tag
5. `docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_analysis` → **11:35:13** 重建完成

**验收（11:35–11:36 实测）**

- `GET :8000/ranges` → **信封**，键 = `ok / rows / count / state / state_text / detail / computed_at / metric / unit`（**不再是裸 `[]`**）✓
- `ranges_state` 表**出现**，**1 行**：`computed_at = 2026-10-14T11:35:18`、`state = no_measurements`、`n_rows = 0` ✓
- 日志：`[ingest] 启动作业 ranges（每天 05:10）`、`[ranges] 重算完成，0 行区间` ✓（**启动即跑了一次**，不是干等到 05:10）
- 迁移核：表多了 `ranges_state`；`rounds` **73** / `images` **3491** 与重建前**逐条相等**；`environment` **2600**（重建前 2596，仍在增长）
- 健康：analysis `/healthz` 200、console `/api/rounds` 200、页面 8002 200、**六容器 healthy**
- **patrol / console 未动**，仍是 `89e4a54` ✓

**⚠️ 两处与预期的差异（如实记）**

1. **空态是 `no_measurements`，不是 `never_computed`**：Chief 的验收词是"库空时是 `never_computed`"。
   实际 `state=no_measurements`、`state_text="样本不足：库房里还没有任何测量行（测量管线未接上）"`、`detail.n_environment=2600`。
   ⇒ `ranges_state` **已有行**、库里**有环境数据**、只是 **0 行测量** ⇒ 落到"样本不足"这一档；
   `never_computed` 应对应"从未重算过"。**信封与状态机是工作的**，只是这一档的名字与预期不同 —— 请票 05 归属方确认口径。
2. `GET http://127.0.0.1:8002/api/ranges` = **404**：页面 nginx 的 `/api` 只反代到 **console:8001**，
   而 `/ranges` 是 **analysis:8000** 的路由（不带 `/api` 前缀）。页面若要直接读 ranges，需 console 侧出路由或页面直连 8000。
   **发布前后都如此，不是本次缺陷**，记一笔备查。

**回滚路径**

```bash
cd /home/sysadmin/algorithm/mushroom_service
cp -a mushroom_solution.yml.bak-ranges-20261014-113451 mushroom_solution.yml
cp -a .env.bak-ranges-20261014-113451 .env          # ← 指回 89e4a54
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_analysis
# DB：/opt/mushroom-analysis/backups/pre-ranges-20261014-113451/
```

⚠️ `.env` 的 `PATROL_IMAGE` 现在指向新 tag，而 patrol/console 容器**仍在跑 `89e4a54`**（容器不会自己跟着变）。
**这是已知且预期的漂移**；下次谁对 patrol/console 执行 `up -d` 会把它们带上来 —— 那一步才是三件套再次统一。

**未做的一件事（要说明）**：Chief 说"DSN 文件与 `:ro` 挂载随这次 analysis 重建一起加"——**我没有加**。
原因：宿主 `/etc/mushroom/` 现在**只有 `ems.env`**，`pgsql.dsn` 还不存在；**docker 对不存在的 bind 源会把该路径建成目录**，
反而让代码读到目录而报错，还会在宿主留一个空目录。⇒ 等 DSN 文件（环境官提供、值不经过我）落到
`/etc/mushroom/pgsql.dsn` 0600 之后，随下一次重建加挂载。

## Comments

- 2026-10-13 发版官写票。全部数字来自现场只读实测（plink 只读，无 pull/重启/改文件）。
  方案要点：**净新增 1 个 service**、复用现役镜像与已有 `api` 角色、DB 原地挂载、
  `.env` 零改动、回滚一条命令。
- 2026-10-13 Chief 六条裁决落定（见 §8），方案获批。

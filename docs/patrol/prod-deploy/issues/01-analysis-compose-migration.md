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
| 1 | **batch-ingest 的 MySQL 通路**（`analysis/batches.py:56-59` 走 `docker exec tools_mysql mysql …`，口令只在 `tools_mysql` 的 env，刻意不外搬） | **目标 (b)：改成直连 MySQL 只读账号**（代码改动，凭据走挂载 env）；**过渡期 (c)：暂不迁 batch-ingest**。<br>⚠️ **永远不挂 `docker.sock`**：那个容器同时是对外 `:8000` API，挂 sock 等于给对外端口配 root 等价权限。**原方案里"(a) 若必须先上"的 fallback 已删除，不留活口。** |
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
2. **`batch-ingest`：目标 (b) 直连 MySQL 只读账号，过渡期 (c)**（迁移当天不迁它，见 §4.1）。
   **绝不给 API 容器挂 `docker.sock`**——对外 :8000 + root 等价权限，红线。
   **原 (a) fallback 已从本票删除，不留活口。** 环境官开票改直连，(b) 落地后补跑一次把窗口
   补齐（`batch_ingest` 按窗口幂等，缺几天可回补）。
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

**已落盘的相关票**：`02-preview-channel-stability.md`（归属巡检手）、
`03-tdengine-6041-exposure.md`（平台侧，我们不动）。

## Comments

- 2026-10-13 发版官写票。全部数字来自现场只读实测（plink 只读，无 pull/重启/改文件）。
  方案要点：**净新增 1 个 service**、复用现役镜像与已有 `api` 角色、DB 原地挂载、
  `.env` 零改动、回滚一条命令。
- 2026-10-13 Chief 六条裁决落定（见 §8），方案获批。

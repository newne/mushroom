# 01 — analysis 侧 6 个 systemd 单元 → compose（迁移方案）

**归属：** compose 定义 / 镜像 / 发布动作 / 回滚 = 发版官；启动**语义** = 环境官（票 12）
**形态红线（用户 2026-10-13）：** 发版一律走 **docker compose**，**严禁 systemd**。
**用户追加约束（2026-10-13）：** **非必要不要增加 compose service。**
**Blocked by:** 票 12（`../env-analysis/issues/12-analysis-service-startup-contract.md`）
**Status:** ready-for-review（等 Chief 批 + 三处裁决）

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

**入口怎么把凭据交给进程**（供票 12 对齐）：`api` 角色在 `exec uvicorn` 之前
`set -a; . "${EMS_ENV_FILE:-/run/ems.env}"; set +a`。文件本身 0600 root:root，容器以 root 跑。

## 3. 三处必须裁的点

| # | 事项 | 选项 | 发版官建议 |
| --- | --- | --- | --- |
| 1 | **batch-ingest 的 MySQL 通路**：`analysis/batches.py:56-59` 走 `docker exec tools_mysql mysql …`（口令只在 `tools_mysql` 的 env，刻意不外搬） | (a) 给这个容器挂 `/var/run/docker.sock` + 镜像加 docker CLI（sock = root 权限，**不推荐**）；(b) 改成直连 MySQL 只读账号（代码改动，凭据走挂载 env，**目标**）；(c) 每日作业暂不上，等 (b) | **(b) 为目标；若(a)必须先上，迁移票里写明"票 12 落地后立刻撤掉 sock"** |
| 2 | **端口 8000 的对外方式** | (a) `publish 8000:8000`，`.env` 的 `PROD_INGEST`/`PATROL_ANALYSIS`/`PATROL_INGEST` 全不改；(b) 只走 compose DNS `http://mushroom_analysis:8000`，要改三个 `.env` 键 | **(a)**；(b) 单开后续票 |
| 3 | **`mushrooms.db` 落点** | (a) 挂宿主原目录 `/opt/mushroom-analysis`（路径不变）；(b) 搬到 `../mushroom_analysis/data`（更"干净"，要搬数据） | **(a)**，回滚最快 |

## 4. 迁移顺序（原子切换，窗口 ≈30 s）

1. 前置：`wal_checkpoint(TRUNCATE)` + 备份 DB（含 `-wal`/`-shm`）；备份 `.env` 与
   `mushroom_solution.yml`；本地构建/推送新 tag（**若需**）。
2. `systemctl disable --now mushroom-analysis mushroom-env-ingest \
   mushroom-audit-ingest.service mushroom-audit-ingest.timer \
   mushroom-batch-ingest.service mushroom-batch-ingest.timer`
   （**只停不删**，unit 文件留盘作回滚点；`disable` 保证机器重启不复活）
3. `docker compose -f mushroom_solution.yml --profile patrol up -d mushroom_analysis`
4. 验收（缺一不可）：
   - 容器 `healthy`；`:8000/healthz` 200
   - **`/rounds`、`/images` 行数与切前逐条相等**（"还是那一份 DB"的判据）
   - `ss -lntp` 里 8000 的属主由 `uvicorn` 变 `docker-proxy`
   - 经 `:8002` 的 `/api/environment`、`/api/images`、`/api/room` 仍 200
   - env-ingest 出小时聚合日志；**audit 的环控登录 ≤ 1 次/10 分钟**（票 12 §7）
   - `data/runs/` 无新增（巡检侧零接触）
5. 回滚：`docker compose … stop mushroom_analysis` → `systemctl enable --now <6 个>`
   （硬前提：同一时刻只有一边在写 DB）

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
2. **`batch-ingest`：目标 (b) 直连 MySQL 只读账号，过渡期 (c)**（迁移当天不迁它）。
   **绝不给 API 容器挂 `docker.sock`**——对外 :8000 + root 等价权限，红线。
   环境官开票改直连，(b) 落地后补跑一次把窗口补齐（`batch_ingest` 按窗口幂等，缺几天可回补）。
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
不会跑。⇒ 迁移第一步的**真正前置**是「票 12 代码 + 重建巡检镜像」，不是 compose 文件。

**已落盘的相关票**：`02-preview-channel-stability.md`（归属巡检手）、
`03-tdengine-6041-exposure.md`（平台侧，我们不动）。

## Comments

- 2026-10-13 发版官写票。全部数字来自现场只读实测（plink 只读，无 pull/重启/改文件）。
  方案要点：**净新增 1 个 service**、复用现役镜像与已有 `api` 角色、DB 原地挂载、
  `.env` 零改动、回滚一条命令。
- 2026-10-13 Chief 六条裁决落定（见 §8），方案获批。

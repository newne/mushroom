# 12 — analysis 侧四个作业的启动契约（容器化 compose 形态）

**Spec:** ../spec.md §4；ADR-0023（小时聚合与逐条操作日志）

**归属：** 启动**语义**（本票）= 环境官；compose 定义、镜像、发布动作 = 发版官。
**形态红线（用户 2026-10-13）：** 发版一律走 docker compose，**严禁 systemd**。
线上现役 systemd 单元文本由发版官逐字转录落盘，只作历史参考，不部署。

**Blocked by:** 无

**Status:** ready-for-agent（发版官据此写 compose；§6 的未知项需现场核对后回填）

**What to build:** 把 analysis 侧四个作业（API / 环境入库 / 操作日志入库 / 批次台账同步）
的启动语义写死，使发版官能照着出 compose 定义：每个作业怎么跑、什么周期、
要哪些环境变量与挂载、游标与幂等怎么算、失败重试与日志落哪里。

## 0. 三条不变式

1. **一个 SQLite 文件、四个读写者**：三个 ingest 与 API 共享同一个 `mushrooms.db`（WAL）。
   必须挂同一个卷、用同一个 `--db` 路径；不许各自建库。
2. **不许拿"进程退出 + 重启"当调度器**。线上 `mushroom-audit-ingest.service` 的
   `Type=simple` + `--once` + `Restart=always` 让退出码 0 也每 60 s 重启一次
   （24 h 内 Started/Deactivated 共 2466 次，发版官实测），而设计是每 10 分钟。
   它每轮都要调环控平台的**登录鉴权**接口拿 token，等于持续给别人系统的接口加压。
   新形态：进程内循环（`loop`），**不许** `--once` + restart 组合。
3. **任何进 prod 容器的模块不许模块级 `import pandas`/`numpy`**（prod 现役 venv 没有它们；
   曾因此把服务打挂 4 分钟）。现役 API 不在这个坑里：`analysis/api.py` 不 import `align`
   （只有 `align.py:11` 自己用 pandas，`environment.py:6` 注释明确"刻意不依赖 pandas"）。

## 1. 四个作业的启动契约（最终形态：**1 个容器**）

| 作业 | 在哪儿跑 | 周期 | 依据 |
| --- | --- | --- | --- |
| **analysis API** | 容器主进程 `uvicorn analysis.api:create_app --factory --host 0.0.0.0 --port 8000` | 常驻 | `deploy/systemd/mushroom-analysis.service:15-16`；**单 worker 硬约束**（`create_app` 建一个 SQLite 连接，多 worker 各持一份、写入互相看不见） |
| **环境入库** | **同一进程**：`analysis.api` 的 lifespan 里的常驻 asyncio 任务（`analysis/jobs.py`） | `ENV_INGEST_INTERVAL`（生产 300 s） | 线上每 300 s（`issues/01:22`） |
| **操作日志入库** | 同上 | `AUDIT_INTERVAL`（生产 600 s） | 设计每 10 分钟（`issues/09:23`）；线上缺陷见 §0.2 |
| **批次台账同步** | 同上（**过渡期不装配**，见 §1.1） | `BATCH_AT`（生产 `05:20`） | 线上 `mushroom-batch-ingest.timer` 每天 05:20 |

**为什么同容器、定时落 lifespan（Chief 2026-10-13 定稿）：** 净新增只有 **1 个** compose service
（用户红线：非必要不加 service）；四个作业共享同一个 SQLite、同一套凭据、同一条 import 链；
`patrol-entrypoint.sh` 不加角色；不引 cron/supercronic/supervisor。

**开关语义：** `ENV_INGEST_INTERVAL` / `AUDIT_INTERVAL` / `BATCH_AT` **设了才跑**，
不设就安静（开发与测试默认无后台请求），实现见 `analysis/jobs.py` 的 `default_jobs`。
API 的 db 路径缺省取 `ANALYSIS_DB`（容器里显式给，**不靠工作目录**）。
凭据不齐（`EMS_ACCOUNT`/`EMS_PASSWORD_MD5` 缺）时**不装配 audit 作业** —— 拿空口令去打
环控平台只是白挨一次 401。

**"不许拿 restart 当调度"（不变式）：** 每个作业是**进程内循环**：先跑一轮，再睡一个周期
（`analysis/jobs.py:run_job`），不是"进程退出 + 容器重启"。循环体吞掉 `Exception`，
单轮失败只打印、等下一周期。线上 audit 单元 `Type=simple` + `--once` + `Restart=always`
把 10 分钟退化成 60 秒一次（24 h 内 2466 次 Started/Deactivated），还持续打对方的登录接口
—— 这次迁移就是要去掉它。

**代价，写清楚：** 作业由 API 进程承载 ⇒ ① API 被 kill（OOM/SIGKILL）时作业一起停，
由 compose 的 `restart` 拉起**整个容器**（可接受：主进程本来就是 API）；
② 单个 asyncio 任务若死掉没有 supervisor 复活，靠"循环体永不退出"这条纪律兜。
**不为此引入 supervisor**（最少新机制）。

### 1.1 批次台账同步：过渡期暂停（Chief 2026-10-13 裁；2026-10-14 改口径）

~~`analysis.batch_ingest` 今天走 `docker exec tools_mysql mysql …` 读台账~~ ——
**旧通路已下线**：用户 2026-10-14 裁定 `tools_mysql` 是业务数据库、**禁止使用**，
"给容器挂 `docker.sock`"那条方案一并作废（给对外的 :8000 服务配 root 等价权限）。故：

- 迁移当天**不装配**这条作业：compose 不设 `BATCH_AT`，`default_jobs` 直接跳过它。
- 目标形态 = **pgsql 取数**（`postgres_db` / 库 `mushroom_algorithm`，DSN 走挂载 env，
  `batches.py` 取 `mushroom_batch_yield ∪ image_text_quality`），见票 13
  （文件名已改为 `13-batch-ledger-pgsql.md`）。
- 落地后**补跑一次**把窗口补齐：全量覆盖 upsert（PK `(room_id, entry_date)`）⇒ 缺几天都能回补。
- 过渡期台账停在迁移前那份快照：`window_of` 的"当前批次"会按过期台账算，
  所以界面与接口必须能显示"数据止于 X"（spec §6 的口径已经这么要求）。

**`batch_ingest.py` 的常驻 loop 已经实现**（`--interval` / `--daily-at HH:MM`，
启动先跑一次、之后按 05:20 对齐），`default_jobs` 通过 `BATCH_AT` 复用它 ——
过渡期结束后只需在 compose 加一行环境变量，不用再改代码。

## 2. 环境变量与挂载

| 项 | 值 | 依据 |
| --- | --- | --- |
| `TDENGINE_URL` | TDengine REST 根，如 `http://<host>:6041`（默认代码里是 `http://127.0.0.1:6041`） | `analysis/api.py:191-196`、`analysis/env/ingest.py:55-59` |
| `TDENGINE_USER` / `TDENGINE_PASSWORD` | 默认 `root` / `taosdata` | 同上 |
| `EMS_URL` | 环控平台根地址（默认 `http://10.77.77.39`） | `analysis/audit_ingest.py:22,46` |
| `EMS_ACCOUNT` / `EMS_PASSWORD_MD5` | 账号与**MD5**口令（明文会回 400200） | `analysis/audit_ingest.py:9-10,46-48` |
| `PYTHONPATH` | 必须含 `src`（容器镜像若是 install 过的包则不需要） | `deploy/systemd/mushroom-analysis.service:12` |
| **pgsql 驱动** | 容器里跑 batch 作业需要 `psycopg`：`analysis/pyproject.toml` 已声明 `psycopg[binary]>=3.3.2`（2026-10-14，票 13）；**锁由发版官统一重生成**（`patrol-workspace/uv.lock` 是共享 artifact） | 票 13 §3.3 |
| 凭据来源 | 线上 `EnvironmentFile=/etc/mushroom/ems.env`（不进仓库） | `issues/09:23` |
| **数据库** | 三个 ingest + API 共享同一文件，必须显式 `--db` + 挂载卷 | systemd 形态靠 `WorkingDirectory=/opt/mushroom-analysis` + 相对默认路径 `mushrooms.db` 决定落点（unit :9-11 注释）；**容器里不能靠工作目录，必须显式路径** |

**凭据不写进 compose 明文、不写进仓库**；容器形态的等价物是 `env_file:` 指向宿主上的
`/etc/mushroom/ems.env`（或 docker secret）。**该文件里的实际变量名未知**，见 §6。

## 3. 游标语义与"整点重算当前小时"

- **环境入库有游标，且是进程内内存变量**（`ingest.py:39-47` 的 `since`）。
  每轮结束后游标 = `hour_start(max(ts))`（`ingest.py:21-35`）——**退到整点**，
  于是"当前这一小时"每轮都从头重算（最多 60 行）。
  理由：只拉"游标之后"的新增行，会把当前小时的均值**算小**（`ingest.py:28-30` 注释）。
- **重启语义**：内存游标随进程消失 ⇒ 首轮无游标，`--lookback` 默认回溯 **120 分钟**
  （`ingest.py:60`）。因此容器重启的代价 = 重算最近 120 分钟覆盖到的小时，
  **不是数据缺口**。幂等由 PK `(ts, zone)` 的 `INSERT OR REPLACE` 保证
  （`db.py:213-222`）：重复跑不翻倍、不产生第二行。
- **操作日志没有游标**：每轮独立拉 `[now - lookback, now]`，`lookback` 默认 90 分钟
  （`audit_ingest.py:31-40`）。窗口重叠靠 PK `(ts, device_code, point_code)` 覆盖去重
  （`db.py:264-272`）。⇒ 周期越长，重叠浪费越少、对环控平台的鉴权调用越少；
  600 s 是设计值。
- **批次没有游标**：全量拉台账、按 PK `(room_id, entry_date)` 覆盖（`db.py:305-312`）。
- **明确否证**：批次边界**不得**用"按断档切段"（真机证否，`environment.py:243-247`、
  `db.py:99`）。台账是唯一权威。

## 4. 失败重试与日志落点

- 环境入库与操作日志入库的循环都是 `try/except` + `print` + `sleep`：
  **单轮失败不中断循环**（`ingest.py:42-45`、`audit_ingest.py:63-70`）。
  失败信息只进日志，没有告警、没有退避（这是现状，不是设计背书）。
- API 无内建重试 ⇒ 常驻 + 容器 `restart` 策略；单 worker 硬约束不变。
- 日志：三个作业都只 `print` 到 stdout/stderr，**没有文件日志、没有轮转**。
  容器形态下即 `docker logs`。json-file 轮转大小/保留数 **未知，待发版官定**。
- **必须修掉的缺陷**（否则迁移等于把 bug 搬过去）：audit 不许 `--once` + `Restart=always`；
  同理 env 也不许出现分钟级重启。判据见 §7 第 3 条。

## 5. 与仓库 / 线上现状的差

- 仓库里只有 `deploy/systemd/mushroom-analysis.service`；`env-ingest`、`audit-ingest`、
  `batch-ingest` 的单元**不在仓库**（证据只在 `issues/01:22`、`issues/09:23` 的评论里）。
- 今发版官只读盘点：线上共 **5 个**不在仓库的单元 —— `mushroom-env-ingest.service`（active，每 300 s）、
  `mushroom-audit-ingest.service` + `.timer`、`mushroom-batch-ingest.service` + `.timer`（每天 05:20）。
- 库房主机与算法侧**同机**（`ycfserver`；`eno1 192.168.1.250` / `tun0 10.77.77.39`，
  同属 compose 项目 `mushroom_service`）；console 已反代 `/api/environment` 等
  （`console.py:910-944`）。容器网络里 analysis 的服务名/端口映射 **未知**。
- 端口现状：analysis API `:8000`（unit :16），console 后端 `:8001`，巡检台页面 `:8002`。

## 6. 未知 / 待现场核对（**不许猜**）

1. `/etc/mushroom/ems.env` 里的**实际变量名**：是否就是 `EMS_ACCOUNT` / `EMS_PASSWORD_MD5`。
2. 线上 `mushrooms.db` 的**绝对路径**（看 systemd 单元的 `WorkingDirectory` 与 `--db` 实参）。
3. `mushroom-env-ingest.service` 的**确切 ExecStart**（是否带 `--source tdengine --url … --rooms …`）。
4. `mushroom-batch-ingest.service` 的 ExecStart，以及"05:20"的时区基准（宿主 local 还是 UTC）。
5. TDengine 的容器名/网络别名（`tools_taos`?）与 6041 是否只在宿主监听 —— 决定 env-ingest 的 `--url`。
6. prod 镜像里**是否装 pandas**。现役 API 不需要；但票 05 的 `refresh_ranges` 需要
   （`align.py:11`）。**决定点**：每日区间重算若进 API 容器，会引入 pandas —— 要么镜像加依赖，
   要么单独一个批处理容器。
7. ~~容器内定时器的实现（cron / supercronic / 外部调度）~~ 已定：batch-ingest 走**常驻 loop**
   （§1 取舍表），三个 ingest 同构；若发版官有异议再议。

## 6.1 现场回填（发版官，2026-10-13 只读实测）

> §6 的 7 条**已全部回填**（含第 7 条的选型点头）。以下每条都带取证方式，没有推断；
> 凭据值一律未取、未落盘。

| # | §6 的问题 | 实测答案 | 取证方式 |
| --- | --- | --- | --- |
| 1 | `ems.env` 实际变量名 | **`EMS_URL` / `EMS_ACCOUNT` / `EMS_PASSWORD_MD5`**（只读键名，值未取）。文件 `/etc/mushroom/ems.env`，`0600 root:root`，101 B | `sed -e 's/=.*/=<hidden>/' /etc/mushroom/ems.env` + `stat` |
| 2 | `mushrooms.db` 绝对路径 | **`/opt/mushroom-analysis/mushrooms.db`**，6,971,392 B；`PRAGMA journal_mode = wal`；同目录另有 `-wal` 6.3 MB 与 `-shm` ⇒ **切换前必须 `wal_checkpoint(TRUNCATE)` 再备份**，直接拷 `.db` 会丢这一段 | `ls -l` + python sqlite3 只读打开 |
| 3 | env-ingest 确切 ExecStart | `/opt/mushroom-analysis/.venv/bin/python -m analysis.env.ingest --db mushrooms.db --source tdengine --url http://127.0.0.1:6041 --interval 300`；`WorkingDirectory=/opt/mushroom-analysis`、`PYTHONPATH=/opt/mushroom-analysis/src`、`Restart=always` / `RestartSec=15`。**没有 `--rooms`** | 线上 `/etc/systemd/system/mushroom-env-ingest.service`，文件 sha256 `36dd960c…`（553 B） |
| 4 | batch-ingest ExecStart + 05:20 时区基准 | `/opt/mushroom-analysis/.venv/bin/python -m analysis.batch_ingest --db mushrooms.db --once`；timer `OnCalendar=*-*-* 05:20:00` + `Persistent=true`。**时区 = 宿主本地 `Asia/Shanghai` (CST, +0800)**：`timedatectl` 实测 `Time zone: Asia/Shanghai (CST, +0800)`、`System clock synchronized: yes`、`NTP service: active`、`RTC in local TZ: no` ⇒ 线上的 05:20 **就是 CST 05:20** | 线上 unit，sha256 `0b7a497b…` / `e2ed4109…`；`timedatectl` |
| 5 | TDengine 容器名 / 6041 监听范围 | 容器名 **`tools_taos`**（`tdengine/tdengine:latest`，Docker 网络 `tools_tool-network`）。**6041 不是只在宿主监听**：`0.0.0.0:6041->6041` 且 `[::]:6041`（docker-proxy pid 5305/5311）；而 6030–6040 的 TCP/UDP 只绑 `172.17.0.1`。⇒ 容器里用 `http://172.17.0.1:6041` 可达（已从 patrol 容器实测连通，见 §Comments）。**6041 目前对 LAN/VPN 开放**——要不要收紧是独立事项，建议单开票，本次迁移不改它 | `docker ps` / `ss -lntp` / `docker inspect tools_taos` |
| 6 | prod 镜像是否装 pandas | **装了**：现役 `PATROL_IMAGE` 镜像内 `python 3.12.14` + **`pandas 3.0.5`** + `fastapi 0.141.1` + `uvicorn 0.52.4`（`import` 实测通过）。**宿主 `/opt/mushroom-analysis/.venv` 没有 pandas**（`ModuleNotFoundError: No module named 'pandas'`）。⇒ 迁移后 §0.3 从"硬约束"变成"口径约束"：`align.py` 在容器里**可用**，但建议仍保持 API 启动链不依赖 pandas | `docker exec mushroom_patrol python3 -c "import pandas,fastapi,uvicorn"`；宿主 venv 同命令 |
| 7 | 容器内定时器 | **维持本票 §1 的选择：常驻 loop**（`--interval` / `--daily-at`）。发版官**无异议**，同意按 (b) 实现，`compose` 里三个 ingest 写法一致；**不许拿 restart 当调度**这条照旧 | 本回填 |

**对 compose 定义的直接影响（发版官据此写）**：

- 三个写者 + API 都显式 `--db /opt/mushroom-analysis/mushrooms.db`，挂同一个宿主目录卷
  （`/opt/mushroom-analysis:/opt/mushroom-analysis`）——**靠卷而不是工作目录**。
- `TDENGINE_URL=http://172.17.0.1:6041`（容器里 127.0.0.1 不是宿主）。
- `BATCH_AT=05:20` 的时区基准 = **CST**；compose 基模板已设 `TZ=Asia/Shanghai`，与线上一致。
- 凭据：`EMS_URL` / `EMS_ACCOUNT` / `EMS_PASSWORD_MD5` 三个键名已确认，容器形态用
  挂载 `/etc/mushroom/ems.env:ro` + 入口 `set -a; . <file>; set +a`（值不进 compose、不进
  `docker inspect` 的 `Config.Env`；比 `env_file:` 少一次明文暴露面）。

## 7. 验收

**A. "容器活着"**（**不等于**作业在跑，别拿这组当作业证据）

- [ ] 容器 `healthy`，`:8000/healthz` 200
- [ ] `GET /ranges` 能读到票 05 批处理写的结果
- [ ] `/rounds`、`/images` 行数与迁移前逐条相等（这是"还是那一份 DB"的判据）

**B. "作业真的在跑"**（三条独立证据，缺一条不算过）

- [ ] `docker logs mushroom_analysis` 有 `[ingest] 启动作业 env（每 300.0s）`、`audit（每 600.0s）`
- [ ] **`environment` 有增长**：连续观察 ≥ 2 个 env 周期（≥10 分钟），
      `SELECT COUNT(*), MAX(ts) FROM environment` 的 `MAX(ts)` 推进到当前小时、行数不减
- [ ] **`operations` 有增长**：连续观察 ≥ 2 个 audit 周期（≥20 分钟），行数增长
      或日志出现 `[audit_ingest] N 条`
- [ ] **环控平台登录调用 ≤ 1 次/10 分钟**（对照线上 2466 次/24 h 的缺陷）
- [ ] 环境变量不含明文口令（挂载 + source；值不进 compose，也不进 `docker inspect` 的 `Config.Env`）

**C. 幂等与重启**

- [ ] 重启容器后：`environment` 无重复行（PK `(ts, zone)`），当前小时被重算
- [ ] 在**未装 pandas** 的镜像里 API 能启动（import 链无 pandas）

**D. 过渡期（batch-ingest 暂停）**

- [ ] 不设 `BATCH_AT` 时容器里**没有** batch 作业（日志无该行、`batches` 表不更新）
- [ ] 票 13 落地后**补跑一次**：`batches` 行数恢复，`GET /environment/batches` 的"当前批次"正确
      （全量覆盖 upsert，缺几天可回补）

## Comments

- 2026-10-13 写票。Chief 三轮口径合并而成：先是"systemd 单元启动语义"，随后按用户红线改为
  **容器化 compose 语义**（严禁 systemd）。依据只取线上实测（发版官只读盘点）与
  `issues/01:22`、`issues/09:23` 以及仓库代码；缺证据的一律进 §6 标"未知"。
  本票**不出 docker 文件**、不碰 systemd 落盘文本（发版官独占），只出语义契约。

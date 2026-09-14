# 库房主机部署与运行（M1 巡检 + 站位示教）

本目录是**部署侧**（`deploy` 包）：把纯逻辑库 `patrol` 接到真实世界——真实 HTTP、
厂商 SDK、时刻表。patrol 自身保持**库内零网络调用**（见 `patrol/links.py` 的 docstring），
所以网络实现只在这里。

本文是库房主机（`10.77.77.39`，Ubuntu 24.04 / x86_64 / Python 3.12）上的操作手册。
现场环境与实测结论见 `docs/patrol/prod-deploy/gap-list.md` 与 `docs/adr/`。

---

## 1. 前置条件（缺一不可）

| # | 条件 | 怎么核对 |
| --- | --- | --- |
| 1 | 厂商 SDK 在机器上 | `FMC4030_LIB_PATH=/opt/mushroom-patrol/lib/libFMC4030_2009_1.so`，`ldd` 干净 |
| 2 | 控制器可达且**软限位已整定** | `patrol-debug` 的 `para` 命令；Y 必须是 `[0, 4495]`（ADR-0008）。未整定时 M1 **拒绝启动** |
| 3 | 采图服务在 `127.0.0.1:7003` 且 `ready:true` | `curl -s 127.0.0.1:7003/healthz` —— ⚠️ **只看 `ready`，`ok:true` 骗人**（ADR-0009） |
| 4 | 站位表存在且每行都有 `camera_ip` | 由 `patrol-teach` 产出（§3）；手写 YAML 很容易漏 `camera_ip` 而被预检拒绝 |
| 5 | 依赖装好 | 离线：`pip install --no-index --find-links <wheels> httpx pyyaml`（见 §2） |

**避开老系统的采图窗口**：相机 `192.168.1.238` 与新系统共用，老系统每小时 `:01:0x–:02:00`
批量采 6 台。`patrol-m1` 默认在第 5 分钟启动正是为了错开它（`deploy/m1.py`）。

---

## 2. 安装（目标机）

现场实测目标机**能连 PyPI**（`https://pypi.org/simple/` → 200，阿里云镜像同样可达），
所以直接建 venv 装依赖即可：

```bash
cd /opt/mushroom-patrol
/usr/bin/python3 -m venv .venv
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ httpx pyyaml
# 只有要用图像微调（--framing）时才需要这两个：
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ pillow numpy
```

> ⚠️ **必须用这个 venv 跑，别用 `/usr/bin/python3`**：`deploy.m1` 顶层就 `import httpx`
> （`deploy.transport`），系统解释器上没有 httpx，会**在导入阶段就崩**——systemd 只会
> 看到一串重启，看不出原因。装依赖若走离线包，用 `docs/patrol/prod-deploy/wheels/`
> 里的 10 个 wheel（`pip install --no-index --find-links wheels httpx pyyaml`）。

源码按 `PYTHONPATH` 直接用（三个包都放在 `src/` 下：`patrol` / `deploy` / `measure`）：

```bash
export FMC4030_LIB_PATH=/opt/mushroom-patrol/lib/libFMC4030_2009_1.so
export PYTHONPATH=/opt/mushroom-patrol/src
.venv/bin/python3 -m deploy.m1 --check
```

装成命令则用 `patrol-m1` / `patrol-teach` / `deploy-fetch-room` / `deploy-flush-outbox`
（`pyproject.toml` 的 `[project.scripts]`）。

**prod 接收 API**（`/ingest`，图像索引入库）是另一个服务，装在 `/opt/mushroom-analysis`：

```bash
/usr/bin/python3 -m venv /opt/mushroom-analysis/.venv
/opt/mushroom-analysis/.venv/bin/pip install fastapi uvicorn
cp deploy/systemd/mushroom-analysis.service /etc/systemd/system/
systemctl enable --now mushroom-analysis && curl -s localhost:8000/healthz
```

它的 SQLite 落在 `WorkingDirectory` 下（`create_app` 的 `db_path` 是相对路径）：
`/opt/mushroom-analysis/mushrooms.db`。**必须单 worker**——连接是在工厂里建的，
多 worker 会各持一份、互相看不见（见 `analysis/api.py` 顶部注释）。

---

## 3. 站位示教（`patrol-teach`）

**什么时候需要示教**：站位坐标本来是**推导**的（`stations.build_grid`：Y 4492mm 均分
12 框、Z 212mm 均分 5 层），不示教也能开跑。**只有**当实际框位与均分不符时才需要——
层高不等分、框距不匀、机械装配偏差，或要现场核对 `box_id ↔ 框` 映射（票 04 的验收项）。
示教的产物就是那份 `stations.yaml`，M1 直接读它。

```bash
export PYTHONPATH=/opt/mushroom-patrol/src
python3 -m patrol.teach --stations /opt/mushroom-patrol/stations.yaml
```

| 命令 | 作用 |
| --- | --- |
| `reset` | 丢弃现表，从推导网格重建（**先在纸上算好编号再动**） |
| `jog Y 20` / `jog Z -5` | 单轴点动，10 mm/s（现场整定档，肉眼跟得上） |
| `pos` | 只打印当前 `Y=… Z=…`（抄数用） |
| `goto 187 -21` | 双轴插补直达（单段，M0 语义） |
| `home` | 两轴回零，重建坐标系 |
| `record S101 B101 1 1` | **以当前坐标**记录站位：id、框 id、层、框 |
| `list` / `drop S101` | 查看 / 删除 |
| `save` | 写盘（不 save 不生效；退出时会提醒） |

**示教一个格子的动作**：`jog` 把滑块挪到该框中心 → 目视/尺子确认 → `record`。
全部 60 个走完 `save`。

⚠️ 三条现场注意：

1. **示教不提供 `speed`/`acc`/`timeout`**：点动档是整定值（Y 10 mm/s）。要排障请用
   `patrol-debug`，那是它的职责——示教台上提速会让人对不准。
2. **命令必须先在纸上编号**：`record` 的层/框号就是写入的 `layer`/`col`，页面与
   `object_name` 都用它；`S105` = 第 1 层第 5 框，第 1 层在最上（`z` 最大）。
3. 示教是**覆盖**推导值：只 `record` 关心的格子，其余保持推导值也能跑。

---

## 4. 运行巡检（`patrol-m1`）

```bash
python3 -m deploy.m1 --check     # 只读预检：连接 + 软限位 + 站位表 + 端点，不动机构
python3 -m deploy.m1 --once      # 跑一轮就退出（首次上机验证用这个）
python3 -m deploy.m1             # 常驻：每小时第 5 分钟一轮
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--once` / `--check` | — | 互斥；单轮 / 只读预检 |
| `--at-minute` | `5` | 每小时第几分钟启动（错开老系统 `:01–:02`） |
| `--interval` | 无 | 固定间隔秒数；给了就忽略 `--at-minute`。**下限 15 min**（单轮约 6–7 min） |
| `--stations` | `/opt/mushroom-patrol/stations.yaml` | 站位表；不存在时按推导网格生成并落盘 |
| `--outbox` | `/opt/mushroom-patrol/outbox.jsonl` | 待同步记录 |
| `--log` | `/opt/mushroom-patrol/m1.log` | 人读日志落盘（同时仍打 stdout）。**别只靠 stdout**：管道接在启动它的会话里，会话一收就什么都不剩 |
| `--room` | `/opt/mushroom-patrol/room.yaml` | 库房在库状态（入库日期）⇒ **巡检准入门禁**，见 §4.1 |
| `--ingest` | `http://10.77.77.39:8000/ingest` | prod 接收端点 |
| `--no-sync` | 关 | **端点未定时用这个**：outbox 只累积，不产生"同步失败"噪音 |
| `--camera-ip` | `192.168.1.238` | 全场同一台相机（生成站位表时写入） |
| `--ip/--port/--device/--lib` | `192.168.1.239/8088/1/环境变量` | 控制器寻址 |

### 4.1 准入规则：什么时候**不许**动（`room.yaml`）

| 在库天数 | 是否巡检 |
| --- | --- |
| 第 0、1 天（入库当天与次日） | **不动**——刚入库未稳定，拍了没有可比性 |
| 第 2 … 25 天 | 巡检（生长期，正是采数据的时候） |
| 第 26 天及以后 | **不自动巡检**——本周期该采收了 |

整库**同一个入库日期**，所以是库房级判定。数据来自生产系统库房批次表
`mogu.mo_gu_batch`（一个批次一行：`code_num`=库房、`in_time`=整库入库日期、
`in_num`=包数、`info_code`=该库「育菇房信息」设备号），用 `deploy-fetch-room` 落地：

```bash
deploy-fetch-room --room 611 --out /opt/mushroom-patrol/room.yaml   # 取数并写入
deploy-fetch-room --room 611 --print                # 只看不写（先看判定再决定）
deploy-fetch-room --room 612 --from dump.tsv --today 2026-09-13    # 离线演练
```

脚本会自己从 `tools_mysql` 容器的环境里取口令（不落命令行历史），也可以 `--from -` 从
stdin、或读一份 TSV。生成的文件长这样：

```yaml
room_id: 611
entry_date: 2026-03-16     # 必填；整库入库日期（YYYY-MM-DD）
packages: 9792             # 入库包数（台账量，留档/对账用）
batch_no: mogu-100         # 批次号（回指生产系统那条记录）
source: "deploy-fetch-room 2026-09-13 22:42:40 (mo_gu_batch id=100, 在库第 181 天, 不巡检)"
```

写之前会先过一遍 `patrol.room` 的校验（写不出"生成得了但读不出来"的文件），
并明确打印 `entry_date` 的**旧值 → 新值**——它会变（换批次），但不该悄悄变。
入库日期比今天晚则拒绝写入（那说明查错了库/表或机器时间不对）。

**读不到就不动**（fail-closed）：文件缺失、`entry_date` 留空、日期格式不对、包数非数字，
一律**拒绝巡检**并在日志里说明原因。这台机器会自己走 4.5 米导轨——拿不到准入依据时
"不动"是唯一安全的默认：误动一次产出 60 张错误照片并写进测量表，少拍一轮只是少一轮数据。

```bash
python3 -m deploy.m1 --why      # 只看判定：今天第几天、为什么不动（不碰控制器）
```

**每轮重新判定**，所以天数走进窗口后**无需重启**就会自动开始巡检；走出窗口后自动停。
判定不过时 `--once` 返回 0（"明确不动"不是故障，别让 systemd 判成 failed 反复重启）。

> **在库房空置期想继续测链路**：另备一份 `room.test.yaml`（`entry_date` 用测试日期），
> 显式 `patrol-m1 --room /opt/mushroom-patrol/room.test.yaml`。
> 真值文件保持原样，于是"不带参数跑会被拒"这件事始终成立——不要用改真值的方式让测试跑起来。

> 首次部署可以先 `ensure_room_file()` 生成一份**留空**的占位 `room.yaml`
> （`python3 -c "from patrol.room import ensure_room_file; ensure_room_file('/opt/mushroom-patrol/room.yaml')"`），
> 再把读到的入库日期填进去。留空期间系统保持不动。

一轮做什么（`patrol.round.PatrolRound`）：回零 → 逐站「两段速到位 → 衰减 0.4s →
OUT0 补光 → `:7003/pool_capture` → 灭灯」→ 回原位 → 落 outbox → 同步 prod。

**失败分级**（2026-09-13 上机教训）：

| 失败 | 处置 |
| --- | --- |
| **运动失败**（`FmcError`，含 `goto` 的传输/控制器错误） | **立即作废本轮**并急停。传输层事实——同一接线上所有站位都会同样失败，继续跑只会又动 60 次机构 |
| **采图失败**（`CaptureError`） | 跳过该站，**连续 3 次**才中止本轮。那是"这一站没拍成"，下一站仍有意义 |
| **中断/未预期异常** | 先急停，记为 failed，**不静默** |

### 4.2 图像微调（第二步定位，`--framing`）

**走到推导坐标只是第一步**：12 框均分 4492mm、5 层均分 212mm 是**推导**值，只保证
"目标进视野"。货架焊接误差、相机斜拍 45° 的光轴偏置都会让目标不在画面中央——所以
第二步用**拍到的图像**把它挪正（实现：`patrol.framing` 闭环 + `measure.framing` 亮区质心）。

```bash
# 每个站位跑一次闭环，学到的偏移写回 stations.yaml；此后常规轮次直接从这里起步
.venv/bin/python3 -m deploy.m1 --framing once --mm-per-px 0.25 --max-rounds 1

# 每站每轮都闭环（60 站各多拍一张 ≈ 每轮多约 10 分钟，排障/标定时才用）
.venv/bin/python3 -m deploy.m1 --framing always --mm-per-px 0.25
```

| 参数 | 作用 |
| --- | --- |
| `--mm-per-px` | **必填**，像素→mm 的标定换算（横向/Y）。没有它微调**拒绝开启** |
| `--mm-per-px-z` | 纵向换算；省略则与横向相同 |
| `--framing-sign-y` / `-z` | 图像方向与轴正方向的对应（默认 Y=+1、Z=-1）。**配错的表现是"越挪越偏"**，闭环的不进步回退会挡住，但现场仍应先用单站验证方向 |

**安全边界**（都在 `patrol.framing` 里，有 21 个单测钉住）：

* 找不到目标 / 置信度低 ⇒ **一步都不挪**，用基准位置那张图交差；
* 单步 ≤ 15mm、累计 ≤ 40mm、写回站位表的 trim ≤ 20mm、目标坐标再过一次行程限位；
* 不进步（改善 < 15%）⇒ 退回**最好**的那个位置，而不是继续朝可能错了的方向挪；
* 总步数 ≤ `max_taps`（默认 2）。

`--check` 会把微调结论一起打出来（`图像微调：开启（once，0.2500 mm/px…）` /
`关闭（只用推导坐标）` / 缺什么就说什么）。**别跳过这一行**——"以为开了其实没开"
是这套东西最容易出的问题。



```
/opt/mushroom-patrol/
├── outbox.jsonl      ← **要同步到 prod 的数据**（round 汇总 / 图像索引 / 测量值），同步成功后清空
├── runs/             ← **本地取证**，每轮一个 JSONL，只增不删，不外传
│   └── 20260913-095938-2378816.jsonl
└── m1.log            ← 人读行文（与 runs/ 互补）
```

`runs/<时间>-<pid>.jsonl` 里是 `start` → 逐站 `station` 心跳 → `end`（含异常类型/文本/堆栈）。
**每写一行都 fsync**，所以哪怕进程被 `kill -9`，盘上也能读出"它走到第几站"。
这是刻意与 outbox 分开的：outbox 同步成功会清空，取证不能跟着没了。

排障第一步永远是：`tail -3 runs/*.jsonl` —— 最后一行就是断点。

**断网/重启的数据安全**：结果先进本地 JSONL outbox，同步成功才清空；失败保留待补传
（ADR-0001，at-least-once，prod 侧 `INSERT OR REPLACE` 保证幂等）。所以拔网线跑几轮
再插回去，数据不会丢。

### 取证：`runs/` 与 outbox 的分工

### systemd（常驻）

单元文件就在仓库里，直接拷：

```bash
cp deploy/systemd/patrol-m1.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now patrol-m1
```

与早先贴在文档里的版本比，`patrol-m1.service` 有两处**必须**的差别：

| 差别 | 原因 |
| --- | --- |
| `ExecStart` 用 `/opt/mushroom-patrol/.venv/bin/python3` | 系统 python3 没有 httpx，`import deploy.m1` 直接崩 |
| `ExecStart` 不带 `--no-sync` | `/ingest` 端点已就位（见 §7），跑完就同步 |

> ⚠️ **不要** `enable` 采图那两份遗留单元（`xcloud-capture.service` 等）——它们指向
> 另一套安装且抢同一个 `:99` 与 `:7003`，见 ADR-0009。

**门禁关着的时候进程会不会退出**：不会。第 0–1 天 / 第 26 天起 / 读不到入库日期时，
常驻进程只把那一轮记成 `skipped` 并**继续等下一个调度时刻**（每轮现读 `room.yaml`
重新判定）。所以库里空着的时候服务是"活着但不干活"，新批次入库后**不用人工重启**。
（早先的实现是 `if not allowed: return 0`：systemd 认为"正常退出"就不会再拉起，
于是新批次来了机器上根本没有进程在等。）

### 补传累积的 outbox

端点通了之后，之前用 `--no-sync` 跑出来的记录要补传：

```bash
deploy-flush-outbox --dry-run --outbox /opt/mushroom-patrol/outbox.jsonl   # 先看有多少
deploy-flush-outbox --outbox /opt/mushroom-patrol/outbox.jsonl             # 真传
```

推成功才动文件；失败时原文件**一个字节都不变**（重跑即可）。推成功后原文件清空、
原文归档到同目录 `sent/`——补传是一次性动作，事后总要能回头核对"到底送出去了什么"。

---

## 5. 首次上机验收顺序

只读 → 能动 → 能拍，逐级放行（沿用 gap-list §10 的顺序）：

1. `patrol-debug`：`status` / `para`（软限位必须 `[0, 4495]`）/ `home`（**人在场**）。
2. `patrol-teach`：`jog` 一个框 → `record` → `save`（机构会动，需在场）。
3. `patrol-m1 --check`：预检通过（不动机构）。**它不受门禁影响**——库里没有批次时
   也会真的连一次控制器、验软限位、读位置，并把当日准入结论一并打出来。早先它在门禁
   关闭时直接退 0，运维看到的"预检通过"其实一次都没检查。
4. `patrol-m1 --once`：一轮跑完。**一次核四样**（缺一不可）：
   - `tail -3 runs/*.jsonl` → 末行是 `"event": "end"`，且 `status` 与命令输出一致；
     若末行是某条 `station` 心跳，说明**它没跑完**，那一行就是断点。
   - `wc -l outbox.jsonl` → `1 + 60` 行（1 条 round 汇总 + 60 条 `image_index`，
     失败站也在内，带 `ok:false`）。开同步时它会同步完清空，那就看 `GET /rounds`
     与 `GET /images` 有没有对应行。
   - MinIO / `:7003` 侧能看到对应对象。
   - `curl localhost:8000/images?station_id=S101` → 能按站位查到刚拍的那几张。

   ⚠️ **别用 stdout 当证据**：`--once | tee` 只保当前会话。跑之前先确认 `--log` 落到了盘上。
5. 补做**丢步验证**（`docs/patrol/prod-deploy/travel-speed-check.py`，限位基准法，阈值 0.2 mm）：
   巡检档 150 mm/s 的现场验收**不含**"无丢步"，静默丢步只认这个判据。
6. 常驻：systemd 起 `patrol-m1`，观察 ≥1 个调度周期。

> 2026-09-13 的教训：当时一轮跑到第 14 站被中断，而 **outbox 里零记录**（daemon 只在
> 整轮返回后才写第一行），stdout 又只活在会话管道里 —— 现场剩下的唯一信息是"它死了"。
> 第 4 步的核对顺序与 `runs/` 就是为这一幕定的。

---

## 6. 手动控制（巡检台页面 → 机构）

页面上点一下，实际走的是这条路（**细节与理由见 ADR-0016**）：

```
页面 → console（写 data/cmd/） → patrol-serve（唯一持有控制器） → FMC4030 → 写回 result.json
```

| 环节 | 干什么 | 不干什么 |
| --- | --- | --- |
| 巡检台页面（`web/console/`） | 发 `POST /api/cmd {kind,args}`、轮询 `GET /api/cmd` | 不认识控制器与 SDK |
| console（`deploy.console`） | 收指令、落盘、巡检中当场拒绝、读写会话与急停 | **绝不连控制器**（单会话设备，会踢掉正在跑的那一轮） |
| patrol-serve（`deploy.manual_exec`） | 领指令 → 校验 → 动机构 → 写回结果 | 不在轮内领指令 |

```
python3 -m deploy.patrol_serve --cmd-dir /app/data/cmd      # 容器里的 patrol-serve 角色
```

**四条必须记住的语义**：

1. **急停是闩锁，而且能打断正在跑的那一轮**。急停写 `data/cmd/ESTOP`（独立文件，不进
   指令队列）；`patrol` 的等待原语每轮询一次 `abort` 回调，为真立刻抛 `MotionAborted`，
   执行方随即 `stop_everything()`。置位期间只有"关灯"和"再停一次"被放行，**复位必须
   显式做**（`DELETE /api/stop`）。延迟约 1 秒（0.5 s 轮询 + 0.1–0.2 s 检查）——
   这是**软件急停**，不替代硬件急停回路。
2. **指令有新鲜度**：提交后 60 秒才被领走的一律不执行（判"已过期"）。正常路径上执行方
   0.5 秒轮询一次，只有"中间隔着 11 分钟的一轮"才可能超时——那正是我们希望它别执行的情形。
3. **巡检进行中不许手动**：console 当场回 409 + "预计 x 分钟后可用"；除急停外连补光灯和
   抓拍也拒（相机链路与曝光窗口都归那一轮）。
4. **放开会话 = 回零 + 撤权**：手动挪过之后坐标系只有回零能重新对齐硬限位。排不上回零
   （轮内/通道忙）时接口会**明说**没排上。

关掉急停联动只在排障时说得通：`patrol-m1 --no-estop`（**正常运行不要用**——那会让"有人
按了急停"拦不住正在跑的那一轮）。

---

## 7. 已知边界

- **`--ingest` 端点已就位**（2026-09-13 关掉 gap-list G1）：`http://10.77.77.39:8000/ingest`，
  由 `mushroom-analysis.service` 提供（`analysis.api`，落在 `/opt/mushroom-analysis/mushrooms.db`）。
  历史遗留的 outbox 用 `deploy-flush-outbox` 补传，见 §4。
- **站位表的 `camera_ip` 是全局配置**：本机只有一台相机（装在滑块上，60 站共用）。
  省略该列 → 装载时取 `--camera-ip`（默认 `192.168.1.238`）；写成空串 → 运行时补默认值；
  显式写了 IP → 一律保留（将来分机位仍然由站位表说了算）。要把它固化进文件，
  用 `patrol-teach` 打开后 `save` 一次。
- **测量值还没接上**：`PatrolDaemon(measure_fn=None)` ⇒ 只落 round 行与图像元数据，
  `measurements` 为空。接法是从 `round` 报告取帧 → `measure` 的 Detector → 按框聚合
  → `MeasurementRecord.from_box_stats`；真模型（票 05）到位后一次接完。
  **影响**：`/growth` 与 `/growth/room` 现在返回空 / 全部 `no_prev`——这是诚实结果不是故障；
  生长判断的逻辑、判词与接口都已就绪（`analysis.growth`，UI 规格见
  `docs/patrol/console-ui/spec.md` §12）。
- **图像索引已落库**（ADR-0005/0011）：`run_cycle` 每帧落一条 `kind:"image_index"`
  （成功/失败各一行，失败带 `ok:false` + `error`），随 outbox → `/ingest` 进 `analysis`
  的 `images` 表，`/images?station_id=&box_id=&room_id=` 可按站位/框/库房回溯，
  每行带 `room_id/entry_date/batch_no`（2026-09-13 修：这三列原先缺失，写入时被**静默丢弃**）。
- **M2（`.elo` 脱机脚本）已决策暂缓**：厂商格式是二进制且无文档（gap-list G4）。
- **systemd 单元已落盘**：`deploy/systemd/{patrol-m1,mushroom-analysis}.service`。
  投产前 `systemctl enable patrol-m1` 并观察 ≥1 个调度周期（gap-list J5 已关）。

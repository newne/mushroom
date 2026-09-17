# 上机切换：巡检台的三个容器（步骤 5 的操作手册）

面向**第一次把巡检容器放上库房主机**的那一次操作。目标是在**不碰老系统**的前提下，让
"算法侧调度 → 跑一轮 → 页面看得到 → 能手动干预"这条链第一次在现场闭环。

前置阅读：`docs/adr/0011`（部署边界）、`0012`（合仓与容器形状）、`0013`（一期不让位）、
`0015`（前后端分离）、`0016`（手动控制执行侧）；容器内的行为见 `deploy/README.md`。

---

## 0. 三条不能弄错的

| # | 事项 | 为什么 |
| --- | --- | --- |
| 1 | **控制器单会话**：同一时刻只能有一个进程连着 FMC4030 | 容器里的 `patrol-serve` 与宿主上的 `patrol-m1`（systemd）同时跑会互相踢掉，那一轮 60 张图作废。上容器前先 `systemctl disable --now patrol-m1` 并确认没有残留进程 |
| 2 | **避开老系统的采图窗口 `:01:0x–:02:00`** | 相机 238 与老系统共用；我们的调度器排在 `:20`（每 3 小时），别改到那一分钟 |
| 3 | **`room.yaml` 是准入门禁的真值** | 它的入库日期决定"今天动不动"。库里空置时系统只会"活着但不干活"，这是设计不是故障 |

## 1. 构建与推送（开发机，WSL）

```bash
cd /mnt/d/code/mushroom
REG=registry.cn-beijing.aliyuncs.com/ncgnewne

docker build -f docker/Dockerfile.patrol      -t $REG/mushroom_patrol:0.1.0      .
docker build -f docker/Dockerfile.console-web -t $REG/mushroom_console_web:0.1.0 .
docker push $REG/mushroom_patrol:0.1.0
docker push $REG/mushroom_console_web:0.1.0
```

> 前端镜像自己打（`Dockerfile.console-web`）是刻意的：现场只依赖阿里云 registry 一条
> 交付通道，不必去 Docker Hub 拉 nginx。

推送前可在本地把两个容器先验一遍（真容器、假配置、不碰硬件）：

```bash
wsl -e bash /mnt/d/code/mushroom/docs/patrol/prod-deploy/verify_console_containers.sh
```

## 2. 现场目录（库房主机）

### 2.0 先把变量写进 `.env`（与 compose 文件同目录）

compose 里的每个变量都有默认值，但**现场应当显式写下来**——默认值是给本地/演练用的。
在 `/home/sysadmin/algorithm/mushroom_service/.env`（与 `mushroom_solution.yml` 同目录；
模板见仓库 `docker/.env` 的同一段）写：

```ini
PATROL_IMAGE=registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0
CONSOLE_WEB_IMAGE=registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_console_web:0.1.0
CONSOLE_WEB_PORT=8002          # 上位机访问的就是这个（对外只开这一个）
CONSOLE_PORT=8001              # 后端：给前端容器与调度器用
PATROL_CAPTURE_HOST=172.17.0.1:7003
PROD_INGEST=http://172.17.0.1:8000/ingest
PATROL_ANALYSIS=http://172.17.0.1:8000
PATROL_RUN_URL=http://mushroom_console:8001/api/patrol/run
```

> 这一版**没有**用项目名/环境后缀做变量名（`COMPOSE_PROJECT_NAME` 仍是 `mushroom_service`），
> 所以 `.env` 里的键与上面这份一一对应，照抄即可。
>
> `docker/.env` 在本机是**未跟踪**文件（根 `.gitignore` 有 `.env`），SSH 口令 `PROD_PW`
> 也可以放在那里给 `docs/patrol/prod-deploy/{rexec,rpush}.sh` 用——**别拷进仓库**。

### 2.1 目录与文件

```bash
SSH=sysadmin@10.77.77.39          # 库房主机
BASE=/home/sysadmin/algorithm

# 与 mushroom_service/ 平级的巡检目录（compose 里的 ../mushroom_patrol/* 指的就是它）
mkdir -p $BASE/mushroom_patrol/{configs,data/trigger,data/cmd,Logs,lib,web}

# 1) 厂商 SDK 动态库（与采图容器一样：运行时挂载，不打进镜像）
scp libFMC4030_2009_1.so $SSH:$BASE/mushroom_patrol/lib/

# 2) 配置：room.yaml（门禁真值）+ stations.yaml（站位表，可由 patrol-teach 产出）
scp room.yaml stations.yaml $SSH:$BASE/mushroom_patrol/configs/

# 3) 前端页面（改页面只需重做这一步，不必重建镜像）
scp -r web/console/* $SSH:$BASE/mushroom_patrol/web/

# 4) compose 文件
scp docker/mushroom_solution.yml $SSH:$BASE/mushroom_service/mushroom_solution.yml
```

## 3. 起容器

```bash
cd $BASE/mushroom_service
# ⚠️ 必须带 -f mushroom_solution.yml：这个项目的 compose 文件名不是默认的 compose.yaml，
#    不带 -f 的话 docker compose 会回一句 `no configuration file provided`（2026-09-15 实测）。
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol ps
```

四个容器各自是什么：

| 容器 | 角色 | 端口 | 说明 |
| --- | --- | --- | --- |
| `mushroom_patrol` | `patrol-serve` | 无 | **唯一持有控制器**的进程；被触发才跑一轮，同时执行手动指令 |
| `mushroom_console` | `console` | 8001 | 收请求、读写协调文件、提供 `/api/*`；**不连控制器** |
| `mushroom_console_web` | nginx | **8002** | 发页面 + 把 `/api` 与 `/healthz` 反代给 console（单一 origin） |
| `mushroom_preview` | `preview` | 无 | 相机实时画面（RTSP→MJPEG，ADR-0017）。**没有宿主端口**：只由 console 反代 |

## 4. 验收（逐条过，别跳）

```bash
# 4.0 执行方预检：把要用的路径、配置与**厂商库能不能加载**一次打出来（不连控制器）
docker run --rm -v /home/sysadmin/algorithm/mushroom_patrol/lib:/opt/fmc-lib:ro \
  registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0 patrol-serve --dry-run \
  --trigger-dir /app/data/trigger --cmd-dir /app/data/cmd \
  --room /app/configs/room.yaml --stations /app/configs/stations.yaml \
  --lib /opt/fmc-lib/libFMC4030_2009_1.so --log ""
# 期望末行：厂商库 … —— 已加载 ✅ / 预检结束：**没有连接控制器，也没有进循环**（退出码 0）
# 加载不了会**退出 2** 并指出方向（路径不对 vs C++ 运行时太旧）

# 4.1 后端活着、门禁读得出来
curl -s localhost:8001/healthz; echo
curl -s localhost:8001/api/room | head -c 300; echo
curl -s localhost:8001/api/status | head -c 400; echo

# 4.2 页面出得来（经前端容器，验证反代与静态页都在）
curl -s -o /dev/null -w '%{http_code}\n' localhost:8002/
curl -s localhost:8002/healthz; echo

# 4.3 协调文件写得进去（整棵data只读会让"跑一轮"静默失败）
curl -s -o /dev/null -w '%{http_code}\n' -X POST 'localhost:8001/api/patrol/run?reason=cutover'
ls -l $BASE/mushroom_patrol/data/trigger/run.json

# 4.4 执行方领到了吗（5 秒内应看到日志）
docker logs --tail 20 mushroom_patrol

# 4.5 实时预览（ADR-0017）：容器健康 + 上游真的有帧 + 反代通了
docker compose -f mushroom_solution.yml --profile patrol ps mushroom_preview         # Up (healthy)
curl -s localhost:8003/healthz 2>/dev/null || \
  docker compose -f mushroom_solution.yml exec mushroom_preview curl -fsS localhost:8003/healthz; echo
# 期望 {"ok":true,"frames":N,"age_s":<1.0,...}；frames 一直在涨 = ffmpeg 在出帧
docker compose -f mushroom_solution.yml exec mushroom_console curl -fsS localhost:8001/api/preview/status; echo
curl -s -o /tmp/f.jpg -w '%{http_code} %{content_type} %{size_download}\n' \
  localhost:8002/api/preview/frame.jpg     # 期望 200 image/jpeg 几十 KB
# 浏览器：接管 → 操作卡下面那张「实时画面」自己亮起来；放开 → 自己关掉
```

浏览器打开 `http://10.77.77.39:8002/`，确认：门禁带、站位列表、平面图、事件日志都在；
状态药丸显示"巡检中"时能看见逐站进度。

**然后按顺序做这三件事**（都要有人在机器旁）：

1. **算法侧触发一轮**：`python -m scheduling` 的巡检任务按 `:20` 触发；或直接手点
   `POST /api/patrol/run`。核对 `runs/*.jsonl` 末行是 `end`、`outbox` 从 `1+60` 行同步清空。
2. **手动一次点动**（步长 0.5mm，人在旁边看着）：页面 → 接管 → `Y+` → 看机构动一下、
   位置读数变化 → `回零` → 放开（放开会自动排一条回零）。
3. **急停链路**：点动一条较大的移动，运动中按急停 → 机构应立刻停住、日志里出现
   "收到急停请求"、页面药丸变"已急停"；**然后复位急停**（它是闩锁，不复位后面每一轮都会失败）。
4. **实时画面**（新增，只需眼看）：接管 → 画面自动出现（约 1 秒延迟）→ 点一次 0.5mm 点动，
   画面里的人/物标尺确实跟着动 → 放开 → 画面自动消失。

## 5. 回滚

```bash
cd $BASE/mushroom_service
docker compose -f mushroom_solution.yml --profile patrol stop mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
# 需要彻底撤掉时
docker compose -f mushroom_solution.yml --profile patrol down
```

容器停掉后控制器就空出来了；要让宿主上的 systemd 版本重新接管：

```bash
sudo systemctl enable --now patrol-m1
```

## 6. 切换后仍欠的账

- 站立的老系统采图（`:01:30` 每小时 6 台）继续跑，与本系统并行；两边的照片目录不同
  （老系统 `mogu/{room}/{YYYYMMDD}/`），互不覆盖。
- **测量值还没接上**（`measure_fn=None`）：`/growth` 与 `/growth/room` 返回空/`no_prev`，
  这是诚实结果不是故障。
- 页面的**历史模式**目前只有"选站位 → 该站位历史图"的简版；时间轴/大图缩放/生长曲线
  见 `docs/patrol/console-ui/spec.md` §5.3 与票 05 的 Comments。
- 一期的**让位握手**不做（ADR-0013）：巡检进行中手动操作会被拒，等轮末（约 94% 的时间可用）。

---

## 7. 2026-09-15 首次上机实录（库房主机 10.77.77.39）

**结果**：三个容器 running/healthy；
`http://10.77.77.39:8002/` 出页面，`/healthz`、`/api/status`、`/api/stations`、`/api/cmd` 全通；
**调度 → 触发 → 执行方 → 门禁**整条链跑通——今天库房第 183 天，门禁按设计拒绝
（`skipped`，日志明确"跳过启动预检（本轮不碰控制器）"，机构一步没动）。
手动面的拒绝路径也验了：没接管 → 403；急停置位中 → 409（且急停优先于"上一条没结束"）；
急停标志文件写入/清除正常。**动机构的两步（手动点动、急停链路）刻意留到有人在场时做。**

上机过程中发现并修掉四个真问题——都属于"只有真跑一次才会露头"的那类：

| # | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 1 | `mushroom_patrol` 反复重启，日志只有 `unrecognized arguments: --room …` | `patrol-serve` 把 m1 的参数声明成**位置参数**（`nargs="*"`），而入口脚本是 `--trigger-dir X --room Y …` 的混合顺序；argparse 不支持位置参数与可选参数交替 | 改 `parse_known_args` 透传；加 `--dry-run` 预检；容器验证脚本改成按**入口角色 + 真实参数形状**跑（原来只跑 `--help`，所以没拦住） |
| 2 | 页面能开，但 `/api/*` 与 `/healthz` 全 **502** | nginx 只在**启动时**解析一次 upstream；后端容器一重建（`compose up -d` 换 IP）就还指着旧地址 | nginx 用 `resolver 127.0.0.11` + 变量 `proxy_pass`，改成每次请求重新解析 |
| 3 | 前端容器一直 `unhealthy`（服务其实好的） | 官方 nginx 镜像里**没有 wget**，healthcheck 必然失败 | 镜像里装 `curl`，healthcheck 换成 `curl -fsS` |
| 4 | 页面点「抓拍」报 `连接控制器失败 … GLIBCXX_3.4.32 not found` | 厂商 `libFMC4030_2009_1.so` 需要 GLIBCXX_3.4.32，而 bookworm（GCC 12）的 libstdc++ 只到 3.4.30；宿主 Ubuntu 24.04 到 3.4.33，所以裸机跑得通、容器里跑不通 | 巡检镜像基础从 `python:3.12-slim-bookworm` 换成 **`-trixie`**（GCC 14 → 3.4.33）；`--dry-run` 预检改成**真的 load 一次**厂商库，把这类问题挡在部署前 |
| 5 | （预防性，上机前已修）后端"跑一轮"会失败 | console 整棵 `data` 只读，而触发请求/手动指令要写 `data/trigger`、`data/cmd` | 这两个子目录单独 rw 挂载，父目录仍 ro |

> 第 4 条是我在 Dockerfile 注释里**写错过**的一处判断：2026-09-14 只核对了"厂商库依赖哪些库"
> （`readelf -d` 显示只要 libstdc++/libc），**没核对它需要的符号版本**，于是得出"bookworm 就行"。
> 教训与现在的防线都记在 `docker/Dockerfile.patrol` 顶部。

**现场状态与后续动作**：

- `room.yaml` 是**真值**：入库 2026-03-16 ⇒ 第 183 天 ⇒ 门禁关闭，自动巡检不会跑（设计如此）。
  要让机构真动（跑一轮或手动点动），要么等新批次进入第 2–25 天，要么显式用 `room.test.yaml`
  ——**后者会让机构真的移动，必须有人在机器旁**。
- 配置已复制到 `/home/sysadmin/algorithm/mushroom_patrol/configs/`，**从今天起它是活的那一份**；
  `deploy-fetch-room` 与 `patrol-teach` 都应当指向它（老的 `/opt/mushroom-patrol/` 原样保留，
  宿主的 `patrol-m1.service` 仍未 enable）。
- 回滚：`docker compose -f mushroom_solution.yml --profile patrol stop mushroom_patrol mushroom_console mushroom_console_web`；
  改动前的 `mushroom_solution.yml` 与 `.env` 备份为 `*.bak-20260915`。
- 只动了三个巡检服务：`mushroom_solution` / `mlflow` / `postgres_db` / `caddy` 未重启
  （服务器那份 compose 里 `mushroom_solution` 的镜像 tag 是 2026-03-20 的，**合并时保留，没有回退**）。

---

## 8. 2026-09-15 第二次上机：实时画面（ADR-0017）

现场用了半天之后提的新需求：**接管时看不见画面**，点动/定位只能靠坐标猜。查过三条路
（采图服务当预览 5.2 s 一张且必然落文件；相机没有 HTTP MJPEG，ISAPI 全 404；相机有 RTSP），
选了 RTSP→MJPEG 转码，决定与实测数据在 `docs/adr/0017`。

这一步上机**只新增一个容器**，另外三个一行没改：

```bash
# 1) 重建并推送巡检镜像（多了 ffmpeg 与 preview 角色）
cd /mnt/d/code/mushroom
docker build -f docker/Dockerfile.patrol -t $REG/mushroom_patrol:0.1.0 .
docker push $REG/mushroom_patrol:0.1.0

# 2) 推 compose 与页面（页面改了，镜像不用重建）
scp docker/mushroom_solution.yml $SSH:$BASE/mushroom_service/mushroom_solution.yml
scp -r web/console/* $SSH:$BASE/mushroom_patrol/web/

# 3) 起预览容器（其余三个不动）
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d --no-deps mushroom_console     # 只换 console 的镜像
docker compose -f mushroom_solution.yml --profile patrol restart mushroom_console_web         # 只重读 nginx 配置
```

**这一节要盯的两个数**：`mushroom_preview` 的 `/healthz` 里 `frames` 要一直在涨（不涨就是
ffmpeg 没连上相机，日志里会有打码后的 RTSP 地址与 ffmpeg 的 stderr）；console 的
`/api/preview/status` 在巡检进行中必须是 `available:false`（这是设计，不是故障）。

**实测记录（2026-09-15 13:36，真相机 192.168.1.238）**：

| 检查 | 结果 |
| --- | --- |
| `mushroom_preview` | `Up (healthy)`；`/healthz` → `{"ok":true,"frames":1911,…，"restarts":0}`，约 **5 fps** |
| 反代（`:8002` → console → preview） | `/api/preview/frame.jpg` → `200 image/jpeg` 640×360（15.5 KB） |
| 流 | `curl --max-time 3 :8002/api/preview` → `multipart/x-mixed-replace`，3 秒 **16 帧**、245 KB |
| 断开后 | 上游 `viewers` 回到 0（浏览器关掉 `<img>` 时上游连接确实断了，没白留队列） |
| 其余容器 | `mushroom_patrol` / `mushroom_console` / `mushroom_console_web` 全部 `Up (healthy)`，页面 200 |

两个上机才暴露、已修的小问题：

1. **compose 必须带 `-f mushroom_solution.yml`**——这个项目的文件名不是默认的 `compose.yaml`，
   照着手册里"cd 过去再 `docker compose --profile patrol up`"的写法会得到
   `no configuration file provided: not found`（本轮手册已逐条改成带 `-f`）。
2. **`启动转码` 那行日志只打了命令末尾 6 个参数**，现场看到的是 `7 -f image2pipe -c:v mjpeg -`，
   像是"打码把命令吃掉了"。改成打**整条命令**（口令仍然打码）——现场排障要的正是
   "实际用的 scale/fps/transport"。

`.env` 里新增了四行（`PATROL_PREVIEW` / `PREVIEW_PORT` / `PREVIEW_SCALE` / `PREVIEW_FPS`），
compose 与 `.env` 都留了 `*.bak-20260915b` 备份。

---

## 9. 2026-09-16 发版：巡检侧按版本 tag 重发一通

**起因**：现场要求"发版到 prod"。先只读核查现状，结论是**巡检侧三个容器已经在跑
`feat/patrol-merge` 的 HEAD**，只有 `mushroom_preview` 还挂在旧镜像上。所以这次发版的实际
内容是：**给巡检侧一个可回滚的版本 tag、把现场 `.env` 钉住、四个容器一起重建、逐条复验**。

### 9.1 发版前的核查（逐条有证据，不是推断）

| 项 | 现场实际 | 与 HEAD（`3667c09`）比 |
| --- | --- | --- |
| `mushroom_patrol` / `mushroom_console` | 镜像 `ba4728d6a804`（09-15 17:51 创建） | **一致**：容器内 `console.py` / `manual_exec.py` / `preview.py` / `patrol_serve.py` 的 sha256 与工作区逐字节相同 |
| `mushroom_console_web` | 镜像 `14b19113a8b5` | **一致**：`nginx.conf` sha256 `622e1628…` |
| 页面 | 宿主 `mushroom_patrol/web/index.html` | **一致**：sha256 `4935763d…` |
| `mushroom_solution.yml` | 宿主副本 | **一致**：sha256 `6cef277f…` |
| 宿主 `analysis`（`:8000`） | `/opt/mushroom-analysis/src` | **一致**（本地是 CRLF，按内容比对 8 个文件全同） |
| `mushroom_preview` | 镜像 `d17f5ee2ef51`（09-15 13:57 创建） | ⚠️ **旧镜像**：里面的 `deploy/m1.py` 是 Z 框架迁移（ADR-0018）之前的版本 |

### 9.2 发版内容

```bash
# 开发机（WSL）：构建并推送（上下文＝仓库根）
cd /mnt/d/code/mushroom
REG=registry.cn-beijing.aliyuncs.com/ncgnewne
TAG=0.1.0-20260916114407-3667c09          # 0.1.0-<YYYYMMDDHHMMSS>-<短 SHA>，同算法侧惯例
docker build -f docker/Dockerfile.patrol      -t $REG/mushroom_patrol:$TAG      -t $REG/mushroom_patrol:0.1.0      .
docker build -f docker/Dockerfile.console-web -t $REG/mushroom_console_web:$TAG -t $REG/mushroom_console_web:0.1.0 .
docker push $REG/mushroom_patrol:$TAG      && docker push $REG/mushroom_patrol:0.1.0
docker push $REG/mushroom_console_web:$TAG && docker push $REG/mushroom_console_web:0.1.0
```

| 产物 | tag | digest |
| --- | --- | --- |
| `mushroom_patrol` | `0.1.0-20260916114407-3667c09`（＝`0.1.0`） | `sha256:58e4981595a2318b1da452ad08a103e6d4705cf21edb50c2995ee7a4e78c7be8` |
| `mushroom_console_web` | `0.1.0-20260916114407-3667c09`（＝`0.1.0`） | `sha256:97e6df932bc430a6f5aab6ea5c2d698764af596f2277a33507f81404dde0d0f3` |

> **构建是全缓存命中的**：Dockerfile 与构建上下文一个字节没变，导出层的摘要与现场正在跑的
> 镜像**逐层相同**（`ba4728d6a804` / `14b19113a8b5`）。也就是说这一版**没有代码变更**，
> 换来的是"现场钉在一个带版本号的 tag 上"这件事本身——之前四个容器都挂在浮动的 `:0.1.0`
> 上，回滚只能靠记镜像 ID（见 9.4）。

```bash
# 库房主机：钉住 tag（只改两行，改前先备份）
TAG=0.1.0-20260916114407-3667c09
REG=registry.cn-beijing.aliyuncs.com/ncgnewne
cd /home/sysadmin/algorithm/mushroom_service
cp -a .env .env.bak-$(date +%Y%m%d-%H%M%S)
sed -i -e "s|^PATROL_IMAGE=.*|PATROL_IMAGE=$REG/mushroom_patrol:$TAG|" \
       -e "s|^CONSOLE_WEB_IMAGE=.*|CONSOLE_WEB_IMAGE=$REG/mushroom_console_web:$TAG|" .env
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
```

**发版前先跑的本地门禁**（都在开发机，不碰现场）：

| 检查 | 结果 |
| --- | --- |
| `patrol-workspace` 全量测试 | **647 passed**（98.7 s） |
| `ruff check .` | All checks passed |
| `verify_console_containers.sh`（真容器、假配置） | **全部通过**；其中用**现场那份真厂商库**（`VENDOR_LIB=…/libFMC4030_2009_1.so`）跑了 `patrol-serve --dry-run`，确认新镜像里**能加载**（GLIBCXX 那条老坑） |

### 9.3 现场动作与验收（2026-09-16 11:46–11:49）

1. `cp -a .env .env.bak-20260916-114629` → `PATROL_IMAGE` / `CONSOLE_WEB_IMAGE` 由 `:0.1.0`
   改成版本 tag（`mushroom_solution.yml` 没动：sha256 与仓库一致）。
2. `pull` + `up -d` 四个服务：**四个容器全部 Recreated**，`mushroom_preview` 因此换到了新镜像。
3. 逐条验收（`container-cutover` §4）：

| 检查 | 结果 |
| --- | --- |
| 四个容器 | 全部 `Up (healthy)`，镜像 tag 都是 `0.1.0-20260916114407-3667c09` |
| `patrol-serve --dry-run`（真厂商库） | `厂商库 … —— 已加载 ✅` / `预检结束：**没有连接控制器，也没有进循环**`，退出码 0 |
| `/healthz` `/api/room` `/api/stations` `/api/grid` | 全通；`/api/grid` → `z 0…212`（Z 新框架）；门禁 `allowed:false`（第 184 天） |
| 页面 `http://10.77.77.39:8002/` | 200，标题「蘑菇房巡检台」，`/healthz` 反代通 |
| 触发链路 `POST /api/patrol/run?reason=release-20260916` | 202 + `run.json` 落盘；执行方 1 秒内领走（日志 `领到巡检请求 20260916-114816`） |
| **门禁是否拦住机构** | 拦住：`准入未通过：跳过启动预检（本轮不碰控制器）` → `单轮结束：skipped`；`data/runs/` 不存在（**一轮取证都没有 = 机构一步没动**） |
| 触发请求是否残留 | 已消费（`run.json` 里 `consumed_at=2026-09-16T11:48:17`），日志里只出现 1 次"领到"——不会在门禁放开时诈尸 |
| 实时预览 | `/healthz` 两次采样 `frames 530 → 555`（≈5 fps，`restarts:0`）；页面侧 `/api/preview/frame.jpg` → `200 image/jpeg 15435` 字节 |
| 控制器 | `/api/status` → `state:IDLE, connected:false`——发版前后都没有进程占着它 |

### 9.4 回滚

```bash
cd /home/sysadmin/algorithm/mushroom_service
cp .env.bak-20260916-114629 .env
docker compose -f mushroom_solution.yml --profile patrol up -d \
  mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
```

⚠️ **靠 tag 回滚不了这一版**：内容没变，所以 `:0.1.0` 与版本 tag 指向**同一个 digest**。
真正的回滚点是上面那份 `.env` 备份；再往回（09-15 之前）的镜像是 `ba4728d6a804`
（patrol/console）与 `14b19113a8b5`（console_web），两者在现场宿主上都还在。

### 9.5 这次发版**没有**覆盖的（欠账，不是漏做）

- **算法侧 `mushroom_solution` 仍是 2026-03-20 的镜像**（compose 里 tag
  `0.1.0-20260320172444-a615909`）。实测该容器里**没有** `PATROL_RUN_URL`，即
  `src/scheduling` 那个"每 3 小时触发巡检"的 job（步骤 2/5）**没在 prod 上跑**——
  现在触发一轮只能靠页面按钮或 `POST /api/patrol/run`。ADR-0012 §7 原本就把算法侧发版
  押后（"巡检跑稳之前不变"），且 `src/` 里有 `wip(solution)` 在建重构快照，故本次不动。
- `patrol-m1.service`（宿主 systemd 那份）依旧 inactive：控制器现在由 `mushroom_patrol`
  容器独占，两者不能同时跑（本文件 §0 第 1 条）。

---

## 10. 2026-09-16 追加：实时画面左右镜像——先定位，再选一条修法

**现象**：接管后看实时画面，左右与现场相反（ADR-0017 补记）。

**定位（先证伪自己这条链，再怀疑上游）**：

| 检查 | 结果 |
| --- | --- |
| 转码链 | `deploy/preview.py` 的滤镜只有 `scale=<w>:-2`；全仓 grep 无 `hflip/vflip/transpose` |
| 页面 | `web/console/index.html` 对图像只有缩放/平移，无 `scaleX(-1)` |
| 抓拍链 | `third_party/capture` 与 `patrol.capture_client` 无任何图像变换 |
| **两条链对照** | 同一静止场景（2026-09-16 16:04）：预览帧（RTSP 640×360）与同期 SDK 抓拍原图（2880×1616）**朝向完全一致**。两张原图存在 `mirrorcheck/`，可自行复核 |

⇒ 镜像在上游（DVR 图像设置，或相机朝向使画面相对现场左右相反），**预览与抓拍同时受影响**。

**两条修法（推荐先 A）**：

```bash
# A. DVR 侧修根因（一次修好三条消费方：预览、抓拍、老系统）
#    浏览器开 Web Viewer（http://192.168.1.238/）→ 图像/编码设置里的"镜像/翻转"关掉。
#    改完刷新页面即可看到；本仓不用动、不用发版。
```

```bash
# B. 只纠预览（动不了 DVR 时）：现场 .env 改一行 + 只重建 preview 容器
cd /home/sysadmin/algorithm/mushroom_service
cp -a .env .env.bak-$(date +%Y%m%d-%H%M%S)
sed -i 's|^PREVIEW_FLIP=.*|PREVIEW_FLIP=hflip|' .env   # 没有该行就手动追加 PREVIEW_FLIP=hflip
docker compose -f mushroom_solution.yml --profile patrol up -d mushroom_preview
# 生效证据：容器日志出现「预览服务就绪：rtsp://… 翻转=hflip」；
# 仍不对就换 vflip（上下颠倒）或 both（180°）——认不出的值会当场报错退出，不会静默忽略。
```

> B 的边界：**只翻预览**，抓拍与历史照片仍是镜像的（对算法无碍——视觉训练本就带
> `hflip_prob=0.5` 的水平翻转增强）。**日后若在 DVR 侧修好，必须把 `PREVIEW_FLIP` 改回
> `none`**，否则会翻两次。

---

## 11. 2026-09-17 发版：console 改版（`d87486f`）——先只发了页面，再补镜像

**起因**：现场要求"发版到 prod"，内容是 `feat/patrol-merge` 上的 console 改版 `d87486f`
（运动中显示实时位置/速度、导轨图移入中栏放大、删除补光灯开关、抓拍并入实时画面卡）。

### 11.1 关键发现：这一版**不能只发镜像**，也不能**只发页面**

`d87486f` 动了 12 个文件，但按"打进镜像 vs 宿主挂载"拆开看，落点完全不同：

| 改动 | 落在哪 | 发版动作 |
| --- | --- | --- |
| `web/console/index.html` | 宿主目录 bind-mount 进 nginx（`Dockerfile.console-web` 注释：**改页面 = scp 一次**） | 拷文件 |
| `deploy/src/deploy/{console,manual,manual_exec}.py` | 打进 `mushroom_patrol` 镜像（`console` 角色） | 重建镜像 |
| `patrol/src/patrol/fmc/client.py`（`status_listener`） | 同上 | 重建镜像 |
| `nginx.conf` / `Dockerfile.patrol` / `mushroom_solution.yml` | 打进镜像 | **本次未动**（一个字节没变） |

**发版前核查发现的真相**：现场在跑的 `mushroom_patrol`（`ba4728d6a804`，tag
`0.1.0-20260916114407-3667c09`）里 **代码是重构前那一版**——`console.py` / `manual.py` /
`manual_exec.py` 三个文件与 `d87486f` **不同**（`preview.py` 相同）。也就是说 09-16 那次
"重发"确实没有代码变更，console 改版从未上过机。所以第一次只换页面等于**制造了版本错配**：
页面在调一个后端还不存在的 `progress` 字段。

> 页面侧对此是**向后兼容**的：`liveProgress()` 第一行就是 `if(!busy() || !cmd.inflight ||
> !cmd.progress) return null;`，所以错配期间不报错、只是"实时位置/速度"那几处不显示。
> 但不该把它留在现场——所以随后补发了镜像。

### 11.2 发版内容

```bash
# 开发机（WSL）：构建并推送。上下文＝workbuddy worktree（内容 = d87486f）
cd /mnt/c/Users/niucg1/WorkBuddy/Worktrees/mushroom/feat-patrol-merge-17681f41
REG=registry.cn-beijing.aliyuncs.com/ncgnewne
TAG=0.1.0-20260917134707-d87486f
docker build -f docker/Dockerfile.patrol      -t $REG/mushroom_patrol:$TAG      -t $REG/mushroom_patrol:0.1.0      .
docker build -f docker/Dockerfile.console-web -t $REG/mushroom_console_web:$TAG -t $REG/mushroom_console_web:0.1.0 .
docker push $REG/mushroom_patrol:$TAG      && docker push $REG/mushroom_patrol:0.1.0
docker push $REG/mushroom_console_web:$TAG && docker push $REG/mushroom_console_web:0.1.0
```

| 产物 | tag | digest |
| --- | --- | --- |
| `mushroom_patrol` | `0.1.0-20260917134707-d87486f`（＝`0.1.0`） | `sha256:c1d4763d1e109373a218aecb7fea91a0998899f35eb9c381f3daf3eb5aab03a1` |
| `mushroom_console_web` | `0.1.0-20260917134707-d87486f`（＝`0.1.0`） | `sha256:7735cd3573d96509bc572e2f9e34dd1cfe82ebc892e5f6eef8706225d4694e26` |

> `mushroom_console_web` 的新旧 digest **不同**（旧 `97e6df93…` / 新 `7735cd35…`），但内容
> 等价：镜像里只有 `nginx.conf`，而它没改；差异来自重建（`apt-get install curl` 层的时间戳）。
> 页面不在镜像里，所以"重建前端镜像"对页面本身没有影响。

**发版前先跑的本地门禁**（开发机，不碰现场）：

| 检查 | 结果 |
| --- | --- |
| `deploy`/`patrol` 相关单测（console / manual / manual_exec / fmc_client） | **116 passed** |
| jsdom 无头页面校验 `verify-page.js` | **101/101 通过**，运行期错误：无 |
| `verify_console_containers.sh`（真容器、假配置、**真厂商库**） | **全部通过**；含 `patrol-serve --dry-run` 加载现场那份 `libFMC4030_2009_1.so` 成功（GLIBCXX 老坑） |

```bash
# 库房主机：钉住 tag + 页面 + 重建四个容器
TAG=0.1.0-20260917134707-d87486f
REG=registry.cn-beijing.aliyuncs.com/ncgnewne
cd /home/sysadmin/algorithm/mushroom_service
cp -a .env .env.bak-$(date +%Y%m%d-%H%M%S)
sed -i -e "s|^PATROL_IMAGE=.*|PATROL_IMAGE=$REG/mushroom_patrol:$TAG|" \
       -e "s|^CONSOLE_WEB_IMAGE=.*|CONSOLE_WEB_IMAGE=$REG/mushroom_console_web:$TAG|" .env
# 页面：备份后原子替换（校验 sha256 再 mv，避免 nginx 读到半个文件）
cd /home/sysadmin/algorithm/mushroom_patrol/web
cp -a index.html index.html.bak-20260917-134111
# 页面 sha256：旧 4935763d…（仅 7dd2345）→ 新 043d0645d9020a3d6d6bd9dca91fee8479b95f9031bde624078b582090d21c07
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d  mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
```

### 11.3 现场验收（2026-09-17 13:41–13:52）

| 检查 | 结果 |
| --- | --- |
| 四个容器 | 全部 `Recreated` → `Up (healthy)`，tag 都是 `0.1.0-20260917134707-d87486f` |
| **容器内代码 == `d87486f`** | `console.py` / `manual.py` / `manual_exec.py` / `preview.py` 四个文件 LF 归一化 sha256 **逐个 SAME**（这是"镜像里真是这一版"的直接证据，不是推断） |
| 新后端生效 | `/api/cmd` 回包里出现 `progress` 字段（旧后端没有这个键）——哪怕空闲时是 `null`，键本身即证据 |
| 页面 | `http://10.77.77.39:8002/` → 200、69578 字节、sha256 `043d0645…`；`maphover/mapcap/capbtn` 命中 10 处；`lampbtn` **0 处**（补光灯开关确已移除） |
| 反代与后端 | `/healthz` 200；`/api/room` 门禁 `allowed:false`（第 185 天，>25）——判定正确 |
| 协调文件可写 | 容器内 `/app/data` 与 `/app/Logs` 均 rw |
| 实时预览 | `available:true`、`frames` 递增、`restarts:0`、`last_frame_age_s 0.08`；`/api/preview/frame.jpg` → `200 image/jpeg 16371` 字节 |
| 控制器 | `state:IDLE, connected:false`——发版前后都没有进程占着它 |
| **机构是否动过** | **一步没动**：门禁拦住（`准入未通过：跳过启动预检（本轮不碰控制器）` → `单轮结束：skipped`），`data/runs/` **不存在** |

> 触发链路（`POST /api/patrol/run`）在**页面发完、镜像补发之前**用旧容器验过一次：
> 202 + `run.json` 落盘、执行方 1 秒内领走、门禁拦住、`runs/` 仍为空。补发镜像后**没有
> 再触发一次**——`patrol-serve` 的路径本次未改，重触发只会往取证账上多记一次。

### 11.4 回滚

```bash
cd /home/sysadmin/algorithm/mushroom_service
cp .env.bak-<STAMP> .env                     # 回到 3667c09 那两个 tag
docker compose -f mushroom_solution.yml --profile patrol up -d \
  mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
# 页面单独回滚（与镜像无关）：
cd /home/sysadmin/algorithm/mushroom_patrol/web && cp -a index.html.bak-20260917-134111 index.html
```

### 11.5 这次暴露的两个"文档/工具与实现不符"（欠账）

1. **本文件 §4.0 的预检命令是错的**：写的是 `patrol --dry-run`，但 `--dry-run` 只存在于
   **`patrol-serve` 角色**（`deploy.m1` 的 argparse 根本不认这个参数，实测 `exit 2`、
   `unrecognized arguments: --dry-run`）。本节按 `verify_console_containers.sh` §4 的写法
   （`docker run … patrol-serve --dry-run`）才跑得通。§4.0 待更正。
2. **开发机 `docker/.env` 与现场 `docker/.env` 不是同一份**：开发机那份的 `PATROL_IMAGE` /
   `CONSOLE_WEB_IMAGE` 仍是浮动的 `:0.1.0`，现场那份才是版本 tag。发版必须**改现场那一份**
   （`/home/sysadmin/algorithm/mushroom_service/.env`），别把开发机的 `.env` 拷过去——
   那会把现场其他变量（`PATROL_RUN_URL`、`PATROL_PREVIEW`、MinIO/MLflow 口令等）一起覆盖。


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


## 12. 2026-09-17：历史图全黑 / 抓拍后不显示——定位与修复

**现场报告**（`http://10.77.77.39:8002/`）：所有历史图像都是黑的；实时模式抓拍之后那一格
也不出现。

### 12.1 定位过程（都是可复现的命令，不是推测）

```bash
# 1) prod 的 /api/images：行是有的，但最新的几行 cloud_url 是 null
curl -s 'http://10.77.77.39:8002/api/images?limit=3'
#   {"ts":"2026-09-17T14:10:12","station_id":"S101",
#    "object_name":"20260917/B101_S101_top45_141012","cloud_url":null,...}

# 2) 175 行里 163 行有 cloud_url，且都是控制网地址
#    http://192.168.1.250:9000/mogu/20260913/B103_S103_top45_215319.jpg

# 3) 上位机到不到那个地址？——不通（5s 超时），而 prod 的 8002/8000 是 0.03s 通
#    本机路由表只有 10.77.77.0/24 与 192.168.124.0/24，**没有 192.168.1.0/24**
python -c "import socket;s=socket.socket();s.settimeout(5);s.connect(('192.168.1.250',9000))"
```

`gap-list.md` §A3 已经写着这台 prod 是双网卡：`eno1 = 192.168.1.250/24`（控制网）、
`tun0 = 10.77.77.39/24`（VPN）。**MinIO 就落在控制网那一段**——上位机走 VPN 够不着它。

### 12.2 两个独立根因

| # | 根因 | 表现 |
| --- | --- | --- |
| 1 | 页面把 `cloud_url`（控制网地址）直接当 `<img src>`，违反 ADR-0003"页面不直连 MinIO" | 每格等一次 TCP 超时 → `.im img` 的深色底留成**一块黑**；163 行全黑 |
| 2 | `ManualExecutor._capture` **丢掉**了 `capture()` 的返回值 | 索引里存的是不带 `.jpg` 的 `image_object_name()`，且没有 `cloud_url` → 抓拍那一格无从取图 |

第 2 条的 6 行在 prod 里能直接看到（`ok=1` 但 `object_name` 不带后缀、`cloud_url` 为
`null`）：`20260915/B103…`、`20260915/B308…`、`20260917/B304…`、`20260917/B106…`×2、
`20260917/B101…`——时间戳正是现场那几次手动抓拍。

### 12.3 修复（本次改动）

- `console.py`：新增 `GET /api/image?object_name=<对象名>`，**服务端**拼 MinIO 地址取字节；
  `/api/images` 不再回 `cloud_url`；新增 `PATROL_MINIO` / `PATROL_MINIO_BUCKET` 环境变量。
- `manual_exec.py`：`_capture` 接着往下传截图服务回的 `object_name`（带 `.jpg`）与 `cloud_url`。
- `web/console/index.html`：`imageUrlFor(row)` 只从对象名拼同源地址；取不到图时标 `noimg`
  并显示"图取不到"（不再留一块和"拍黑了"分不开的深色方块）。
- `docker/mushroom_solution.yml`：`mushroom_console` 增两个环境变量（**现场 `.env` 不必改**，
  默认值即宿主 docker0 网关）。

### 12.4 现场验收（发完页面 + 重建 console 之后）

> ⚠️ **本节原先把发版面写小了**（2026-09-17 实发时改正）：只重建 `mushroom_console` 是**不够**的
> ——`deploy/manual_exec.py` 不只被 console 用，`patrol_serve.py:257/297` 也用它装配手动执行方，
> 而跑 `patrol-serve` 的是 `mushroom_patrol`。也就是说根因 2 的修复对**守护进程**同样必要，
> 否则计划/运动巡检的抓拍仍会索引一个不带 `.jpg`、没有 `cloud_url` 的名字。
> 正确做法见 §13.2：`PATROL_IMAGE` 一把影响 patrol / console / preview 三个，一起重建。

```bash
cd /home/sysadmin/algorithm/mushroom_service
# 只动了环境变量与后端代码 ⇒ 重建并重启 console；页面是挂载的，单独拷一次
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate mushroom_console
scp web/console/index.html sysadmin@10.77.77.39:/home/sysadmin/algorithm/mushroom_patrol/web/

# 1) 字段已经不再下发（期望空输出）
curl -s 'localhost:8002/api/images?limit=3' | grep -c cloud_url

# 2) 取图真的通（期望 200 + image/jpeg；实际约 750 KB——相机原图 1080×648，
#    不是缩略图。历史模式一屏几帧就会拉几 MB，见 §13.4 的欠账）
curl -s -o /dev/null -w '%{http_code} %{content_type} %{size_download}\n' \
  'localhost:8002/api/image?object_name=20260917/B101_S101_top45_141012'

# 3) 取不到时是"说清楚"而不是黑块（期望 502 + JSON 理由）
curl -s 'localhost:8002/api/image?object_name=20260917/NOPE.jpg' ; echo

# 4) 浏览器打开 http://10.77.77.39:8002/ → 历史模式：缩略图出图；
#    断网/停 MinIO 时应显示"图取不到"，不是一块黑
```

> 判据分得开才叫修好：**200 但图黑** = 相机/补光的问题；**502 或"图取不到"** = 取图链路
> 的问题。旧版本这两种情况在页面上长得一模一样。

## 13. 2026-09-17 发版实录：cc94258（历史图同源代理 + 抓拍索引补全）

上一节是"定位与修复"，本节是这一版**真的上机**的记录。

### 13.1 产物

```bash
# 开发机（WSL）：构建并推送。上下文＝workbuddy worktree（内容 = cc94258）
REG=registry.cn-beijing.aliyuncs.com/ncgnewne
TAG=0.1.0-20260917153139-cc94258
docker build -f docker/Dockerfile.patrol -t $REG/mushroom_patrol:$TAG -t $REG/mushroom_patrol:0.1.0 .
docker push  $REG/mushroom_patrol:$TAG && docker push $REG/mushroom_patrol:0.1.0
```

| 产物 | tag | digest | 本地 image id |
| --- | --- | --- | --- |
| `mushroom_patrol` | `0.1.0-20260917153139-cc94258`（＝`0.1.0`） | `sha256:13522d942c40f81737e457ac62b82cdf8a6052d27c095e178b9969b87670f708` | `sha256:94b40634d732…` |

`mushroom_console_web` **没重建**：页面是 bind-mount 的、`nginx.conf` 一个字节没变
（§11.1 同一结论），所以 `CONSOLE_WEB_IMAGE` 保持 `0.1.0-20260917134707-d87486f`。

### 13.2 发版范围：**三个容器**，不是文档原先写的"一个"

`PATROL_IMAGE` 被 `mushroom_patrol` / `mushroom_console` / `mushroom_preview` 共用：

```bash
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol mushroom_console mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate \
  mushroom_patrol mushroom_console mushroom_preview
```

为什么要带 `mushroom_patrol`：`patrol_serve.py` 装配手动执行方用的就是
`deploy.manual_exec.ManualExecutor`——根因 2 的修复对守护进程同样必要（见 §12.4 的更正）。

> **发版前先门禁复核**，且写成 fail-closed：`/api/room` 的 `allowed` 不是 `false` 就整体中止。
> 理由是重建 `mushroom_patrol` 会走一次启动预检；门禁关着（现场 `day:185 > max_day:25`）
> 才会 `skipped`、一步不碰控制器。

### 13.3 现场验收（2026-09-17 15:57–16:08）

| 检查 | 结果 |
| --- | --- |
| 门禁 | `allowed:false`（第 185 天）⇒ 重建不影响机构 |
| 三个容器 | `Recreated` → `Up (healthy)`，tag 全是 `…-cc94258`；`console_web` 保持 `…-d87486f` |
| **容器内代码 == `cc94258`** | `console.py` `80a1e12e…`、`manual_exec.py` `94144bb4…`，与仓库 LF 归一化 sha256 **逐个 SAME** |
| 页面 | 经 `:8002` 发出来的字节 **71740 B / `348e3021…`**，与 git blob 逐字节一致 |
| 页面里的旧路径 | `192.168.1.250` **0 处**、`cloud_url` 仅剩 1 处（注释里解释"为什么不能用它"） |
| `PATROL_MINIO` | 容器内 `http://172.17.0.1:9000` / bucket `mogu` |
| `/api/images` 不再泄露控制网地址 | `cloud_url` **0 处** |
| **取图真的通** | 不带 `.jpg` 的名字 `20260917/B101_S101_top45_141012` → **200 `image/jpeg` 766370 B**（带后缀同值，证明补齐逻辑对） |
| 取不到时说清楚 | `NOPE.jpg` → **502** + JSON 理由（指向 `172.17.0.1:9000` 的 404），不是黑块 |
| 非法入参 | 完整 URL / `../` 穿越 / 缺参 → 全 **400**（地址不可被请求方改写） |
| **肉眼确认** | 把 `/api/image` 的字节取回本地打开：真实灰阶帧、`2026-09-17 14:10:02`、`CAM01`——**不再是黑的** |
| **历史模式截图** | 无头 Chrome 打真页面：大图与时间轴缩略图**都出图**，且出图的正是那条"不带 `.jpg`、`cloud_url:null`"的旧行 |
| 基础健康 | `/healthz` 200；`/api/preview/frame.jpg` 200 `image/jpeg` 16598 B；`frames` 递增、`restarts:0` |
| 机构是否动过 | `data/runs/` **不存在**（门禁拦住），日志 `巡检执行方就绪` |

> 截图的两个坑记一下，免得下次误判成"图还是黑的"：
> 1. `core.autocrlf=true` ⇒ 工作区那份页面是 **CRLF**，直接 scp 会把 CRLF 发上机。
>    prod 上一直是 **LF**（旧文件 69578 B == git blob 的 sha256 `043d0645…`）。
>    **要发的是 git blob 的字节**：`git show <sha>:web/console/index.html > 产物`。
> 2. 历史模式要**三次点击**才看到图：切模式 → 选站位（触发 `renderHistory`）→ 挑一帧。
>    只做前两步的话 `#bigimg` 根本没有 `src`，截出来那块深色是**空容器**，不是"图黑"。

### 13.4 这版留下的欠账

1. **`/api/image` 给的是原图，没有缩略图参数**——实测 **766 KB/帧**（1080×648）。历史模式
   一屏几帧就是几 MB；现场走 VPN 会更慢。`spec.md` §10.10 记的 `w=` 未决项就是这个，
   建议下一步给 `/api/image` 加 `w=`（服务端降采样，或落一份 MinIO 侧缩略图）。
2. **`docker system df`：镜像 45.06 GB，其中 32.78 GB 可回收**（140 个镜像 / 58 个容器）。
   发版没动它，但值得排一次批量清理——别在生产机上随手 `prune`，先列出再决定。

### 13.5 回滚

```bash
cd /home/sysadmin/algorithm/mushroom_service
cp .env.bak-20260917-155719 .env
cp mushroom_solution.yml.bak-20260917-155719 mushroom_solution.yml
docker compose -f mushroom_solution.yml --profile patrol up -d \
  mushroom_patrol mushroom_console mushroom_preview
# 页面单独回滚（与镜像无关）：
cd /home/sysadmin/algorithm/mushroom_patrol/web && cp -a index.html.bak-20260917-155719 index.html
```

---

## 14. 2026-09-18：预览提质（1280×15）+ 抓拍电平修正

现场一句话反馈：

> 图像太糊了，和直接使用摄像头软件看到的质量不一样，且视频流是支持25帧的。

拆开是**两条互不相干的根因**，各自修各自的。第 0 步先把"25 帧"这个前提证伪——它错了，
而且错的方向会让人去做一件纯亏的事（把 `-r` 写成 25）。

### 14.1 先把前提钉死：相机流是 **15 fps**，不是 25

`ffprobe` 的 `avg_frame_rate` 只是容器里的声明，**不能直接信**；要**真解码一段流时间数帧**。

```bash
IMG=registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0-<tag>   # 这个镜像里有 ffmpeg 7.1.5
# ⚠️ 口令为空也必须写那个冒号：admin:@host。写成 admin@host 会被 DVR 判 401。
docker run --rm --network host --entrypoint ffprobe "$IMG" -rtsp_transport tcp \
  -show_entries stream=codec_name,width,height,avg_frame_rate,pix_fmt,color_range \
  -of default=nw=1 'rtsp://admin:@192.168.1.238:554/Streaming/Channels/101'
docker run --rm --network host --entrypoint ffmpeg "$IMG" -loglevel info \
  -rtsp_transport tcp -i 'rtsp://admin:@192.168.1.238:554/Streaming/Channels/101' \
  -t 10 -an -f null -            # ← 看结尾的 frame= 计数
```

实测（2026-09-18，相机 192.168.1.238）：

| | 主流 101 | 子流 102 |
| --- | --- | --- |
| 编码 / 分辨率 | hevc **2880×1616** | hevc **2880×1616**（与主流同分辨率） |
| `r_frame_rate` | **15/1** | 15/1 |
| 真解码 10 s 流时间 | **151 帧 ⇒ 15.1 fps** | 150 帧 ⇒ 15.0 fps |
| `pix_fmt` / `color_range` | `yuvj420p` / **`pc`（满量程）** | 同左 |

⇒ **25 fps 是 DVR 侧的编码设置，我们这边改不动**；把 `PREVIEW_FPS` 写成 25 只会让 ffmpeg
**复制帧**（观感不变）而带宽 ×1.7。所以取 15，与源对齐。

> `color_range=pc` 这一项同时是 §14.3 的对照组：**相机给的是满量程**，被抓拍链路压窄了。

### 14.2 根因 A：预览 640 宽在页面上被放大到 ≈844px

页面里预览容器是 `aspect-ratio:16/9; max-height:44vh`，1080p 下高度被压到 475px，
`object-fit:contain` 反推**显示宽度 ≈844px**。而转码命令写的是 `scale=640:-2` ⇒ 每帧都要
**放大 1.3 倍**才上屏。"糊"里最大的一块就是这里，跟相机没关系。

改动（**只改默认值 + 现场 `.env`，不重建任何镜像**）：

- `deploy/src/deploy/preview.py`：`DEFAULT_SCALE_WIDTH 640→1280`、`DEFAULT_FPS 5→15`
  （顶部注释里写了上面那份证据，另加一条测试 `test_defaults_are_pinned_to_display_size_and_camera_fps`
  钉住，防止有人凭直觉改小回去）
- `docker/mushroom_solution.yml`：`PREVIEW_SCALE:-1280`、`PREVIEW_FPS:-15`
- 现场 `.env` 显式写上这两个值（原来没写，吃的是 compose 默认值）

发版（**只重建 `mushroom_preview` 一个容器**）：

```bash
cd /home/sysadmin/algorithm/mushroom_service
docker compose -f mushroom_solution.yml --profile patrol up -d mushroom_preview
# 生效证据：日志里会打出**真实的** ffmpeg 命令行
docker logs mushroom_preview 2>&1 | grep '启动转码'
```

实测（重建后 10 分钟）：

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| 单帧 | 640×360 / ≈16 KB | **1280×718 / 61096 B** |
| 输出帧率 | 5 | **14.9**（healthz 的 frames 计数差分，10 s） |
| 带宽（回环实测） | ≈0.66 Mbps | **7.31 Mbps**（914 KB/s × 10 s） |
| 色域 | — | `yuvj420p` / `color_range=pc` ✅ |
| 容器 CPU / 内存 | — | **36%**（limit 1.5 核）/ **264 MiB**（limit 512 MiB） |

> 带宽是**每个观看者**一份（转码只做一次，但字节要发给每个人）。现场 VPN 实测可用 ≈28 Mbps
> （单张 766 KB 图 0.216 s 下完），单路观众有余量，2–3 路会开始吃紧。
> `q:v` 仍硬编码为 7——`PREVIEW_SCALE/FPS` 能靠 `.env` 调，**`q:v` 不能**，要调必须改代码重建镜像。

### 14.3 根因 B：抓拍被压进了 limited range（"发灰"的那一层）

XCloudSDK 的成像管线把**满量程**输入当 limited-range 输出，写出的 JPEG 服从
`out = 16 + in*219/255`。而 JPEG **没有"我是 limited range"这种元数据**，浏览器/PIL/ffmpeg
一律按满量程解释 ⇒ 黑停在 16、白停在 235：整幅发灰、对比度塌掉。

**细节其实没丢**，只是电平契约错了 —— 这一点必须先确认，否则会跑去查相机/镜头/曝光：

| 2026-09-18 实测（2880×1616） | min/max | p01/p99 | 近黑(<16) | 近白(>240) | 标准差 | 梯度能量 |
| --- | --- | --- | --- | --- | --- | --- |
| 抓拍（修正前） | **11 / 239** | 60 / 214 | 0.01% | **0.00%** | 30.64 | 11.117 |
| 抓拍（修正后） | **0 / 255** | 51 / 231 | 0.40% | 0.69% | **35.65** | 12.478 |
| 同相机 RTSP 直出帧 | **0 / 255** | 59 / 220 | 0.07% | 0.32% | 34.01 | 13.894 |

最直接的证据：**抓拍的 max 永远到不了 240**（0.00% 近白），而同一台相机直出的流有 0.32%。
量工具是 `levels.py`（见 §14.5）。

同一帧的目视对照（左 = 修正前，右 = 修正后，内容完全一致、只差一次 LUT）：
`captures/levels-before-after-20260918.jpg` —— 发灰的那层"纱"就是被压缩掉的电平区间。

修法（**厂商镜像一个字节都没改**）：在离线包 `xcloudsdk_py_offline_20260120_175307/` 下新增

```
levels-fix/
  capture.py            # 补丁版模块，**覆盖**站点包里那一个文件
  pylib/                # Pillow 12.2.0（cp310 + manylinux2014，glibc 2.17 ⇒ bullseye 可用）
```

并在 `docker-compose.override.yml`（厂商 compose 的覆盖层）里加两处：

```yaml
    environment:
      PYTHONPATH: "/app/levels-fix/pylib"
    volumes:
      - ./levels-fix/capture.py:/usr/local/lib/python3.10/site-packages/xcloudsdk_py/capture.py:ro
      - ./levels-fix/pylib:/app/levels-fix/pylib:ro
```

> ⚠️ **为什么不重建厂商镜像**：这份镜像是离线包 `docker load` 进来的（`image.tar.gz` 127 MB），
> 里面只有 fastapi/uvicorn/boto3；而且 SDK 的 `.so` 对运行库版本敏感，重打一遍基础镜像风险远
> 大于收益。挂载覆盖只动一个文件，**回滚 = 删两行 + `up -d --force-recreate`**。
> ⚠️ `./levels-fix/` 下的文件**必须先存在**再 `up`——bind-mount 源不存在时 Docker 会把它建成
> **目录**，挂进容器就成了目录。

补丁的三条硬约束（都有单测钉住：

```bash
cd third_party/capture
uv run --no-project --with pytest --with pillow --with boto3 pytest tests -q
```

）:

1. **fail-open**：没 Pillow 或读写失败 ⇒ 原样放行 + WARNING + 响应里带
   `levels_normalized=false`。这台服务掉了就是**一张图都没有**，比"图发灰"严重得多；
   而 Pillow 对厂商镜像是**可选**依赖（靠挂载注入），所以"没有它"是预期内的情况。
2. **不重复拉**：端点 ≤4 或 ≥251 判为已是满量程 ⇒ 跳过。防的是"厂商哪天修好了，我们把它再烤一遍"。
3. **不毁原图**：写同目录临时文件再 `os.replace` 原子替换。直接 `save(file_path)` 一旦中途失败，
   留下的是**截断的 JPEG**，而它已经覆盖了唯一一份有效数据。

> 重编码取 `q=95` 而非 `quality="keep"`：`im.point()` 派生出的新图没有 `format`/`quantization`，
> 而 Pillow 的 'keep' 判据正是 `im.format != "JPEG"`（`JpegImagePlugin.py:707/753`）⇒ 必然抛错；
> 且 'keep' 沿用**原图那套较粗的量化表**，是在原有损失上再加一代同样粗的损失，q=95 反而更小。
> 代价是文件大 **19%**（1050 KB → 1198 KB）。

发版（**只重建采图容器**，且必须先做写文件+预检，再动容器）：

```bash
B=/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
cd $B
cp -a docker-compose.override.yml docker-compose.override.yml.bak-$(date +%Y%m%d%H%M%S)
# 1) 先把 levels-fix/ 落好，2) 用 --rm 一次性容器预检，3) 再 up
docker compose config >/dev/null && echo "覆盖层解析 OK"     # ⚠️ 不要写成 `-f docker-compose.yml`，会跳过覆盖层
docker compose up -d
```

**动容器之前**用 `--rm` 一次性容器把三件事验完（不碰相机、不影响运行中的服务）：

```bash
docker run --rm -v "$B/levels-fix/pylib:/app/levels-fix/pylib:ro" \
  -e PYTHONPATH=/app/levels-fix/pylib --entrypoint python3 xcloudsdk-py:0.1.0 \
  -c "import PIL; from PIL import Image, features; print(PIL.__version__, features.check('libjpeg_turbo'))"
docker run --rm -v "$B/levels-fix/capture.py:/usr/local/lib/python3.10/site-packages/xcloudsdk_py/capture.py:ro" \
  -v "$B/levels-fix/pylib:/app/levels-fix/pylib:ro" -e PYTHONPATH=/app/levels-fix/pylib \
  --entrypoint python3 xcloudsdk-py:0.1.0 -c "import xcloudsdk_py.capture as c; print(c.__file__, c._levels_enabled())"
```

### 14.4 发版范围：这次是**两个互不相干的部署单元**

| 项 | 落点 | 发版动作 | commit |
| --- | --- | --- | --- |
| 预览 1280×15 | `deploy/src/deploy/preview.py` + compose 默认值 | 传 compose + 改 `.env`，**只重建 `mushroom_preview`**（不重建镜像） | `09bb891` |
| 抓拍电平 | `third_party/capture/src/xcloudsdk_py/capture.py` | 离线包 `levels-fix/` 挂载 + **只重建采图容器**（不重建厂商镜像） | `3e84e2c` |
| 预览单帧截断 | `deploy/src/deploy/console.py` | **需要重建 `mushroom_patrol` 镜像**——本次**未发**，见 §14.7 | `4324c78` |

### 14.5 现场验收

```bash
# A) 预览：生效证据是日志里那条真实命令行，以及 healthz 的计数
docker logs mushroom_preview 2>&1 | grep '启动转码'
docker exec mushroom_preview sh -c 'tr "\0" "\n" < /proc/1/environ | grep ^PREVIEW_'
docker exec mushroom_preview curl -s -o /dev/null -w '%{size_download}\n' --max-time 10 \
  http://127.0.0.1:8003/stream.mjpg          # ≈ 9.1 MB / 10 s ⇒ ≈7.3 Mbps

# B) 抓拍：响应对里直接带电平字段，这是最省事的判据
curl -s -m 90 'http://127.0.0.1:7003/pool_capture?ip=192.168.1.238&user=admin&storage=local&filename=check' \
  | grep -o '"levels_[^}]*'
#   期望： "levels_normalized":true,"levels_reason":"remapped","levels_before":[12,240],"levels_after":[0,255]

# C) 把图取回本地，用与"改前"完全相同的量法复测（别只看服务自己的自述）
python levels.py before.jpg after.jpg       # 端点应贴满 0/255，近白占比 >0
```

### 14.6 回滚

```bash
# A) 预览：只回环境变量 + compose，重建一个容器
cd /home/sysadmin/algorithm/mushroom_service
cp .env.bak-20260918110536 .env
cp mushroom_solution.yml.bak-20260918110536 mushroom_solution.yml
docker compose -f mushroom_solution.yml --profile patrol up -d mushroom_preview

# B) 抓拍电平：软关（最快，不改文件、不重建）
#    在 docker-compose.override.yml 的 environment 下加 CAPTURE_NORMALIZE_LEVELS: "0"，再 up -d
#    彻底回滚：
cd /home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
cp docker-compose.override.yml.bak-20260918113710 docker-compose.override.yml
docker compose up -d --force-recreate

# C) 整个覆盖层停用（连加固一起退回厂商原入口）
mv docker-compose.override.yml docker-compose.override.yml.off && docker compose up -d --force-recreate
```

### 14.7 这版留下的欠账

1. **`console.py` 的 64 KiB 单帧上限修复（`4324c78`）还没上机**——它需要重建 `mushroom_patrol`
   镜像。改动本身是必要的：预览提到 1280 宽后单帧 61096 B，离原来写死的 64 KiB 只剩 4 KB，
   而旧实现触顶时返回的是 **200 + 截断的 JPEG**（半张图看着像相机坏了）。
   现场下次重建镜像时会一起带上。
2. **`q:v` 仍然硬编码**（`preview.py` 的 `DEFAULT_QUALITY = 7`）。1280×15 下约 61 KB/帧 ≈7.3 Mbps；
   若现场觉得 VPN 吃紧，最省事的是把它做成 `PREVIEW_QUALITY` 环境变量（下次重建时一起做）。
3. **带宽随观看人数线性叠加**：转码只做一次，但每个观看者都要收 7.3 Mbps。上位机多人同时看时
   要留意。
4. **抓拍单张 5.2 s** 由 SDK 决定（`capture_time_ms: 5200`，电平修正只加 ≈0.1 s）。
   另外 SDK 里还有一个未被 Python 绑定的 `XCloudSDK_Device_DevSnap`（设备侧抓图、不依赖播放窗口），
   值得将来试它能不能更快/更清。
5. **历史图仍是"旧电平"**：MinIO 里 2026-09-18 之前的对象都没修过。要不要批量重刷是产品决定
   （重刷会把已入库的图再编码一代，不建议无差别做）。

---

## 15. 2026-09-21 第二次上机：console-ui 非格点抓拍 + 去掉帧数（`39aacfe` → `8e197d8`）

上一节（§14）之后现场线上一共发过三版，本节只记最后两版（同一天内）；
P2-1 那一版（`0.1.0-20260921112006-4fbf713`，页面 `88763 B / 0db2d591…`）的细节在
`.workbuddy/memory/2026-09-21.md` 与 `docs/patrol/console-ui/review-20260918.md` 的
"P2-1 实施记录"里，本节只把它作为"上一版"列在表内。

### 15.1 产物

| 产物 | tag | digest | 本地 image id |
| --- | --- | --- | --- |
| `mushroom_patrol` | `0.1.0-20260921151249-39aacfe`（＝`0.1.0`） | `sha256:589628e82eea3995b655b072b257c602f50d7a43bc6f54b0aebd35cfebb97ac6` | `sha256:2a6e7de9c7eccb038abf1a22bc4c7b3313e3b2b4b4471144be3e4ff10f5e0dbc` |

`mushroom_console_web` **没重建**（页面是 bind-mount 的；`CONSOLE_WEB_IMAGE` 保持
`0.1.0-20260917134707-d87486f`）。

| 页面版本 | 字节 | LF sha256（= git blob） |
| --- | --- | --- |
| 主体发版（`39aacfe`） | 90610 | `b077b736b8653d0de80a4ce7c506d54bbe845c22321083c9a141ac3032d663c1` |
| 页面补发 2（`8e197d8`） | 91504 | `7de0d0696020ab1d6796aea0075a04c57a843272bc950621c0729c6dbb955246` |
| 页面补发 3（`dec9e01`，**当前线上**，见 §15.6） | 92090 | `06602ca54820885958ada8188d070d98f56881d3bb852f8540fde233c2db28ab` |

改动到的三个模块的 LF sha256（两处拷贝逐一比对用）：

| 模块 | sha256 |
| --- | --- |
| `deploy/src/deploy/console.py` | `c32127b27d2f729ba6f86b8a527e68509332e1acd7db9a68888c27c06b95e884` |
| `deploy/src/deploy/manual_exec.py` | `cf697543e25d68b6c5947d8861755d5a4f1ce67603d2c23bad6c1db8a7b4c533` |
| `patrol/src/patrol/stations.py` | `fb0a0e292c0c2f6bc9f8898d06ddaad8f11f0b193c43d1e3f39f4e705e3a615e` |

### 15.2 发版范围：三容器（重建）+ 页面（挂载）→ 之后又一次**只换页面**

```bash
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol mushroom_console mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate \
  mushroom_patrol mushroom_console mushroom_preview
docker restart mushroom_console_web
```

`manual_exec.py` 动了 ⇒ 必须重建 `mushroom_patrol`（§13.2 的同一条理由：
`patrol_serve.py` 装配手动执行方用的就是它）。门禁 fail-closed 通过（第 51 天 > 25）。

**页面补发那一轮不重建任何镜像**：只 `cp → mv` 换 `index.html` + `docker restart
mushroom_console_web`，没有"启动预检可能碰机构"的路径，所以门禁只作记录、不作中止条件
（fail-closed 是给**重建 `mushroom_patrol`** 准备的）。

### 15.3 现场验收（2026-09-21 15:23–15:41）

| 检查 | 结果 |
| --- | --- |
| 门禁 | `allowed:false`（第 51 天 > 25）⇒ 重建不影响机构 |
| 四个容器 | 三个 patrol 系 `Recreated`→`Up (healthy)`，tag 全为 `…-39aacfe`；`console_web` 保持 `…-d87486f` |
| **容器内代码 == 本版** | `/srv` 与 `site-packages` 两处 × 三个模块 **6/6 SAME** |
| 镜像里确实换了话术 | `grep -c '手动抓拍需要 station_id'` = **0**；`grep -c 'def nearest_station'` = 1 |
| 页面（主体） | `:8002` 发出来 **90610 B / `b077b736…`**，与 git blob 逐字节一致；补发后 **91504 B / `7de0d069…`** |
| 归属逻辑（**不动机构**） | 在 `mushroom_console` 里跑 `nearest_station` 打 8 个点：S101 格点 / 层界下方 0.2mm→S101 / 层界上方 0.2mm→**S201** / 列界左→S101 / 列界右→S102 / S202 格点 / 原点角→S101 / 行程远端角→S512，**8/8 命中** |
| `/api/station_summary` | 字段集合 `{last_ok, last_ts}`，**无 `n`/`n_fail` 残留**（帧数已按现场反馈去掉） |
| `/api/stations` | `grid` 带 `y_pitch=374.3333` / `z_pitch=42.4`（页面 `nearestStation` 依赖它） |
| 机构零接触 | `data/runs` 不存在 |

备份：`index.html.bak-20260921-113329`（上一版）、`index.html.bak-20260921-154051`、
`.env.bak-20260921-152316`。

### 15.4 真机复核抓到的一处"死预告"（本节最值得记的一条）

发版后用**无头浏览器直接打现场 URL** 取渲染后的 DOM（真页面 + 真 API，**不用桩**），
发现抓拍下方的 `#capnote` 是**空的**：

- **根因**：页面 `posNow()` 读 `lastStatus.machine.real_pos`，而 `console.status()` 里
  `connected` 的定义就是 `source == "controller"`，`source` 只有 `station`（巡检中由当前
  站位推出）或 `none` —— console 按 ADR-0004/0013 **从不主动连控制器**（单会话设备，
  只有执行方碰它）。现场实测 `real_pos: null` / `pos_source: "none"`，所以
  **"位置读不到"是常态**；早先的写法在这条常态下走到 `else` 留空。
- **它为什么算缺陷**：这是同一毛病的第二次出现（第一次是"实时显示上游累计帧数"）——
  **UI 上摆了一个在真实运行条件下不产生任何输出的元素**。单测与 jsdom 都测不出来，
  因为**假后端总是给 `real_pos`**，假件的默认值比现场"更好"。
- **修法**：分档说清规则，末档（现场常态）必须非空：选中站位→留空；位置+站位表都有→
  具体站名 + ΔY/ΔZ；只有位置→只说规则；**位置未知→"拍照后按实际位置归到最近的格点
  （归到哪一站写在下方结果里）"**。**不**拿"最后成功目标"去猜站名——抓拍前机构可能已被
  别处挪过，猜错比不猜更坏（告诉他归 S102、结果写成 S101）。
- **纪律**：① 页面类改动上线后，用 `chrome --headless --dump-dom <现场URL>` 抓**渲染值**
  复核，这一步不需要桩、最省事；② 假件要能表达现场**真实的**取值（含 `null`/`"none"`），
  否则兜底分支永远测不到；③ 判据不是"有 else 分支"，而是"这条分支在现场参数下真的会被
  走到吗"。

修好后再取一次现场 DOM，`#capnote` 渲染为
`未选站位 → 拍照后按实际位置归到最近的格点（归到哪一站写在下方结果里）`。

### 15.5 回滚

```bash
# 后端（回到 P2-1 那一版镜像）
cd /home/sysadmin/algorithm/mushroom_service
cp .env.bak-20260921-152316 .env
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol mushroom_console mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate \
  mushroom_patrol mushroom_console mushroom_preview
# 页面（只回页面，不碰容器镜像）
cd /home/sysadmin/algorithm/mushroom_patrol/web
cp -a index.html.bak-20260921-154051 index.html     # → 90610 B / b077b736…
cp -a index.html.bak-20260921-113329 index.html     # → 88763 B / 0db2d591…（P2-1 原版）
docker restart mushroom_console_web
```

### 15.6 第三轮（`dec9e01`）：抓拍不再要求会话——现场"打开画面点抓拍没反应"

**反馈**：在零点打开画面，点击抓拍，没有反应。

**取证（prod 只读）**：`/api/cmd` → `session_active: false`、`estop: false`、无在飞指令；
最近一条结果是 **13:05 的 `home`**（"回零完成…Y=0.00 Z=0.00"，即"在零点"）——
**没有任何 `capture` 指令进过队列**。所以不是"收到了没执行"，而是"请求从未发生"。

**根因（页面，纯前端）**：抓拍复用了运动按钮那份门槛

```js
const locked = est || !active || busy() || inRound();   // active = session_active
$('#capbtn').disabled = locked;                          // ← 抓拍被 !active 一起关掉
```

而**抓拍不需要会话**：会话只约束运动指令（`MOTION_KINDS = ("goto","jog","home")`，
`ManualChannel.submit` 只对它们校验有效会话；`manual_exec.py` 里 `session` 出现 **0 次**），
ADR-0016 §6 明写"手动抓拍不移动机构"。

**为什么表现成"没有反应"**：**`disabled` 的按钮连 `click` 都不派发**——`onclick` 不执行、
后端收不到、页面也没有任何提示。这是"点了没反应"的完整解释。

**改法**：`#capbtn` 单独算门槛 `est || busy() || inRound()`（与后端三种 409 逐条对应），
`#capnote` **先解释为什么不能拍**（急停 / 巡检中 / 上一条没结束），没被锁时才说归属规则。
`verify-page.js` 148→155，并改掉一条把错行为写死的旧断言
（"未接管时点动/定位/回零/抓拍都禁用" → 拆成"运动类禁用"+"抓拍可用"）。

**本地全绿却现场中招的原因**：用例在前面就 `takebtn` 过，会话一直有效，
`!active` 那条分支**从没被走到**——假件的默认状态比现场"更好"。当天同类问题第三次。

**上机**：仍是**页面-only**（不重建镜像）。页面 `92090 B / 06602ca54820885958ada8188d070d98f56881d3bb852f8540fde233c2db28ab`；
`index.html.bak-20260921-160807` 为回滚点。真机 DOM 复核：`#capbtn` 无 `disabled`、
`#capnote` 渲染为归属兜底话术、三个 patrol 系容器 Uptime 未归零、`data/runs` 不存在。

> ⚠️ 本次写发版脚本时踩了一个自己的坑：验收段用 `grep -c <pattern>` 且**期望计数为 0** 时，
> `grep` 在无匹配下退出码是 1，**`set -e` 会把脚本就地中止**，后面的检查静默不跑。
> 期望 0 的检查要写成 `grep -c ... || true`，或改用 `! grep -q ...`。

## 16. 2026-09-21 第四次发版：实时画面黑边清零（`565e236`）——页面-only

**触发**：现场反馈"图像左右两侧有较大黑边，优化布局"。

### 16.1 根因与判据

`.pvwrap` 被 `width:100%` + `aspect-ratio:16/9` + `max-height:44vh` 三者夹住：前两条按
**宽度**定形、第三条压**高度**；`max-height` 一生效 `aspect-ratio` 就被打破，框的**有效**
宽高比变成 2.3~2.8。而画面是 **1280×718（≈1.7827）**，`object-fit:contain` 只能按高度缩进
去，左右各留一条 `.pvwrap` 的深色底（`#16212b`）—— 那"黑边"就是框自己的背景色，
**不是相机、不是取图链路**（这两条先排除，别去查曝光和 MinIO）。

判据（探针量的，不是目视估的）：视口 1904×929 每侧 **211px、画面只占框 63%**；
1664×900 / 1424×820 / 1366×768 各 123 / 88 / 103px。**四档都犯** ⇒ 固定 CSS 缺陷，
不是"某档宽度才犯"，所以也不该靠调媒体查询断点去糊（改前矮屏那档
`aspect-ratio:16/8.5` 反倒加重了它）。

改法见 spec §10.11：框宽 = `--pv-h × --pv-k`（比例单一来源），读数栏移到画面右侧吃掉
省出来的横向空间，画面从 44vh 提到 50vh；分界 1800px 是解方程得出来的。

### 16.2 发版范围：页面-only

只换 bind-mount 的 `index.html` + `docker restart mushroom_console_web`，**不重建任何镜像**，
所以不触发启动预检、没有"可能碰机构"的路径 ⇒ 门禁 `allowed:false` 只作记录，不作中止条件
（§5「只换页面的补发」）。

### 16.3 上机

```bash
source .scratch/prodenv.sh
bash docs/patrol/prod-deploy/rpush.sh .scratch/visual/index.new.html /root/index.new.html
bash docs/patrol/prod-deploy/rpush.sh .scratch/visual/release-page.sh  /root/release-page.sh
bash docs/patrol/prod-deploy/rexec.sh "sed -i 's/\r\$//' /root/release-page.sh; bash /root/release-page.sh"
```

- 待发字节：`git show 565e236:web/console/index.html` → **96607 B / 0 CRLF（纯 LF）/
  `c5755476b2e68f31342ce6da582bee7dcecf2ed15cd42596c516917d932200c2`**
  （工作区那份是 98470 B 的 CRLF，别发它 —— `core.autocrlf=true`）
- 备份：`index.html.bak-20260921-190545`（92090 B = 上一版 `06602ca5…`）

### 16.4 验收（逐条过）

| 检查 | 结果 |
| --- | --- |
| 线上返回字节 sha256 | `c5755476…` == 待发值 ✓ |
| 新写法在位（6 条） | `--pv-k:1280/718` / `.pvwrap{…aspect-ratio:var(--pv-k);` / `flex:0 1 calc(var(--pv-h) * var(--pv-k))` / `class="pvbody"` / `class="pvside"` / `overflow-wrap:anywhere` 各 **1** ✓ |
| 旧写法消失 | `aspect-ratio:16/9` = **0** ✓ |
| patrol 系三容器**未被重建** | `mushroom_patrol` / `mushroom_console` / `mushroom_preview` 各 **Up 4 hours** ✓ |
| 页面容器已重启 | `mushroom_console_web` Up 6 seconds ✓ |
| 机构零接触 | `docker exec mushroom_patrol ls /app/data/runs` → `No such file or directory` ✓ |
| 门禁（仅记录） | `allowed:false`、day 51 ✓ |

**端到端复核（最强的一条）**：写了个桩把**现场原样字节**代理到本机、只在**自己发出去的
字节**里注入探针（prod 一个字节不动），用真页面 + 真 API 量渲染后的几何：

| 视口 | 框宽高比 | 现场画面比例 | 每侧黑边 | 读数截断 |
| --- | --- | --- | --- | --- |
| 1904×1040 | **1.7827** | 1.78273 | **0** | 无 |
| 1664×900 | **1.7828** | 1.78273 | **0** | 无 |
| 1424×820 | **1.7828** | 1.78273 | **0** | 无 |

`getComputedStyle(.pvwrap).aspectRatio` = `1280 / 718`，与 `--pv-k` 一致。框比例恒等于
画面比例 ⇒ 黑边在数学上不存在（这一条比"我没看见黑边"强得多）。

### 16.5 回滚

```bash
cd /home/sysadmin/algorithm/mushroom_patrol/web
cp -a index.html.bak-20260921-190545 index.html   # → 92090 B / 06602ca5…（dec9e01 那版）
docker restart mushroom_console_web
```

> 页面-only 的补发**不需要**动 `.env` / compose / 镜像，所以回滚也就一步。

### 16.6 这一轮值得记的两条

1. **`--pv-h` 是 vh 单位，量的时候要按"真实视口"量。** 无头 Chrome 的
   `--window-size=W,H` 给出的 `innerHeight` 是 `H−151`（窗口边框+标签栏），拿它当现场
   视口会把结论系统性地算悲观（差 130px 左右）。做法是先 `--dump-dom` 读出 `innerHeight`
   再折算出对应的窗口高度 —— 目标是真实 vh 1000，就传 `--window-size=W,1151`。
   本轮第一遍就是没折，差点以为 1080p 上导轨图进不了首屏。
2. **比例这类"单一来源"必须只有一处。** 改前 `aspect-ratio` 同时出现在 `.pvwrap` 基线和
   `@media (max-height:820px)` 覆盖里，两处不一致就是黑边的一半来源。现在只有 `--pv-k`
   一处，并且在那条媒体查询旁写了"⚠️ 不要在这里改比例"。

---

## 17. 2026-10-04：实时预览黑了 5 天——监督循环只认"进程退出"

**现场报告**：接管后「实时画面」显示 `预览服务暂时没有画面（上游 HTTP 503）`。

那句话是 `console.py` 的 `/api/preview/status` 在转述上游状态码（ADR-0017 落地形态第 1 条
的既定形状），不是预览服务自己的判断。**"暂时"两个字是错的**，读一次状态就能拆掉。

### 17.1 现象与现场证据（只读，不需要进容器）

```bash
curl -s 'http://10.77.77.39:8001/api/preview/status'
# {"available":false,
#  "upstream":{"ok":false,"frames":8642060,"viewers":0,"last_frame_age_s":453587.16,
#              "uptime_s":1029736.8,"restarts":0,
#              "last_error":"[vf#0:0 @ 0x579af8f63640] More than 100000 frames duplicated"},
#  "error":"预览服务暂时没有画面（上游 HTTP 503）"}
```

| 读数 | 换算 | 结论 |
| --- | --- | --- |
| `uptime_s` 1029736.8 | ≈11.92 天 | 容器 2026-09-22 11:03 起没重启过 |
| `last_frame_age_s` 453587 | ≈5.25 天 | **最后一帧 2026-09-29 03:09** |
| `frames` 8642060 | ÷ 6.67 天 = **15.0 fps** | 死之前一路满帧 ⇒ 相机侧没问题 |
| `restarts` **0** | — | **恢复逻辑一次都没跑**，这才是缺陷 |

同一时刻 `/api/status` 里 patrol/console 一切正常（`machine.state: IDLE`、
`room.allowed:false` 第 66 天）、采图服务 `ready:true` ⇒ 只有预览这一路死了，
`data/runs` 不存在（机构零接触）。

### 17.2 死因不是那句 `duplicated`，也不是 ffmpeg

`last_error` 是 ffmpeg **最后一句 stderr**，极易被当成死因。用部署镜像里的同一版 ffmpeg
（7.1.5）做一次性判定——人为制造海量重复帧，看它是警告还是致命：

```bash
IMG=registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:<现役 tag>
docker run --rm --entrypoint sh "$IMG" -c \
  'ffmpeg -hide_banner -loglevel warning -f lavfi -i testsrc=size=64x64:rate=1 -t 3 \
     -r 1000 -q:v 7 -f image2pipe -c:v mjpeg - > /dev/null; echo EXIT=$?'
# [vf#0:0 @ 0x…] More than 1000 frames duplicated
# EXIT=0                    ← 警告：照常跑完（管道口径实测写出 3000 帧）
```

⇒ 那句是**警告**（ADR-0017 补记二 §2 把它当"源帧率探针"是**对的**），**不是**死因；它只说明
`-r` 与源帧率错位、在复制帧多花带宽。真正的死因在那条日志**之外**：ffmpeg 既不写 stdout、
也不退出，而 `-loglevel warning` 下一次卡死**不留任何一行错误**——`last_error` 于是永远
停在那句旧警告上。

**缺陷在监督循环**：`run_forever` 只等 stdout EOF（= 进程退出）这一种"挂了"，
「进程还在但哑了」完全在它之外（读 RTSP 的 socket 停住是最典型的一种）。所以它一边
**忠实地**报 503（这是它对的地方），一边**没有任何东西去救它**。
（2026-10-06 上机取证把"读 RTSP 的 socket 停住"这一类钉成了实证，见 §17.8。）

### 17.3 修法（`8d01f90`）：看门狗，按"有没有新帧"判生死

```python
FIRST_FRAME_GRACE_S = 25.0   # 这一路一帧都没出过：够连相机 + 出首帧
STALL_TIMEOUT_S     = 10.0   # 出过帧之后：10 s（≈150 帧）没有新帧就换人
```

`_watchdog` 与 `read_frames` **并行**跑；超时即 `terminate()`（5 s 不放手升级 `kill()`），
进程一死 stdout 即 EOF，**原有的退避重启路径原样接管**（不改动它）。两条细节都有单测钉住：

- 判据是"**这一路**的帧数有没有在涨"，不是全局帧龄——重启后 broadcaster 上还挂着上一路的
  旧帧，拿帧龄判会把刚拉起、还没出首帧的新进程立刻误杀（自杀循环）；
- 换掉它的理由写进 `last_error`（`画面停滞 N 秒（ffmpeg 进程还在、但没有新帧…）`），
  下一次同类故障在 `/api/preview/status` 里直接可读。

### 17.4 发版范围：**必须重建 `mushroom_patrol` 镜像**（preview 的代码在镜像里）

不是"只改取值"那一档，也不是页面-only。`PATROL_IMAGE` 一把影响 patrol / console / preview
三个，但本次只需要**重建 preview 这一个容器**（`--no-deps`，与 ADR-0017 补记二同一条路）。

```bash
# 开发机（WSL）：worktree 内容 = main@8d01f90
cd /mnt/c/Users/niucg1/WorkBuddy/Worktrees/mushroom/feat-patrol-merge-17681f41
REG=registry.cn-beijing.aliyuncs.com/ncgnewne
TAG=0.1.0-$(date +%Y%m%d%H%M%S)-$(git rev-parse --short HEAD)
docker build -f docker/Dockerfile.patrol -t $REG/mushroom_patrol:$TAG .
docker push  $REG/mushroom_patrol:$TAG
```

```bash
# 库房主机：钉 tag + 只重建 preview
cd /home/sysadmin/algorithm/mushroom_service
cp -a .env .env.bak-$(date +%Y%m%d-%H%M%S)
sed -i "s|^PATROL_IMAGE=.*|PATROL_IMAGE=registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:$TAG|" .env
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_preview
```

### 17.5 现场验收（含一条**不用等一周**的演练）

```bash
# A) 十分钟内：画面应当直接回来
curl -s 'http://10.77.77.39:8001/api/preview/status'      # available:true、restarts:0、last_frame_age_s<1
docker logs mushroom_preview 2>&1 | grep '启动转码'        # 那行会打出真实的 -vf/-r/fps

# B) 确认容器里真是这一版（部署镜像的常规判据）
docker exec mushroom_preview grep -c '_watchdog' /srv/deploy/src/deploy/preview.py   # 期望 ≥1

# C) **看门狗演练**：把 ffmpeg 冻住（SIGSTOP = 进程还在、一个字节都不再写）
docker exec mushroom_preview sh -c \
  'for p in /proc/[0-9]*; do [ "$(cat $p/comm 2>/dev/null)" = ffmpeg ] && kill -STOP ${p#/proc/}; done'
```

期望（C 之后累计 ≈15 s，然后画面自己回来）：

| 时刻 | 现象 | 说明 |
| --- | --- | --- |
| ~+10 s | 日志出现 `! 画面停滞 N 秒（ffmpeg 进程还在、但没有新帧…），换掉这个 ffmpeg` | `STALL_TIMEOUT_S` 到点 |
| ~+15 s | 进程被 **SIGKILL** 收掉（日志接着出现 `转码退出（第 1 次），1 秒后重试`） | SIGSTOP 中的进程**不理会 SIGTERM**，所以必然走 `_stop_proc` 的 5 s 超时升级路径——这正是单测里标了 `pragma: no cover` 的那一段，本演练顺手覆盖它 |
| ~+16 s | `/api/preview/status` → `restarts:1`、`last_frame_age_s<1`、`available:true`，画面恢复 | 退避 1 s 后重启 |

> 演练用的是 SIGSTOP，**不会**碰相机、控制器与采图服务；`restarts` 会从 0 变 1，
> 演练完在页面下方事件里留个记录即可（它本来就是"恢复过"的账）。

### 17.6 回滚

```bash
cd /home/sysadmin/algorithm/mushroom_service
cp .env.bak-<STAMP> .env                    # 回到 0.1.0-20260921151249-39aacfe
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_preview
```

> 回滚只影响预览：看门狗是新加的旁路逻辑，**不改变任何既有行为**（`-r`/`-vf`/`-q:v` 与
> 退避序列一个字节没动），所以回滚的动机基本只可能是"演练不满意"。

### 17.7 这一轮留下的两条结论（别再踩）

1. **`-loglevel warning` 让"静默卡死"没有任何痕迹**——这正是本次能黑 5 天的原因。看门狗把
   恢复做成了自动的，但**排障面**仍是一条欠账：真要弄清 ffmpeg 当时卡在哪，需要把
   `-loglevel` 提到 `info`（或给 RTSP 加 `-timeout`，见第 2 条）——两者都改变行为，
   本次**没做**（ADR-0017 补记三记了取舍）。
2. **RTSP 读超时只有 demuxer 那一个名字有效**：`-rw_timeout` 对 RTSP **不生效**（实测挂死），
   得用 `-timeout <微秒>`（实测 `Connection timed out`，EXIT=146）。本次未采用——它只覆盖
   "socket 读超时"一类，而看门狗覆盖全部静默类；现场链路抖动大，加短超时反而可能频繁换流。
   哪天要做，先按 ADR-0017 补记三的那条一次性容器命令验一遍再上。

### 17.8 补记（2026-10-06）：重启前的现场取证——socket 还 ESTABLISHED，ffmpeg 0 CPU

上机重启**之前**先进容器取了一次证（只读），把 §17.2 的"死因在日志之外"钉成实证：

| 取证 | 命令 | 结果 |
| --- | --- | --- |
| 进程还在 | `docker top mushroom_preview` | `ffmpeg … TIME 1-21:50:10`、`STIME Sep22` |
| **它没在忙等** | 隔 3 s 采两次 `TIME` | **两次完全相同** ⇒ 这 3 秒消耗 0 CPU。⚠️ 那一列 `13` 是**生涯平均** %CPU，别当"当前在烧 CPU"读（`docker stats` 的当前值只有 0.14%） |
| **但 socket 还连着** | 容器 netns 的 `/proc/net/tcp` | `080015AC:C85C → EE01A8C0:022A` ⇒ 本容器 → **192.168.1.238:554**，状态 `01` = **ESTABLISHED** |
| 相机最后一眼 | `GET /api/preview/frame.jpg` | 真图，OSD `2026-09-29 03:05:07` / `CAM01` ⇒ 卡死前相机完全正常 |
| 资源 | `docker stats --no-stream` | 293.7 MiB / 512 MiB、CPU 0.14% ⇒ 不是内存或算力问题 |
| 只重启了 preview | `docker ps` | `preview Up 34 s (healthy)`；patrol / console / console_web 全部 `Up 2 weeks`；`/app/data/runs` 不存在 |

**结论**：对端（DVR）停止发数据、但**没有关连接**；ffmpeg 没有读超时，于是挂在一次读上
（0 CPU、无输出、无错误）。这正是 §17.2 说的那一类——**任何"看进程还在不在"的判据都救不了
它**，只有看门狗（或读超时）能。顺带一句：这个容器在毫无产出地占着一路 ESTABLISHED 会话
空了 2 周，重启本身也该做。

**复发间隔**：容器 2026-09-22 11:03 起、最后一帧 2026-09-29 03:05 ⇒ **约 6 天 16 小时**。
2026-10-06 20:56 已现场重启（`docker compose … restart mushroom_preview`，只动这一个容器），
画面当即回来（`available:true`、`last_frame_age_s 0.05`、`frames` 重新计数）；但
**只要看门狗没上机，大约一周后必然复现**。

> 上机后有一条额外收益：下一次卡死会被看门狗**记下时刻与 `last_error`**，而不是像这次
> 静默 5 天——那时才可能拿它去对 DVR 侧的事件（为什么对端不关连接）。
> 在那之前，`docs/patrol/prod-deploy/probe-rtsp.py` 这类只读探测是查 DVR 的唯一手段。

### 17.9 2026-10-07 00:30 看门狗上机 + 实战演练（一次通过）

镜像 `0.1.0-20261007000526-b07531a`（＝`39aacfe` + `preview.py` 看门狗；构建走国内源，
见 §19）push 后**只重建 `mushroom_preview`**（`--force-recreate --no-deps`，`.env` 有备份）。
验收：10 秒 healthy；容器内 `grep -c _watchdog /srv/deploy/src/deploy/preview.py` = **2**。

**演练（§17.5 计划的那一条，实际执行）**：`kill -STOP` 冻住 ffmpeg（进程还在、不写字节）。

| 时刻 | `frames` | `restarts` | `available` | 日志 |
| --- | --- | --- | --- | --- |
| 冻住前 | 4719 | 0 | true（age 0.08） | — |
| +5 / +10 / +15 s | **4719（不动）** | 0 | false（age 5.1→15.1） | — |
| ~+10 s | — | — | — | `! 画面停滞 10 秒（ffmpeg 进程还在、但没有新帧：4719 帧后就没有了），换掉这个 ffmpeg` |
| ~+15 s | — | — | — | `转码退出（第 1 次），1 秒后重试：…`（SIGSTOP 的进程不理会 SIGTERM ⇒ 走了 5 s 升级 SIGKILL 那条路，**顺手覆盖了单测里标 `pragma: no cover` 的分支**） |
| **+20 s** | **4770** | **1** | **true**（age 0.04） | 新 ffmpeg 拉起，画面自己回来 |

⇒ **从"卡死"到"画面恢复"共约 20 秒**；同样的故障形态在 9-29 那次让预览黑了 **5 天 6 小时**
（而当时 `restarts` 是 0）。这一条是本次修复最直接的验收。

> 顺带：同一时刻（00:20）**算法侧调度器发起了生产上第一轮无人值守巡检**
> （`20261007-002002-1.jsonl`，60/60、0 失败、9m45s，见 §18.4 的 registrar）。
> 两条链路——"数据面自己会恢复"与"巡检自己会开始"——在同一个夜里各自闭环。

---

## 18. 2026-10-06：换批次（mogu-107）+ 首轮巡检成功 + 触发链路欠账

### 18.1 换批次：从生产系统取真值，不是手填

现场"已有蘑菇入库"，而门禁还停在旧批次（`mogu-103 / 2026-08-01`，第 66 天 ⇒ 不放行）。
按 §10/§14 的既定做法取真值：

```bash
# 1) 取数（口令只在 tools_mysql 容器内用，不过宿主命令行）
docker exec tools_mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" mogu -N -B -e "select id, code_num, in_time, in_day_num, in_num, info_code, update_time from mo_gu_batch where del_flag = 0 and code_num = 611 order by in_time desc"'
# 2) 先 --print 看判定，判成"本轮会巡检"才落盘（脚本自守卫）
docker exec -i mushroom_patrol python3 -m deploy.fetch_room --room 611 --from - --print  < /tmp/batch611.tsv
docker exec -i mushroom_patrol python3 -m deploy.fetch_room --room 611 --from - --out /app/data/room.yaml < /tmp/batch611.tsv
# 3) 宿主同盘原子替换（install 会先 unlink，daemon 可能撞到"文件不存在"）
cd /home/sysadmin/algorithm/mushroom_patrol/configs
cp -a room.yaml room.yaml.bak-20261006 && cp ../data/room.yaml room.yaml.new && mv room.yaml.new room.yaml
```

结果：611 库现行批次 **`mogu-107`，`in_time=2026-09-30`**（生产口径第 7 天 ⇒ 我们口径第 **6** 天，
落在 2–25 窗口内）；`/api/room` 立刻 `allowed:true`。
**不需要重启任何容器**——`patrol_serve` 的 `room_fields()` 与 daemon 都是每轮现读 `room.yaml`
（`m1.py` 传的是**路径**不是读好的对象，见其注释）。

### 18.2 首轮巡检：60/60，全成功（生产上第一次跑成）

手动投一次：`POST :8001/api/patrol/run?reason=batch-mogu-107-day6-first-round&by=agent`

| 项 | 结果 |
| --- | --- |
| 轮次 | 21:03:05 → 21:12:49（9 分 44 秒） |
| 站点 | `n_stations_seen: 60`、`n_results: 60`、**`n_failures: 0`**、`aborted: false`、`status: ok` |
| 节奏 | 稳定 9.2 s/站 |
| 落库 | prod 索引 60 行全 `ok=1`，每行带 `room_id=611 / entry_date=2026-09-30 / batch_no=mogu-107`，S101→S512 |
| 机构 | 收尾回零，`state: IDLE` |
| outbox | 0 条滞留（同步正常） |

**顺带实测掉一个悬念**：全程**没有**停 `mushroom_preview`（它的 ffmpeg 一直占着一路 RTSP），
60 站抓图 0 失败、节奏不变 ⇒ ADR-0017 §4 担心的"预览 + 抓拍并发"有了**整轮**实测证据
（此前只有协商层探测）。

### 18.3 ⚠️ 触发链路欠账：算法侧的 registrar **从未部署**

"每 3 小时自动巡检"是 ADR-0012 §6，但现场从未发生（最后一次触发是 2026-09-17 的**手动**验收）：

| 检查 | 证据 |
| --- | --- |
| 算法侧代码 | `mushroom_solution` 容器里 `PATROL_RUN_URL` 出现 **0 次**；环境变量里也没有 |
| 算法侧镜像 | `0.1.0-20260320172444-a615909`（**2026-03-20**）——早于 ADR-0012（09-14） |
| 它自己的调度 | `hourly_text_quality_inference`（`minute=25`）自 **2026-09-13 01:25** 起被连续跳过 **285 次**（`maximum number of running instances reached`）⇒ 一个实例卡了 3 周半（日志里对应 `mushroom_image_encoder._call_llama_api` 270 s 超时） |
| 宿主 | 无 crontab、无 systemd timer |

**代码其实早就写好了**：`src/scheduling/tasks/register_patrol_jobs.py`（`hour=*/3, minute=20`，
只 POST 一次就走；409 视为正常）。问题在**发布**。

**为什么不能"直接部署 main"**：main 的 `src/` 含一份**在建重构快照**（`cba6a40` 自述
"非本次合并所写"；`a615909..main` 共 80 文件 +3618/−1209：新 `segmentation/` 包、
`storage/models` 落库层、`vision/mushroom_image_encoder.py` 重写）。那是算法侧的独立发版决定。

**已做的**：分支 **`alg/patrol-trigger` @ `6bedc5c`**，从现役基线 `a615909` 只摘两处
（新增 `register_patrol_jobs.py` + `tasks/__init__.py` 注册），其余与现役镜像**逐字节一致**。
在有 `codeenigma` 的机器上跑 `bash docker/build.sh` 即可（⚠️ `build.sh` 在缺 codeenigma 时
**静默退回不加密构建**，不加密产物不符合 ADR-0012 §3——构建日志里必须看到
`Building encrypted version` 与 `✓ CodeEnigma runtime file found`）。

**同期已清掉卡死的 job**：`docker compose -f mushroom_solution.yml restart mushroom_solution`
（只动这一个容器；10 s 内 healthy）。启动日志确认 `每小时文本/质量任务已添加 (每小时第25分钟执行)`，
无新 skip。⚠️ 根因（一个实例挂住 + APScheduler 默认 `max_instances=1`）未修，**可能复发**；
main 上重写过的 `mushroom_image_encoder.py` 也许已经处理了那次超时，属算法侧的判断。

> 在算法镜像重建之前，**没有任何东西会触发巡检**。要么等 `alg/patrol-trigger` 构建上线，
> 要么先用宿主 cron 顶上（`POST :8001/api/patrol/run`；端点本身有 fail-closed 门禁与
> "同一时刻只认一条待处理请求"的 409 兜底）。

### 18.4 2026-10-06 深夜：算法侧 registrar 发版完成（`121f1b9`）

**产物**：`registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_solution:0.1.0-20261006222420-121f1b9`
（digest `sha256:1f93321c…`，1.78GB，`encrypted: true`，`obfuscation_tool: codeenigma`）。
分支 `alg/patrol-trigger` = `a615909` + 两处（registrar + 下面那个缩进修复）。

**构建环境（本机，WSL）**——三条都是踩出来的：

| 坑 | 症状 | 解法 |
| --- | --- | --- |
| codeenigma 直连 PyPI 太慢 | `uv tool install` 16MB 依赖下载卡 10 分钟超时 | `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple`，**3 秒**装完 |
| 缺 `poetry` | 混淆第 [2/3] 步 `Creating runtime package` 报 `No such file or directory: 'poetry'` → **出不了 runtime .so** | `uv tool install poetry` |
| runtime ABI 必须对上目标镜像 | 默认 Python 3.14 装出来的 `.so` 是 `cpython-314`，进 3.12 的镜像必炸 | `uv tool install --python 3.12 codeenigma` → `codeenigma_runtime.cpython-312-x86_64-linux-gnu.so` |

**基线里本来就有的两个缺陷（都在本分支里补掉了）**：

1. `src/vision/get_env_status.py` **第 205 行缩进错误**——`python -m py_compile` 都过不去，
   `codeenigma` 直接中止（`find src -name '*.py' | xargs py_compile` 全量扫过：179 个文件里
   只此一个）。而**线上那份镜像里这个模块是混淆成功的** ⇒ 当时构建机的工作区是脏的
   （本地改好没提交，tag 记的是 HEAD）。不补这一处，加密构建必失败。
2. Dockerfile 的 `ARG ENCRYPTED=false` **声明在用它那个 `RUN` 的后面**，所以那个判断恒走
   else 分支、日志永远打 `Note: runtime present in unencrypted build`。**只是账目显示**
   （混淆在 docker 之前完成），别被它误导。

**发版与踩坑**：

```bash
# 本机（WSL）：构建 + 只推版本 tag（**没动 :latest**）
cd ~/alg-build && PUSH_IMAGE=false bash docker/build.sh
docker push registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_solution:0.1.0-20261006222420-121f1b9
```

⚠️ **现场 compose 的 tag 是硬编码在第 73 行、而且比线上跑的还旧**
（compose `e4d3a2f`/2026-02-24 vs 实际 `a615909`/2026-03-20）⇒ 发版必须改这一行。
⚠️ 改的时候**必须带行地址**：
```bash
sed -i '/^[[:space:]]*image:.*mushroom_solution:/ s|mushroom_solution:[^"]*|mushroom_solution:'"$TAG"'|' "$COMPOSE"
```
不带行地址的 `s|\(mushroom_solution:\)[^"]*|\1TAG|` 会把**服务名那一行**
（`  mushroom_solution:`）一起改成 `  mushroom_solution:0.1.0-…` —— YAML 立刻
`could not find expected ':'`。本次就踩了（`up -d` 因 `&&` 链中止**没执行**，容器没被换，
从 `.bak-20261006-223010` 恢复后重做）。
⚠️ 还有一个与本任务无关但会咬人的：**`| head -N` 截断管道会把远端命令一起带走**（SIGPIPE），
本次第一次 `up -d` 就是这样"拉完镜像但没重建"。远端命令的输出用**文件重定向**，别接 `head`。

**验收（逐条过）**：

| 检查 | 结果 |
| --- | --- |
| 容器 | `mushroom_solution` 换到新 tag、`healthy`（15 s） |
| **调度器注册了巡检任务** | 启动日志 `[SCHEDULER] 巡检任务已添加：每 3 小时第 20 分触发一次（只投请求，不等它跑完）` |
| 触发地址可达（registrar 吞异常，这一步必须单独验） | 容器内 `GET http://172.17.0.1:8001/api/room` → **200**（`allowed:true / mogu-107 / 第 6 天`）；`GET /api/patrol/run` → 200 |
| 巡检侧未受影响 | `mushroom_patrol/console/console_web/preview` 保持 `Up 2 weeks / 2 hours` |
| 下一次自动触发 | 22:34 完成部署 ⇒ 最近一档 **00:20**（`hour=*/3` ⇒ 00/03/06/09/12/15/18/21 时） |

**遗留（算法侧既有问题，非本次引入）**：日志里 `vision.mushroom_image_encoder._call_llama_api`
**270 s 超时**（`Request timed out`）与 MLflow `403 Invalid Host header - possible DNS rebinding`
反复出现——这正是 18.3 那个 `hourly_text_quality_inference` 卡死 3 周半的成因家族。
main 上重写过的 `mushroom_image_encoder.py`（`cba6a40` 那张 WIP 快照里）也许已处理，
需算法侧判断。

---

## 19. 2026-10-09 / 2026-10-10：Z 轴方向改回 ADR-0018（只换标签，不动机器）

> ⚠️ **本节结论已被 §20 推翻**：当天稍晚用户在现场直接观察，按 `Z+` 机构**往上**走——正方向
> 是"上"，详见 §20。本节的"复核 `jog Z 40` 往下"结论作废。

**背景**：2026-10-09 按"从回零位 Z=0 向 +Z 走到行程尽头触发**正限位**（落点 Z≈210.15）"
把 Z 的框架改判成"原点在底端、`Z+ = 向上`"（`7169156`）。那条实测只证明"回零落在负限位
那一端"，而"负限位在哪端"正是要证明的东西——**循环论证**，当时并没有肉眼核对 +Z 到底是上
还是下。

**2026-10-10 现场复核**（直接看机器，不看屏幕）：按「回零」Z 滑台仍然**往上**到顶端、
Z=0 在顶端；`jog Z 40` 机构**往下**走（控制器 Z+ = 下）。所以 Z 的框架仍是 ADR-0018：
**原点在顶端（靠近电机端）、向下为正**。`home_dir=HOME_DIR_NEGATIVE`（=2，负限位）与行程
`0…212` 自始至终没变——用户看到的"回零方向是正限位"其实是**页面把"上"标成了 `Z+`**，
把标签改回即可，机构一个字节都没动。

**改动（`3537d00`，只换标签与派生量）**：

| 层 | 改动 |
| --- | --- |
| `patrol.motion_profile` | `positive_towards` `"up"`→`"down"`（`home_dir`、行程不动） |
| `patrol.framing` | `default_sign_z()` 由它派生 ⇒ **+1**（docstring 同步） |
| `web/console/index.html` | 平面图标题/aria、地图 Z 映射（去掉原点在底端的翻转）、点动方向盘（↑=Z−、↓=Z+，键盘同步）、`#envlbl` 文案 |
| 测试 | `test_motion_profile`（down）、`test_framing`（sign_z=+1）；`ruff` 通过 |
| 文档 | `console-ui/spec.md`、`console-ui/verify-page.js`、`prod-deploy/station-retarget.md` §8.2、`deploy/README.md` |

**发版（重建 `mushroom_patrol` 镜像 + 换页面）**：

```bash
# 开发机（WSL）：从 main@3537d00 构建并推版本 tag（没动 :latest）
cd /mnt/d/code/mushroom
TAG=0.1.0-20261010152748-3537d00
docker build -f docker/Dockerfile.patrol -t registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:$TAG .
docker push  registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:$TAG
```

```bash
# 库房主机：钉 tag + 只重建 patrol/console（preview 不动）
cd /home/sysadmin/algorithm/mushroom_service
cp -p .env .env.bak-20261010-153441
sed -i 's#^PATROL_IMAGE=.*#PATROL_IMAGE=registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0-20261010152748-3537d00#' .env
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol mushroom_console
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_patrol mushroom_console
```

页面是 `mushroom_console_web` 的 bind-mount（`../mushroom_patrol/web`），**只换文件**：先
`cp -p index.html index.html.bak-20261010-153324`，上传到 `index.html.new`、核对 sha 再
同盘 `mv` 原子替换（不重建 console_web）。

**验收**：

| 检查 | 结果 |
| --- | --- |
| 容器 | patrol/console 换到新 tag、`healthy`、`restarts=0` |
| 镜像内单源 | `positive_towards=down`、`default_sign_z()=+1.0`、`home_dir=2`、`travel 0…212` |
| 页面 | `/`（nginx :8002）4×「Z 向下」、0×「Z 向上」；字节 sha 与本地一致 |
| 接口 | `/healthz {"ok":true}`、`/api/grid z 0…212` |
| 未波及 | `mushroom_preview` 仍 `station-calibration-…`；`mushroom_console_web` 未重建 |

**回滚**：`.env` 换回 `.env.bak-20261010-153441`（tag `0.1.0-20261010140703-fde98e1`，10-09
的"向上"版）、页面换回 `index.html.bak-20261010-153324`，再
`up -d --force-recreate --no-deps mushroom_patrol mushroom_console`。

**教训**：方向这种事，"撞到哪个限位"证明不了"哪端是原点"——**得用眼睛**（或回零前后读
位置）。同一台机器上 Y 与 Z 的原点都在**靠近电机那一端**，这条不变量由 `home_position`
派生并可断言。

## 20. 2026-10-10（同日晚些）：Z 轴方向定稿 `Z+ = 向上`（现场用户观察，撤销 §19）

**背景**：§19 依"现场复核 `jog Z 40` 往下"把 Z 定成"向下为正"（`3537d00`）。当天用户自己
在机器旁操作后指出：**按 `Z+` 机构往上、`Z−` 往下**，且"方向键（键盘 ↑/↓、页面方向盘）
也要同步"。这正是 `7169156` 的框架（`positive_towards="up"`）——§19 那次复核只有约 2 mm
位移，把方位看反了，结论作废。

**定稿框架**（`home_dir`/行程与 §19 完全相同，只把"正方向"翻回"上"，代码 `40c8434`）：

| 层 | 值 |
| --- | --- |
| `patrol.motion_profile` | `positive_towards="up"`；`home_dir=HOME_DIR_NEGATIVE`(=2)、行程 `0…212`、`home_position=0.0` 不变 |
| `patrol.framing` | `default_sign_z()` 由它派生 ⇒ **−1** |
| 页面 | 平面图 `Z 向上`、地图 Z 映射（z 越大越靠上）、方向盘 ↑=`Z+` / ↓=`Z−`、键盘 ↑/↓ 同步、`#envlbl` "Z 向上为正" |
| 测试 | `test_motion_profile`（up）、`test_framing`（sign_z=−1）；并补上 10-09 漏改的两个 framing 用例（一直按 `+1` 期望、在 up 框架下红着） |
| 文档 | `console-ui/spec.md`、`console-ui/verify-page.js`、`station-retarget.md` §8.2、`deploy/README.md` |

`verify-page.js` 195/195；patrol 全量 `690 passed`（唯一红 `test_migrate_z_frame` 为既有、与本改无关）；`ruff` 过。

**发版**（重建 `mushroom_patrol` 镜像 + 换页面）：

```bash
# 开发机（WSL）：从 main@40c8434 构建并推版本 tag（没动 :latest）
cd /mnt/d/code/mushroom
TAG=0.1.0-20261010155834-40c8434
docker build -f docker/Dockerfile.patrol -t registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:$TAG .
docker push  registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:$TAG
# digest sha256:09b7abc4…；image id sha256:d64b6efd…
```

```bash
# 库房主机：钉 tag + 只重建 patrol/console（preview / console_web 不动）
cd /home/sysadmin/algorithm/mushroom_service
cp -p .env .env.bak-20261010-160140
sed -i 's#^PATROL_IMAGE=.*#PATROL_IMAGE=registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0-20261010155834-40c8434#' .env
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol mushroom_console
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_patrol mushroom_console
```

页面是 `mushroom_console_web` 的 bind-mount（`../mushroom_patrol/web`），**只换文件**：先
`cp -p index.html index.html.bak-20261010-160021`，上传到 `index.html.new`、核对 sha
（`478a95e5…`）再同盘 `mv` 原子替换（不重建 console_web）。

**验收**：

| 检查 | 结果 |
| --- | --- |
| 容器 | patrol/console 换到 `0.1.0-20261010155834-40c8434`、`healthy`、`restarts=0` |
| 镜像内单源 | `positive_towards=up`、`default_sign_z()=-1.0`、`home_dir=2`、`travel 0…212`、`home_position=0.0` |
| 页面 | `/`（nginx :8002）4×「Z 向上」、0×「Z 向下」；字节 sha `478a95e5…` 与本地一致 |
| 接口 | `/healthz {"ok":true}`、`/api/grid z 0…212`；机器 `IDLE`、无进行中轮次 |
| 未波及 | `mushroom_preview` 仍 `station-calibration-…`；`mushroom_console_web` 未重建 |

**回滚**：`.env` 换回 `.env.bak-20261010-160140`（tag `0.1.0-20261010152748-3537d00`，§19 的
"向下"版）、页面换回 `index.html.bak-20261010-160021`，再
`up -d --force-recreate --no-deps mushroom_patrol mushroom_console`。

**教训**：Z 的方向在同一天被翻了两次——根因是"同一事实有三处副本"（`positive_towards`、
页面文案、`framing.sign_z`）且现场结论互相打架。定论只能来自**用户在机器旁直接看"按哪个
方向、执行器往哪走"**，而且是看**轴命令符号**（`jog Z +N` 往上 ⇒ `+Z = 上`），不要从
"撞到哪个限位"反推原点。


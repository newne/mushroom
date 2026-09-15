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
docker compose --profile patrol pull mushroom_patrol mushroom_console mushroom_console_web
docker compose --profile patrol up -d mushroom_patrol mushroom_console mushroom_console_web
docker compose --profile patrol ps
```

三个容器各自是什么：

| 容器 | 角色 | 端口 | 说明 |
| --- | --- | --- | --- |
| `mushroom_patrol` | `patrol-serve` | 无 | **唯一持有控制器**的进程；被触发才跑一轮，同时执行手动指令 |
| `mushroom_console` | `console` | 8001 | 收请求、读写协调文件、提供 `/api/*`；**不连控制器** |
| `mushroom_console_web` | nginx | **8002** | 发页面 + 把 `/api` 与 `/healthz` 反代给 console（单一 origin） |

## 4. 验收（逐条过，别跳）

```bash
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

## 5. 回滚

```bash
cd $BASE/mushroom_service
docker compose --profile patrol stop mushroom_patrol mushroom_console mushroom_console_web
# 需要彻底撤掉时
docker compose --profile patrol down
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

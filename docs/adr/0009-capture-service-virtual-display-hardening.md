# ADR-0009：采图服务的虚拟显示加固——失败必须可见、可自愈

状态：已实施并验证（2026-09-12，prod `10.77.77.39`）
相关：`capture/INTEGRATION.md`、`docs/patrol/prod-deploy/gap-list.md`、ADR-0002（参数单源）

## 背景

采图服务以容器形态运行在 prod：

- compose 项目 `xcloudsdk_py_offline_20260120_175307`，镜像 `xcloudsdk-py:0.1.0`
- 对外 `GET :7003/{dynamic,fast,pool,capture}`，容器内 `Xvfb :99` + `libXCloudSDK.so`
- 截图依赖隐藏的 X11 窗口，所以**没有一个可用的 X server，每次采图都会失败**

历史故障的特征是一句话：

> **进程活着、`/healthz` 返回 200、而每一次采图都 500。**

排查后确认了四个结构性缺陷。它们**各自独立成立**，不依赖具体触发原因：

| # | 缺陷 | 证据 |
| --- | --- | --- |
| 1 | Xvfb 起不来时**只打一行警告就继续启动**服务 | 故障响应里 `headless_mode: true, display: ""` 正是入口里 `unset DISPLAY` 那条分支 |
| 2 | 就绪判定用 `[ -S socket ]`，**残留 socket 文件会骗过它** | 实测：`bind` 后 `close` 的 socket 仍满足 `-S`，但连接被拒 |
| 3 | Xvfb 是**无人监管**的后台进程，它死了 python 照跑 | 原入口 `Xvfb … &` 之后直接 `exec python3` |
| 4 | `/healthz` **不反映显示器**，拿它当 docker healthcheck 等于没探 | `DOCKER.md` 自述"只表示进程存活（应该始终返回 200）" |

⇒ 于是**没有任何信号能触发 `restart: unless-stopped`**，故障可以一直躺着，直到有人手工重启容器。
这就是"老出问题"的机制：不是随机故障，是**故障不可见 + 不可自愈**。

## 决策

**不改镜像、不改厂商的 `docker-compose.yml`**，用 compose 覆盖层 + bind-mount 入口替换：

| 手段 | 内容 |
| --- | --- |
| `docker-compose.override.yml` | 覆盖 `entrypoint`、`init: true`、`TZ=Asia/Shanghai`、healthcheck |
| `capture-entrypoint.sh` | 真连接就绪探测 + **fail-closed** + 运行期 watchdog |
| `capture-healthcheck.py` | 独立健康检查：X socket 真能连 + HTTP `ready` |
| `capture-status.sh` | 宿主端体检脚本，`--fix` 重建 |

## 理由

### 为什么 fail-closed，而不是"尽力而为继续跑"

原来的降级行为是**最坏的一种**：服务照常接受请求并把失败推迟到每一次采图，
而所有常规探针（进程、端口、`/healthz`）都显示正常。
改为 `exit 1` 后，`restart: unless-stopped` 会持续重试：
**故障从"静默 500"变成"可见的 crash loop"**，且大部分瞬时原因（例如 display 被占用）
能自愈。宁可不可用且吵闹，也不要可用且静默错误。

### 为什么运行期判据是"display 还能不能连"，而不是"Xvfb 进程还在不在"

`进程活着 ≠ 显示器可用`（进程可能已经卡死），而 socket 探测与就绪判定用的是**同一把尺子**。
顺带纠正一个我最初的猜测：`kill -0` 对已死的子进程会因**僵尸**而误判——实测在
这套镜像（Debian/dash）里**不成立**（dash 会回收后台子进程，`/proc` 条目消失、`kill -0`
返回非 0）。换判据是为了覆盖面，不是因为僵尸问题。

### 健康检查**故意不抓拍**

采图服务是**单 worker 串行**的，且相机 `192.168.1.238` **与老系统共用**。
健康检查若按固定周期真去抓一张，会与业务互相排队并干扰共用的老系统。
因此只查「X socket 真能连」+「HTTP `ready`」，不碰相机。

### 为什么不用宿主上的 `xcloud-capture.service`

宿主 `/etc/systemd/system/xcloud-capture.service`（当前 `disabled`）指向**另一套安装**
`/home/sysadmin/algorithm/image_capture`，并且会和容器**抢同一个 `:99` 和 `:7003`**。
切换到它意味着改动一套未经验证的安装路径、并把容器里已验证的挂载与配置全部重做，
收益不足。**保留容器，只加固它的入口。**

### 为什么硬编码 display 为 `:99` 而不是动态挑一个

Xvfb 会自动回收**死进程**留下的锁、也会覆盖残留 socket（两者都实测确认），
所以固定的 `:99` 本身不是问题源。动态挑号反而让 healthcheck 与入口可能指向不同的
display。用 `XVFB_DISPLAY` 单点配置，并在覆盖层里同时喂给入口与 healthcheck。

## 后果

- **正面**：Xvfb 失败 → 容器退出并重试；Xvfb 运行期死亡 → 容器重启；
  `docker healthcheck` 首次真正覆盖显示器与 SDK 就绪状态；容器日志时区与宿主一致（CST）。
- **代价**：Xvfb 起不来时端口会短暂不可用（crash loop），而不是"能连但总失败"。
  这是刻意的取舍。另外 watchdog 每 5 s 起一次 `python3` 探针（约 1% CPU）。
- **约束**：`restart` 必须是 `unless-stopped`（不能写成 `on-failure`）——
  服务收到 `SIGTERM` 时会**优雅退出、退出码 0**，`on-failure` 不会重启它。
- **运维**：只能用 `docker compose up -d`，**不能**带 `-f`（显式 `-f` 会跳过覆盖层）。

## 验证（2026-09-12 实测）

| 项 | 结果 |
| --- | --- |
| 入口/健康检查语法（`sh -n`、`py_compile`） | ✅ |
| `docker compose config` 合并结果 | ✅ `entrypoint`/`init`/`TZ`/`XVFB_DISPLAY`/healthcheck/挂载全部正确 |
| 预检 A：Xvfb 引导（独立 `:95`，自检模式） | ✅ 就绪，退出码 0 |
| 预检 B：**fail-closed**（故意给坏的 Xvfb 参数） | ✅ `❌ Xvfb 未能在 10s 内就绪` → **退出码 1** |
| 预检 C：健康检查对现役服务 | ✅ `[healthy] X socket … ok; GET /healthz ok (ready)` |
| 回归 A：`docker restart`（历史故障类型） | ✅ 15 s 内回到 `healthy` |
| 回归 B：**运行期杀掉 Xvfb** | ✅ `RestartCount` 0→1，容器被拉起并回到 `healthy` |
| 端到端抓拍（238, `storage=local`） | ✅ `http=200`，`"display":":99"`，`snap_result=0` |

顺带发现并修正：**`/healthz` 在 `ready:false` 时也返回 200 + `ok:true`**（实测：容器刚起时），
只看 `ok` 会把"SDK 未就绪、采图必然 503"判成健康。健康检查已改为要求 `ready=true`。

## 未确定

最近一次故障的**具体触发点**未确定——那个容器已被重建，现场已不存在。
本 ADR 不依赖它：四条缺陷各自成立，且加固覆盖了**所有会走到"静默 headless"的路径**。

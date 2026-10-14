# 02. 内部离线标注环境：X-AnyLabeling（Server 已装、监听 9213）

- **归属：** 部署与运行态 = 发版官；标注规范 = 测量师（票 `01` §4）；裁决 = 用户/Chief
- **状态：** **Server 已安装并在内部环境运行（9213）**；**客户端与模型权重未装**；断网可用性 = **部分成立**（§4 逐类给结论）
- **决策变更：** Plan §5.1 原建议内部自部署 **CVAT**；**用户 2026-10-14 15:19 改裁 X-AnyLabeling 方案**（参考 `CVHub520/X-AnyLabeling-Server` 的 `docs/source/en/get_started.md`），**端口 9213**。CVAT 相关取证停止，本票按 X-AnyLabeling 落地。
- **权威依据：** `docs/patrol/growth-algo/plan-2026-10-14.md` §5；上游仓库文档（已逐条引用，见 §6）
- **边界：** **只在内部环境（本机 WSL2）**。**不连生产 10.77.77.39、不用生产凭据、不挂 docker.sock、不动仓库生产代码。**

---

## 1. 一句话结论

**Server 装好了、9213 通了，但它现在是个"能跑的空框架"：`models_loaded=0`。**
人工标注（多边形/掩码/属性）**不依赖它**——那部分靠桌面前端；Server 的作用是**自动/辅助标注的推理后端**，而推理后端需要**模型依赖 + 权重**，二者都还没装（§7 待决）。

---

## 2. 部署形态（实际做的，不是设想）

| 项 | 实际值 | 证据 |
| --- | --- | --- |
| 内部环境 | 本机 WSL2（Linux x86_64）：22 核 / 15 GiB 内存 / `/` 可用 **690 GB** / **无 GPU** | `nproc`、`free -h`、`df -h`、`nvidia-smi: command not found` |
| 代码位置 | `/home/newne/xanylabeling-server`（**不在仓库里**，仓库一个文件没动） | `git -C ... rev-parse HEAD` |
| 代码版本 | HEAD `8efd68c8f4d0911886b37b2eb89c1c16fd386d91`（`git describe` = `v0.0.12-1-g8efd68c`，距 tag `v0.0.12` 一个提交） | 见 §3 复现命令 |
| 运行用户 | `newne`（uid/gid 1000:1000）；**`/opt` 不可写** ⇒ 装在家目录 | `id`、`test -w /opt` |
| 解释器 | **python 3.12.13**（uv 托管；系统自带的是 3.14.4，且**无 pip/ensurepip**） | `.venv/bin/python -V` |
| venv | `/home/newne/xanylabeling-server/.venv`（**独立**；**未触碰共享 Windows venv**，任何 `uv pip` 都显式 `--python` 指向它） | `uv venv --python 3.12` |
| 安装方式 | `uv pip install -e .`（**只装核心框架**，未装 `[all]`）⇒ **71 个包** | `requirements-frozen-20261014-152459.txt`（71 行） |
| 配置 | `/home/newne/xanylabeling-config/server.yaml`（**只写覆盖项**，不动上游 `configs/server.yaml`）：`host 0.0.0.0` / **`port 9213`** / 日志 100 MB 轮转 × 30 天 | 文件内容见 §3 |
| 启动方式 | `setsid nohup .venv/bin/x-anylabeling-server --config <上面那个> > logs/server.out 2>&1 &`（**不用 systemd**） | `pgrep -af` pid=2992 |
| 监听 | **`0.0.0.0:9213`**（Windows 侧 `127.0.0.1:9213` 亦可达 —— WSL2 本地转发） | `ss -ltnp`、Windows `Invoke-WebRequest` |
| Docker 镜像 | **无** —— 上游仓库根本**没有 Dockerfile/镜像**（仓库根清单里没有）；本次是 venv 部署。容器化见 §7 待决 4 | GitHub contents API 实测 |

---

## 3. 实测证据（可逐条复验）

```
# 1) 版本与代码点
git -C /home/newne/xanylabeling-server rev-parse HEAD     # → 8efd68c8f4d0911886b37b2eb89c1c16fd386d91

# 2) 服务在跑、在听 9213
pgrep -af x-anylabeling-server
ss -ltnp | grep 9213          # → 0.0.0.0:9213 users:(("x-anylabeling-s",pid=2992,fd=16))

# 3) 健康与模型清单
curl -s http://127.0.0.1:9213/health
# → {"status":"healthy","models_loaded":0,"timestamp":"2026-10-14T07:25:33.707371Z"}
curl -s http://127.0.0.1:9213/v1/models
# → {"success":true,"data":{}}

# 4) Windows 侧同端口（标注员机器视角）
#    (Invoke-WebRequest http://127.0.0.1:9213/health).Content → 同上 200
```

**启动日志原文（决定性的三行）** —— 说明"为什么 `models_loaded=0`"：

```
WARNING | app.core.registry | Failed to import model module app.models.geco2: No module named 'torch'
WARNING | app.core.registry | Failed to import model module app.models.segment_anything_2: No module named 'torch'
WARNING | app.core.registry | Failed to import model module app.models.segment_anything_3: No module named 'torch'
INFO    | app.core.registry | Loading 1 enabled model(s)...          # 默认 models.yaml 只启用 yolo11n
ERROR   | app.core.registry | Failed to load model [yolo11n]: No module named 'ultralytics'
WARNING | app.core.registry | No models were successfully loaded
INFO:     Uvicorn running on http://0.0.0.0:9213
```

⇒ **不是配置错、不是端口错**：核心框架不含 torch/ultralytics，这正是"只装核心"的必然结果（§7 待决 2）。

**API 面（`/openapi.json` 实测，共 11 条路由）**：
`GET /health`、`GET /v1/models`、`GET /v1/models/{model_id}/info`、`POST /v1/predict`、
`POST /v1/video/{init,prompt,propagate,propagate/stream}`、`GET /v1/video/status/{task_id}`、
`POST /v1/video/{cancel,cleanup}/…`

**日志与配置文件路径**：
- 启动日志：`/home/newne/xanylabeling-config/logs/server.out`
- 应用日志：`/home/newne/xanylabeling-config/logs/server.log`（100 MB × 30 天）
- 覆盖配置：`/home/newne/xanylabeling-config/server.yaml`

---

## 4. 断网可用性核实（Plan §5.1 要求"镜像/依赖/权重/前端资源**各自**验证"）

**结论先说：自部署 ≠ 断网可用；本环境断网时"标注 API 可用、其余多半不可用"。逐类：**

| 类别 | 现状 | 断网判定 | 证据/依据 |
| --- | --- | --- | --- |
| **运行时依赖**（Python 包） | 71 个核心包**已全部落盘**在 `.venv` + 冻结清单 | **成立** ✅ | `.venv` 自足；`requirements-frozen-*.txt` 71 行 |
| **前端资源** | `/docs`（Swagger UI）**引用公网 CDN** | **不成立** ❌（有缺口，但**只影响文档页**） | 实测 `/docs` HTML 里三个外链：`cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js`、`…swagger-ui.css`、`fastapi.tiangolo.com/img/favicon.png` ⇒ 断网时 `/docs` 白屏，**`/health`、`/v1/models`、`/v1/predict` 不受影响**（本地路由；`/openapi.json` 也是本地生成）。修法见 §7 待决 6 |
| **模型权重** | **一个都没有** | **不成立** ❌（必须先预置才谈离线） | 权重不在仓库、不在 venv：`ultralytics` 默认联网取 `.pt`，`transformers` 默认连 HF Hub。离线落地要求：权重文件预置到本地目录 + 模型 yaml 的 `model_path` 写**本地绝对路径** + `HF_HUB_OFFLINE=1` |
| **镜像** | 本次**不是**镜像部署 ⇒ 不适用 | 不适用 | 若将来要"一次打包带走"，用 `docker save/load`；**本机 docker 直连 Docker Hub 不通**（TCP 443 失败），但**代理镜像 `docker.1ms.run` 可用**（实测 `hello-world` 拉取成功、`cvat/server` manifest 可取）——留档备查，不属本票 |
| **桌面前端**（标注员用的 GUI） | **未装** | **未验** ⚠️ | X-AnyLabeling 客户端是 **PyQt6 桌面程序**（不是网页），跑在标注员机器上；它自带模型下载与**启动自动更新检查**（`--no-auto-update-check` 可关）。离线要求：关自动更新 + 预置其模型目录。**装在哪台机器见 §7 待决 1** |
| **服务出网行为** | 启动到 `health 200` 全程**无任何外联** | 成立 ✅ | 启动日志无外联请求；无模型 ⇒ 无权重下载 |

---

## 5. 存储与权限

| 项 | 现状/建议 | 说明 |
| --- | --- | --- |
| 代码 / venv / 配置 / 日志 | `/home/newne/xanylabeling-server/`、`/home/newne/xanylabeling-config/` | 属主 `newne:newne`（1000:1000）；`/opt` 不可写所以不装那里 |
| 磁盘余量 | `/` **1007 G，用 267 G（28%）**，余 690 G | 装 `[all]` 前先看 §7 待决 2 的体积账 |
| 日志轮转 | 100 MB × 保留 30 天 | 已写进覆盖配置 |
| **帧与标注档案（待建）** | 建议 `/home/newne/xanylabeling-data/{images,labels,exports}` | **不进仓库、不进生产、不进任何镜像**；帧从生产只读拷贝（人工搬运，不经网络直连） |
| 备份 | **待定（§7 待决 5）** | **人工标注是唯一不可再生的资产**（帧可以从生产重取，人工标注不能重来）⇒ 必须有备份点 |
| 权限红线 | 无生产凭据、不挂 docker.sock、不连 10.77.77.39 | 本票全程只在本机 |

---

## 6. 导出格式与回读校验（Plan §5.2/§5.3 ↔ 工具能力）

### 6.1 上游给的通道（`docs/en/cli.md` 原文核实）

- **原生格式 = XLABEL JSON**：上游 FAQ Q1 原文"stores all annotation information in a human-readable format, including shape data, labels, **attributes**, and metadata"
  ⇒ **属性保真通道 = 原生 JSON**（`ObjectAnnotation` 的 `occluded/truncated/association_valid/measurement_valid/rejection_reason/review_status/bag_group_id`、`BagTopAnnotation` 的 `baseline_valid`、`FrameAnnotation` 的 `cap_state/quality_tags` 都能挂在属性/元数据上）。
- **导出任务（10 个 `xlabel2*`）**：`yolo[detect,segment,obb,pose]`、`voc[detect,segment]`、`coco[detect,segment,pose]`、`dota`、**`mask`**、`mot`、`mots`、`odvg`、`vlmr1`、`ppocr[rec,kie]`
- **导入任务（9 个）**：`coco2xlabel[detect,segment,pose]`、`mask2xlabel`、`yolo2xlabel`、`voc2xlabel`、`dota2xlabel`、`mot2xlabel`、`ppocr2xlabel`、`vlmr12xlabel`、`odvg2xlabel`
- **掩码**：`xlabel2mask` 对应 Plan §5.2 的 `visible_mask`；`coco:segment` / `mots` 也带掩码或多边形
- **转换是"星形"的**：所有转换都以 XLABEL 为中转（上游 FAQ Q5："Can I convert directly between formats? Not currently"）

### 6.2 **必须实测的一条**（不许假设）

**COCO / VOC / YOLO 这些标准格式是否携带属性 —— 上游文档没有明说，而这些标准本身没有属性位。**
⇒ 落地第一件事就是**一帧真图的往返回读**（Plan §5.5 原话"转格式后必须回读检查"）：

```
帧：2880×1616 现场真图（生产只读取，票 21 红线口径）
标：bag(1) + cluster(1 袋 1 簇) + 袋顶折线 + cap(可辨认时) + 属性若干
链：人工标注 → XLABEL JSON → xlabel2coco / xlabel2mask → 回读
判据（四条，逐条落证据）：
  ① 对象数与类别逐条一致；
  ② 多边形/掩码几何在容差内（顶点集合与面积）；
  ③ 属性键值**逐条一致**，或**显式产出"丢失清单"**（禁止静默丢）；
  ④ 坐标一律**原图 px**、`w_px/h_px` 恒 **2880×1616**（票 21 §1.5 红线，禁 `ds=2` 工作坐标）。
```

**未做**（本票只装服务，没有装客户端、没有帧）⇒ **§6.2 是"待执行"而不是"已验证"**，别当结论用。

### 6.3 与票 21（现有复核页）的关系

票 21 的 `frames.csv`/`instances.csv` 是 **bbox 通道**（已可用、离线、单文件 HTML）；本票是**掩码/多边形 + 全属性**通道。
二者**不互相取代**：`measurement_valid` ↔ 票 21 `height_valid`、`bag_group_id` ↔ `cluster_id`（票 `01` §4.1 已给映射）。
⇒ 如果最终走 X-AnyLabeling，票 21 的复核页仍可作为"框级快速复核"通道；**是否需要保留两条通道由 Chief 定**。

---

## 7. 两条落地路径的成本清单（Chief 2026-10-14 15:39 要求"收敛成能直接选的两条"）

**跟两条路都有关的三条实测前提**（证据原文见 §4）：

| 事实 | 实测 |
| --- | --- |
| `:9213` **是纯 REST API，不是 Web 标注界面** | `/openapi.json` 11 条路由全是接口；界面是 **PyQt6 桌面程序**（上游 `docs/en/cli.md` §1 + `xanylabeling checks` 输出）⇒ **"浏览器标注"必须换工具** |
| 本机 WSL2 的 9213 **局域网不可达** | `netsh portproxy` 空；防火墙三档全开、9213 规则 0 条；`.wslconfig` 2026-09-30 已由 mirrored **改回 NAT** |
| **mirrored 不能改回** | 注释写明：mirrored 下 Docker 发布端口在 Windows 侧连不上，而仓库开发/测试**要求从 Windows 连 `127.0.0.1:5432`**（AGENTS.md）⇒ 改回去会打断开发链路 |

### 7.1 (a) 桌面客户端路线（Chief 倾向 · **我同意**）

| 项 | 内容 |
| --- | --- |
| 工具 | **X-AnyLabeling 桌面客户端**（PyQt6，上游 v4.0.0 线） |
| 分发方式 | ① **官方发布包**（Windows 解压即用）——**发布物我尚未逐个核实**，落地第一步先核 release 资产与校验值；② pip/venv 安装（需 Python ≥3.10；本环境 3.12 已验证）；③ 容器化分发——GUI 需 X 转发，**对 Windows 标注机不实用，不推荐** |
| 客户端要求 | Python 3.10+ / PyQt6；**CPU 够用**（人工画掩码不需要 GPU）；GPU 只影响自动/预标注速度；Qt 图像内存上限可调（`--qt-image-allocation-limit`） |
| 离线可用性 | 桌面程序本身离线可用；**必须关掉启动自动更新检查**（`--no-auto-update-check`）；**不装内置模型 ⇒ 人工标注零联网需求** |
| 连不连本机 `127.0.0.1:9213` | **本期等价**（现在 `models_loaded=0`，连了也没模型）；P2 有自训模型后才产生差别（Remote-Server 预标注） |
| 服务端资源 | **0**（完全不占生产机） |
| 协作成本 | 各人本地文件 ⇒ **要约定目录与汇总方式**（合并不便）；双人独立标注（Plan §5.5）天然支持 |
| 备份 | 标注 JSON 在标注人机器上 ⇒ **必须定备份点**（否则手工拷贝就是唯一副本） |
| 需要管理员权限 | **不需要** |

**一个人从零到第一张掩码（可照做）**：
① 装客户端（官方包或 pip）；② 从生产**人工搬运**帧到 `D:\xany-data\images\<日期>\`（**不做网络直连**）；③ 打开客户端 → **Open Dir** 选该目录；④ 选 **Polygon**（或 Brush）画菌包/菌簇轮廓、双击闭合；⑤ 标签用票 01 §4.1 的 `bag`/`cluster`/`cap`，属性用 flags（`occluded`/`truncated`/`association_valid`/`measurement_valid`/`rejection_reason`/`review_status`）——**属性怎么落进 XLABEL JSON 要实测一次**；⑥ Ctrl+S 存 JSON；⑦ 导出 `xanylabeling convert --task xlabel2coco` / `xlabel2mask`；⑧ 做 §6.2 的四条回读判据。

**落点已更新（2026-10-14 15:5x 用户/Chief 裁定）**：**路径 (a)，但客户端装在蘑菇 prod（10.77.77.39）、由用户自己走 RDP 使用**（不是装在标注人机器上）。据此对上面这行成本表的差异：

| 差异项 | 最终口径 |
| --- | --- |
| 分发 | **官方 release v4.1.1 的 `X-AnyLabeling-v4.1.1-Linux-CPU-Portable.tar.gz`**（**445,026,914 B**，sha256 `45354a08a2929eed0f1a581aba8809996b04f26308691dbd39206897fdeecbe9`，上游附 `.sha256`）；**CPU 版**（prod 无 NVIDIA GPU） |
| 外网 | **prod 可直连**（`github.com`/`pypi.org`/`mirrors.aliyun.com` 全 200）⇒ 直接 `curl -LO` + `sha256sum -c`，**不需人工搬包** |
| 安装路径 | **`/data/annot/xanylabeling-4.1.1/`**（`/data` 剩 1.5 T；`/` 只剩 49 G，不装 `/`）；数据 `/data/annot/{images,labels,exports}` |
| 限流 | `nice -n 19` + `taskset -c 2,3`（4 核给 2 核），必要时 `systemd-run --scope -p CPUQuota=150%` |
| systemd | **不写服务、不开机自启**（Chief 明令）；只在 RDP 会话里前台跑 |
| RDP 注意 | 断连默认保会话 ⇒ **退出 RDP 前自己关客户端**；建议跑在专用非 root 账号 `annot` |
| 避开巡检轮 | 实测节拍 **每 3 小时、常在 `:27`**（`15:27:44/12:27:45/09:27:47/06:27:45/03:27:57`）⇒ 避开 **`:18–:35`**（**不是** Chief 信里说的 7:40） |
| 回传/备份 | `rpull.ps1` 拉回本机；`labels` 每日 tar 到 `/data/annot/backup/` 保留 30 天 |
| 卸载 | `rm -rf /data/annot/xanylabeling-4.1.1`（自包含、无服务）；停进程 `pkill -f xanylabeling` |
| 验收 | 按 Chief 第 4 条：RDP 启动截图+版本号 → 一袋一簇掩码+袋顶折线+一个帽掩码能画能存 → 导出 COCO 回读校验 → 资源实测 + 下一轮 `n_frames_recorded`/失败数对比 → 路径清单 |

**我同意 (a) 的理由**：零服务端资源、不动生产机（4 核已在跑 49 个容器）、不需要管理员权限、不改系统网络配置；P0/P1 的人工标注量本来就够用；9213 的价值在 P2，不必此刻跨机。

### 7.2 (b) Web 集中标注路线（要浏览器多人协作才走）

**换哪个工具，我明确推荐：Label Studio（社区版，Apache-2.0）。** 上游事实（已核）：单容器 `heartexlabs/label-studio:latest`、端口 **8080**、数据卷 `/label-studio/data`（默认 SQLite）、**容器非 root（挂载目录属主要归 UID 1001）**、官方建议 **≥8 GB RAM（推荐 16 GB）**/50 GB 磁盘/Chrome、**官方有成文的断网安装步骤**（`docker save | gzip` → `scp` → `docker load`）、多用户+登录+项目、可接 ML 后端做预标注。

| 对比项 | **Label Studio（推荐）** | **CVAT**（Plan 原选 / 备选） |
| --- | --- | --- |
| 容器清单 | **1 个**（官方 compose 版另加 Nginx + PostgreSQL ⇒ 3 个） | **10+ 个**：`cvat_server`+`cvat_ui`+8 个 worker + `postgres` + `redis`×2（含 kvrocks）+ `traefik` + `opa` + `clickhouse` + `vector` + `grafana`（我按上游 compose 逐条核过） |
| 端口 | 8080 | 8080（+8090 看板） |
| 官方资源门槛 | **≥8 GB RAM / 16 GB 推荐**、50 GB 磁盘 | 官方安装文档**无资源门槛章节，也无离线章节** |
| 断网可用 | **有官方成文步骤** ✅ | 要自己 `docker save/load`（无官方离线章节） |
| 我给的资源上限 | `cpus=1.5`、`mem_limit=4g`、数据卷挂 **`/data/annot`**、**端口只发布到内网/VPN 地址** | 至少 `cpus=2`、`mem_limit=8g`（12 个 python worker，4 核会抢 CPU） |
| 红线 | 无 docker.sock | **`traefik` 挂 `/var/run/docker.sock:ro`** ⇒ 选它就得先批这条例外并写死范围 |

**生产机资源实测（2026-10-14 15:40 只读）**：**4 核**、load ~1.7、**62 GiB 内存（17 用 / 45 可用）**、swap 未用；`/` 98 G（**用 45 / 剩 49 G**）、**`/data` 1.8 T（剩 1.5 T）**；**49 容器 / 7 个 compose 项目**；镜像里**没有任何标注类镜像**（无 cvat/label-studio/nuclio/clickhouse/grafana/traefik/kvrocks）。
**出网**：**直连 Docker Hub 不通（000）**；prod `daemon.json` 已配镜像站（`docker.1ms.run`/`proxy.1panel.live`/`docker.ketches.cn`）且 `docker.1ms.run` 可达（401=需 token，正常）⇒ **走镜像站可拉**（拉取是写动作，**批准后先拉 `label-studio` 一个验证**）。
**结论**：内存足够；**瓶颈是 4 核 CPU 和 `/` 只剩 49 G** ⇒ 选 Label Studio（单容器 + 限额）比 CVAT 稳。

**访问控制 / TLS**：先**内网 + 防火墙白名单**，不公网暴露；要跨 VPN 再自签或复用现有反代（生产已有 caddy/tools_nginx）。
**需要管理员权限的动作**：工具跑**生产机** ⇒ 端口发布/防火墙策略可能要用（**要先确认谁有权限**）；跑**另一台内网机** ⇒ 无。
**回滚（一条命令）**：独立 compose 项目 `annot`（**与 `mushroom_service` 完全分离**）⇒ `docker compose -f annot.yml -p annot down`，数据卷保留，**不 touch 现有 7 个项目**。
**凭据**：Label Studio 管理员口令走 `request_secret`，**不进聊天/仓库**。
**标注数据放哪（Chief 问的）**：(a) 天然在标注人机器（要定备份）；(b) 若跑生产机 ⇒ 数据卷放 **`/data/annot`（1.5 T）**，**绝不放只剩 49 G 的 `/`**。**我仍不建议标注数据长期放生产机**——它是人工资产、生产机是巡检运行环境（49 容器），混放让备份与权限纠缠。折中：工具跑生产机（可达性最好）+ 数据挂 `/data/annot` + **单独备份**。

### 7.3 9213 那套：**保持现状别拆**

服务继续跑在内部 WSL `0.0.0.0:9213`，不改配置、不拆；票面写明**现阶段用途 = 预留（P2 服务端预标注）**；核心装（71 包）已冻结在 `.venv` + `requirements-frozen-*.txt`。当前 `models_loaded=0`、无权重 ⇒ **对两条路都不构成依赖**。

---

## 8. 两条路各自的验收证据要求

**共同**：能打开 → 能画 → 能存 → **能导出且回读一致**（§6.2 四条判据）。

| # | (a) 桌面路线 | (b) Web 路线 |
| --- | --- | --- |
| 1 | 客户端启动截图（版本号可见） | `curl -I http://<host>:<port>/` → **200** + 页面标题截图 |
| 2 | 一帧 2880×1616 真图：画出 **1 袋 + 1 簇**掩码并保存 | 浏览器里用同一帧画出同样对象并保存 |
| 3 | XLABEL JSON 落盘 + 字段清单（含属性） | 平台导出的 JSON/COCO/掩码 + 字段清单 |
| 4 | `xlabel2coco`/`xlabel2mask` 导出 → **回读四条判据** | 导出（COCO/mask）→ **回读四条判据** |
| 5 | **断网重跑** 2–4 步（Plan §5.1 硬要求） | 断网/镜像站断供下容器仍能起、能标、能导出 |
| 6 | 客户端与数据目录路径、备份点写进票 | `docker ps` healthy + `docker stats --no-stream` **资源占用实测** |
| 7 | — | **部署前后 `patrol`/`analysis`/`console`/`preview` 状态对比**（tag/uptime/healthz 逐条），**不许连带重启** |

---

## Comments

- 2026-10-14 发版官写票 + 实施。依据用户当日指令（改用 X-AnyLabeling、装 server、端口 9213）与 Plan §5.1。
- 同日的生产侧挂起（`--measure` 待 P6、现场作业不排期、容器不动）记在 `docs/patrol/prod-deploy/issues/09-measure-enable-release-checklist.md` §5。
- 2026-10-14 15:3x 追加：§7 拆成"已裁定 / 待决"；新增 **§8**（`:9213` 不是 Web 标注界面、局域网不可达、`.wslconfig` 不能再改 mirrored 的实测）。依据 Chief 当日回复。
- 2026-10-14 15:39 **收敛改版**：按 Chief 要求，§7 重写为**两条落地路径的成本清单**（(a) 桌面客户端 / (b) Web 集中标注，明确推荐 **Label Studio**、对比 CVAT），§8 重写为**两条路各自的验收证据要求**；原 §7/§8 的裁定与实测结论全部并入 §7 开头的前提表与各路径行内，未丢事实。**CVAT 取证与 9213 重复诊断按 Chief 指令停止**；**9213 保持现状不拆**（§7.3，现阶段用途=预留）。

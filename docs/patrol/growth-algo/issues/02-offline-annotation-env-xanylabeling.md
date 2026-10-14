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

## 7. 裁定与待决

### 7.1 已裁定（Chief 2026-10-14 15:3x）

1. **`[ultralytics]` 不装** —— 同意我列的理由（45 包 / 18 个 CUDA 轮子 / `torch 528.9 MiB` / `triton 236.5 MiB`，且 P2 才有权重可服务）⇒ **维持"核心框架"形态**（当前已装形态就是最终形态，直到 P2）。
2. **接受纯人工标注**：SAM2/SAM3 是**加速手段、不是判据**；P0/P1 的量（抽检 ≥12 帧 / ≥40 袋 / ≥20 帽 + 训练集）人工做得完。**GPU 机器要用户批硬件 ⇒ 记为条件项，不默认"必须有"**（§7.2-5 保留为条件项，不再当缺口）。
3. **不在生产机装 GUI**；标注入口的可行性核实见 **§8**。
4. **断网缺口可接受，但票面写清**（§4 的 `/docs` CDN 那条保留为已知缺口）。
5. **权重是 P2 前提**（预置权重 + `model_path` 绝对路径 + `HF_HUB_OFFLINE=1`）；**本期人工标注不需要**。
6. **括号帧收尾腿** ⇒ 记 **"未取得"、今日不追**；下一轮收尾后的空闲窗口若还要那份漂移对照，再做一组三腿（可选项，Chief 不再催）。原始字节见 prod-deploy 票 09 §5.2。

### 7.2 待决

1. **每台标注机各自跑一份 vs 一台共享服务**（依据 §8 结论③）—— 共享要先解决 `portproxy` + 防火墙（**需管理员**）；各自跑则**零网络依赖**。**我建议各自跑**，跨机留到 P2 前再办。
2. **要不要容器化**：上游没有 Dockerfile；收益 = 离线打包一次带走 + 环境不漂移，代价 = 自写 Dockerfile（我的领地）。当前 venv 已能跑，建议等 P2 有模型时一起做（免得镜像里背没用的 CUDA）。
3. **标注数据备份点**（§5 最后一行）——**人工标注不可再生**，必须定。
4. **`/docs` 的 CDN 依赖**要不要本地化（只影响交互式文档页）。

---

## 8. Chief 要求核实的那条：**"两人各自浏览器开 `:9213`"到底行不行**

**两条结论，都是实测/上游文档级，不是推断。**

### 8.1 `:9213` **没有标注界面** —— 它是纯 REST API ❌

- `/openapi.json` 实测 **11 条路由**，全是 API：`/health`、`/v1/models`、`/v1/models/{id}/info`、`/v1/predict`、`/v1/video/*`。浏览器打开根路径**没有标注 UI**；唯一的网页是 `/docs`（Swagger，且**断网白屏**，§4）。
- 上游的标注界面是 **PyQt6 桌面程序**：`docs/en/cli.md` §1 的 "GUI Launch Options" 全是桌面用法（`xanylabeling --filename …`），`xanylabeling checks` 输出里 `PyQt6 Version: 6.9.1`。
- ⇒ **"浏览器开 `:9213` 标注"这条不成立。** 两条替代：**(a)** 桌面客户端装在**标注人自己的机器**上（上游原生路径，本期人工标注够用）；**(b)** 若要**浏览器里标注**，得**换工具**（CVAT 是 Web 形态；**X-AnyLabeling 不提供 Web 标注界面**）。

### 8.2 局域网另一台机器**现在访问不了** `:9213` ❌（要跨机得动系统网络）

| 检查项 | 实测结果 | 含义 |
| --- | --- | --- |
| Windows 网卡 | `10.77.77.47/24`（本地连接 2）、`192.168.0.36/24`、`10.112.124.92/17`（WLAN） | 本机在 **10.77.77.0/24**（与生产 10.77.77.39 同网段） |
| `netsh interface portproxy show all` | **空** | 没有端口转发 ⇒ WSL2 NAT 下，**只有这台 Windows 能**通过 `127.0.0.1:9213` 访问（已实测 200） |
| `%USERPROFILE%\.wslconfig` | `[wsl2] localhostForwarding=true`；注释：**2026-09-30 由 mirrored 改回 NAT**，原因 = mirrored 下 Docker 发布的端口（postgres `127.0.0.1:5432`）在 WSL 内与 Windows 侧都连不上，而仓库开发/测试**要求从 Windows 侧连 `127.0.0.1:5432`**（AGENTS.md）；备份 `.wslconfig.bak-20260930` | **不能再改回 mirrored** —— 会打断开发链路（这是一条已知回归） |
| Windows 防火墙 | Domain / Private / Public **三个 profile 全开**；`9213` 相关规则 **0 条** | 即使加了转发，入站也会被挡（加规则**需管理员**） |

⇒ 跨机两条路，**都不轻**：
- **(a)** `netsh interface portproxy add v4tov4 listenaddress=10.77.77.47 listenport=9213 connectaddress=<WSL IP> connectport=9213` + 入站防火墙放行 —— **需管理员**，且 **WSL IP 每次重启会变**（现 `172.22.117.116`）⇒ 还要一段"WSL 起来后重设 connectaddress"的脚本；
- **(b)** mirrored 网络 —— **已被 2026-09-30 的决定排除**（见上表）。
- **(c) 我更推荐的第三条**：**每台标注机本机跑一份**（venv 可复制，或用 §7.2-2 的容器化分发），桌面客户端连本机 `127.0.0.1:9213` ⇒ **零网络依赖、不动 `.wslconfig`、不需要管理员**。

### 8.3 一个让这条不那么紧急的事实

**本期纯人工标注根本不需要 `:9213`**：桌面客户端本地就能画多边形/掩码/属性（Plan §5.2 的对象与属性），`9213` 的价值在 **P2 自训模型出来之后**做服务端预标注。
⇒ **跨机打通可以推到 P2 之前再办**，先记成 P2 前置项；本期不要为它改系统的网络配置。

---

## Comments

- 2026-10-14 发版官写票 + 实施。依据用户当日指令（改用 X-AnyLabeling、装 server、端口 9213）与 Plan §5.1。
- 同日的生产侧挂起（`--measure` 待 P6、现场作业不排期、容器不动）记在 `docs/patrol/prod-deploy/issues/09-measure-enable-release-checklist.md` §5。
- 2026-10-14 15:3x 追加：§7 拆成"已裁定 / 待决"；新增 **§8**（`:9213` 不是 Web 标注界面、局域网不可达、`.wslconfig` 不能再改 mirrored 的实测）。依据 Chief 当日回复。

# 生产部署信息补全清单（目标机 `ssh root@10.77.77.39`）

状态：**A/B 类已实测；采图链路已修复并验证（ADR-0009）；M2 已决策暂缓；巡检档已整定并现场验收（Y 50→150）；
补光灯与 IN0–IN3 已实测到"可判定边界"；**M1 已装配并上机跑通整轮 60 站位**（ADR-0011，逐站取证见 §10「整轮实证」）；
C 类（机械标定）待现场示教**
日期：2026-09-13（M1 装配收尾更新）
一期范围：**M0 手动调试台 → M1 主机调度巡检**（连接 → `para` 读回 → `home` 回零 → `jog` 点动 →
单站采图 → 两段速 `goto` → 整轮 60 站位巡检 + 图像索引落库）
配套交付：`.scratch/prod-deploy/motor-command-review.md`（电机控制指令逐条复核 + IO 实测）、
`docs/adr/0009-capture-service-virtual-display-hardening.md`（采图服务加固）、
`docs/adr/0007`（运动参数单源，含本轮巡检档整定与段序修复）、
`docs/adr/0011-m1-assembly-and-deploy-boundary.md`（M1 装配、deploy 包边界、图像索引落地）

本文回答一个问题：**现在的代码里，哪些是"我根据你的口头输入 + 说明书推出来的"，
而不是"从机器上读回来的"**。凡属后者，上机前都必须补齐——因为这一类错误的共同点是
**下发返回成功、机构行为不对**，没有任何错误码可以让上层看见。

---

## 0. 连接方式（阻塞已解除，留档）

- **沙箱是拦路的那一层**：Bash 工具默认沙箱对私网一律拦截（本机地址 `EACCES`，其他私网静默丢包），
  公网正常。必须用 `dangerouslyDisableSandbox: true` 才能出网到 `10.77.77.0/24`。
- **plink 首次连接会挂在主机密钥确认上**。免交互的正确姿势：先用 OpenSSH 把密钥写进
  `~/.ssh/known_hosts`（`ssh -o StrictHostKeyChecking=accept-new` 一次即可，哪怕认证失败也会写入），
  再用 `ssh-keygen -lF <host>` 取出 `SHA256:...` 指纹，把它喂给 plink：

  ```bash
  plink -ssh -batch -hostkey "SHA256:<指纹>" -pw '<密码>' root@10.77.77.39 "<命令>"
  ```

- 本仓库已封装两个包装（密码只走环境变量，不落盘）：
  `PROD_PW=… bash .scratch/prod-deploy/rexec.sh "<命令>"`（执行）
  `PROD_PW=… bash .scratch/prod-deploy/rpush.sh <本地> <远端>`（上传）

---

## 1. 第 4 轮（2026-09-12 晚）新增

| 项 | 结论 | 对代码的影响 |
| --- | --- | --- |
| **M2 决策** | **暂缓**（厂商 `.elo` 是二进制、无格式文档；M1 主机驱动已能独立跑全流程） | `elo/generator.py` **本轮不动**；D7 / G4 挂起，**不阻塞主干** |
| **回零速度** | 现场原给 Y 150/1500、Z 50/500——**回零比巡检还快 2.5–3 倍**，与"回零应更慢"的常规整定相反；已授权下调 | 改为 **Y 90/900、Z 20/200**（见 §4.1）。注意 Y 有**下界**，不能更慢 |
| **采图 500** | ✅ **已修复并验证**：容器入口改为 **fail-closed** + 真连接就绪判定 + 运行期 watchdog，healthcheck 独立并覆盖显示器 | 与 patrol 代码无关；见 `docs/adr/0009`。旧系统同一根因，同步受益 |
| **238 可用性** | ✅ `curl /dynamic_capture?ip=192.168.1.238` → **200**，`snap_result=0`，落盘 882–891 KB，响应里 `"display":":99"` | **S5 的前置条件解除** |

### 第 3 轮确认（保留）

| 项 | 结论 | 对代码的影响 |
| --- | --- | --- |
| **限位开关** | **Y、Z 两轴都装了独立限位开关**（现场确认） | `home_dir`（Y=2 负限位 / Z=1 正限位）**有硬件依据**；控制器 `homeTime` 是有效保护；B4 关闭 |
| **相机密码** | **无密码**（`user=admin`，`pwd` 留空）| `capture_client` 不应要求凭据；D1 关闭 |
| **相机 238 归属** | **保留给当前系统，与老系统共用同一台** | 新系统采图**必须走同一个 `:7003` 服务**（单 worker 串行 → 天然互斥），并**避开旧系统每小时 `:01:30` 的窗口** |
| **采图 500 根因** | **不是调用方式，是显示环境**：容器 `DISPLAY` 为空、内部无 X 服务 → 服务自报 `headless_mode:true`、"无头模式截图失败" | 与代码无关，属现场服务部署问题（见 §4.2） |
| **`.elo` 脚本格式** | **厂商脚本是二进制 + GBK 分号 CSV 两级命名；我们的生成器两种都不是** | 🔴 **M2 路径当前不可能下发成功**，需决策（见 `motor-command-review.md` §三） |
| **`Set_Output` 极性** | 说明书 §三.3 + SDK详解 p4：**`status=1` → 输出低电平 → 回路导通 → 负载得电** | `lamp(True)=1` 语义正确，但建议上机实测一次（D4） |

---

## 2. 前两轮已确认（7 项）

| 项 | 结论 | 对代码的影响 |
| --- | --- | --- |
| 控制器可达性 | 目标机经 `192.168.1.239:8088` 可达 | `CONTROLLER_IP / CONTROLLER_PORT` **一致，不必改** |
| 轴接线 | **Y = 轴 1、Z = 轴 2、轴 0 空置** | `axis_mask = 0x06`、`.elo` 等待槽位 `(1,2,1)`、`layer_z / col_y` **全部成立** |
| 相机布置 | 滑块上 1 台，全部站位共用 | `camera_ip` 应退化为**单一全局配置**；原型里「每框一个 IP」要改（J4） |
| **部署拓扑** | **同一台机器**：`10.77.77.39` 就是目标机，它同时有 `eno1 = 192.168.1.250/24` 直连控制网 | 控制器/相机**不跨网段**，无需第二网卡或静态路由；`analysis` 与 `patrol` 同机 → `sync` 可退化为本机 |
| **Y 轴软限位** | **已整定为 `[0, 4495]`**（不是出厂 ±200） | ADR-0008 的"投产前置条件"**已在现场解决**，长行程截断风险消除 |
| **软限位符号假设** | **被实测证实**（见 §3 B7） | `DevicePara.effective_limits` 的 docstring 已从"待证伪"改为"已证实" |
| 一期范围 | M0 手动调试台 | 只需补 1 层胶水；**不依赖** console 后端与 analysis |

---

## 3. A 类 · 目标机环境盘点 —— ✅ 已完成

| # | 信息项 | **实测结果** |
| --- | --- | --- |
| A1 | OS / 架构 | **Ubuntu 24.04.4 LTS，kernel 6.8.0-139-generic，x86_64（amd64）** ✅ 与厂商 `.so` 匹配 |
| A2 | Python | **3.12.3** ✅ 满足 `requires-python >= 3.11` |
| A3 | 网络形态 | **双网卡**：`eno1 = 192.168.1.250/24`（控制网，直连）、`tun0 = 10.77.77.39/24`（VPN）。`ip route get 192.168.1.239` → `dev eno1` ✅ **不跨网段** |
| A4 | 厂商 SDK | ❌ **原机没有 `libFMC4030_2009_1.so`**，只有一个未解压的 `/home/sysadmin/algorithm/fmc4030/FMC4030.rar`（144 MB，机上无 unrar/7z/bsdtar）。→ **已从本地 `D:\Documents\蘑菇项目\国内\linux版本二次开发库\ubuntu\` 上传至 `/opt/mushroom-patrol/lib/`**，`ldd` 干净（仅 libc/libstdc++/libm/libgcc_s），`CDLL` 加载成功 |
| A5 | X11 / **xvfb** | **宿主 `/usr/bin/Xvfb`、`/usr/bin/xvfb-run` 均在位，`/tmp/.X11-unix/` 有 `X0`、`X10`** ✅；但**采图容器内没有 DISPLAY、没有跑 X 服务** ← 这就是 500 的原因 |
| A6 | capture 服务 | ✅ **在跑**：容器 `xcloudsdk_py_offline_20260120_175307-xcloud-capture-1`（镜像 `xcloudsdk-py:0.1.0`）→ `0.0.0.0:7003`。`/`、`/healthz`、`/readyz` **实测均 200** |
| A7 | 时钟 | ✅ `NTP service: active`，已同步，`Asia/Shanghai (+0800)` |
| A8 | 资源 | ✅ `/` 98G 用 44G 剩 50G；内存 62G 可用 47G；swap 未用 |
| A9 | 权限/常驻方式 | ✅ root + systemd + docker 齐全。宿主上跑大量容器（dify 全家桶、mlflow、pgvector、minio、taos、kafka、caddy…），端口 7000–7011 / 8080 / 8081 已占用 |

### 端口实测（从目标机发出）

| 目标 | 结果 |
| --- | --- |
| `192.168.1.239:8088`（控制器） | **OPEN** ✅ |
| `192.168.1.238:80`（相机 HTTP） | **OPEN**（返回相机 Web UI 页面） |
| `192.168.1.238:554`（相机 RTSP） | **OPEN** |
| `127.0.0.1:7003`（采集服务） | **OPEN** ✅ |
| `127.0.0.1:8000`（原以为是 analysis API） | **CLOSED** ❌ 见 §6 G1 |

---

## 4. B 类 · 控制器整定参数 —— ✅ 已读回

`Get_Device_Para` 实测（2026-09-12，全程只读，未发任何运动指令）：

```
192.168.1.239:8088  device_id=1  sizeof(machine_device_para)=92 ✅ 与厂商头文件一致
  div          = (5000,   100000, 100000)
  lead         = (10,     95,     95)      mm/转
  softLimitMax = (200,    4495,   -1)
  softLimitMin = (200,    0,      220)
  homeTime     = (10000,  100000, 100000)  ms
  bound232 = bound485 = 115200
```

| # | 信息项 | **实测结果** | 结论 |
| --- | --- | --- | --- |
| B1 ★ | 软限位 | 轴0 `[-200, 200]`、**轴1(Y) `[0, 4495]`**、**轴2(Z) 已取消**（原始 `softLimitMax = -1`） | Y ✅ 已整定，与行程 4492 一致；**Z 无控制器侧保护** |
| B2 ★ | 细分 / 导程 | 轴1、轴2 均为 `div=100000, lead=95` → **1052.632 脉冲/mm**；轴0 `5000/10` → 500 | mm→脉冲换算有据；由控制器内部换算，我方按 mm 下发即可 |
| B3 | `homeTime` | 轴1、轴2 各 **100000 ms（100 s）**；轴0 10000 ms | 主机侧 `home_timeout=120s > 100s` ✅ 两层超时关系正确 |
| B4 | 限位开关是否安装 | ✅ **已装**（Y、Z 各有独立限位开关，现场确认） | 关机打开：`home_dir` 有硬件依据；`homeTime` 保护有效 |
| B5 | `Line_2Axis` 虚拟坐标语义 | ✅ **文档已确认**（SDK详解 p5 / 指令表 p6："此 X 为虚拟坐标系，与选择启动的轴无关"）；参数顺序 `endX=Y, endY=Z` | 待 S4 实机复验一次 |
| B6 | 回零方向 | ✅ **已实机复验（2026-09-13）**：Y `homeDir=2` 确实向**负限位**走（回零中触发 `LIMIT_N`），Z `homeDir=1` 向**正限位**，两轴落点均**精确 0.000**。用户定稿约定＝"Z 向上回零后为 0 点、Y 反向回零后为原点"，与 `motion_profile` 完全一致。逐帧证据见 ADR-0007 §四 | 关闭 |
| B7 ★ | **软限位符号约定** | `softLimitMin` 存**幅值**（`[-min, +max]`），任一字段为负 = 取消 | ✅ **ADR-0008 的假设被实测证实** |

### ⚠️ 状态位命名陷阱（已修）

厂商英文注释极易误读——`FMC4030.h` 原文：

```c
#define MACHINE_LIMIT_N_NONE  0x0200   //负限位未触发     ← 不是"没有限位开关"
#define MACHINE_LIMIT_P_NONE  0x0400   //正限位未触发
#define MACHINE_HOME_NONE     0x0800   //未回零
```

我一度**读错**（当成"无负限位开关"），会让调试台打出误导现场操作员的字样。
已修 `patrol/fmc/status.py`：把厂商原文注释逐条抄进常量定义、在 `AxisStatus`
docstring 里点名这个陷阱、并让 `homed` 同时校验 `home_done / homing / home_none` 三位。

实测自洽：`轴1/轴2 axisStatus = 0x0648` = `停止+回零完成+负限位未触发+正限位未触发`；
`轴0 = 0x0e08` 额外带 `未回零`（未接线轴，符合预期）；`homeStatus` 全 0（厂商注明"未使用"）。

### ⚠️ 安全项：Z 轴此刻没有控制器侧过行程保护

- Z 的控制器软限位**已取消**，且行程 `-212…0` 的**上端就是 0（原点）**——如果软限位其实被
  按 `[-220, -1]` 解读（而非"取消"），那么命令 Z 走到 `0` 会被**静默截断到 -1mm**。
  两种解读只差一次实机验证，但都会让"回零后第一帧"位置不对。
- 我们自己的 `check_travel` 会兜底 `-212…0`，但拦不住"控制器接受目标却少走 1mm"。
- **硬件侧已有独立限位开关兜底**（本轮确认），所以不会撞坏机构；但**点位精度**仍受影响。
- **建议**：首次上机时把 Z 先下到一个明显小于 0 的目标（如 `-50`）验证方向与到位；
  确认后再动 `0`。

---

### 4.1 本轮调整：回零速度（**我们自己的**整定值，不是控制器参数）

现场原给 **Y 150/1500、Z 50/500**，**回零比巡检（Y 50/20、Z 20）还快 2.5–3 倍** ——
与"回零是找硬限位、应更慢"的常规整定相反。经授权下调为：

| 轴 | 原 | 新 | 最坏寻零 | 约束 |
| --- | --- | --- | --- | --- |
| Y | 150 / 1500 | **90 / 900** | 4492/90 ≈ **50 s** | 控制器 `homeTime = 100 s` → **2.0 倍余量** |
| Z | 50 / 500 | **20 / 200** | 212/20 ≈ **11 s** | 无超时压力（100 s），取巡检整定值 |

★ **Y 的回零速度有下界，不是越慢越好**：回零是"从行程内任意位置出发、向固定的限位开关找开关"，
最坏起点在行程另一端，所以按**全行程**估。一旦超过控制器 `homeTime`(100 s)，控制器会中止回零
并置位 `MACHINE_HOME_OVERTIME` —— 后果是**原点不可信**，不是"慢一点"。
故 `home_speed ≥ travel_span / (余量系数 × homeTime)`。

想把 Y 再降到 50 mm/s 就必须改控制器 `homeTime`，但那会让"限位开关坏了"时的**硬顶时间从
100 s 变成 200 s**——用机构损伤换速度，**不建议**。

该约束已落成回归测试 `test_home_speed_keeps_margin_under_controller_home_timeout`：
以后有人把速度调慢会**直接测试失败**，而不是等到现场回零随机超时才发现。

### 4.2 本轮整定：**巡检速度**（也是我们自己的值）

原 **Y 50 / Z 20**。2026-09-12 晚确认这组值"**没定过**"（双轴改造时按假想机器推的），
授权重新整定：

| 轴 | 原 | 新 | 依据 | 脉冲率 |
| --- | --- | --- | --- | --- |
| Y | 50 / 500 | **150 / 1500** | 控制器硬上界 190 mm/s（200 kHz ÷ 1052.632 脉冲·mm⁻¹）；且**改造前现场值就是 150/1500**（现场给的 Y 回零档同值） | 158 kHz = 上界 **79%** |
| Z | 20 / 200 | **不变** | 一轮只有 4 次换层动作、合计 191 mm，提速收益 < 5 s/轮，不值得动竖直轴 | 21 kHz = 11% |

★ **提速必须同时钉住点动档**：`jog_ratio` 默认 0.2 会把 Y 点动从 10 推到 30 mm/s
（示教时肉眼跟不上），故显式取 `10/150` ⇒ 点动仍是 **10/100**（改造前现场值）。
守护测试：`test_derived_ladder_matches_the_pre_migration_tuning`。

★ 比提速更值钱的是**段序 bug**：`goto`/`elo` 把**空程**喂了接近档、把**最后 5 mm** 喂了巡检档。
只修这一处（速度不动）整轮就从 **36.8 min → 9.5 min**；再叠加 Y150 → **4.6 min**。
模型脚本 `.scratch/prod-deploy/speed-tune.py`（可重跑），推导见 ADR-0007 补充。

⚠️ **唯一残留风险**：驱动器（FMDD50D40NOM）最大输入频率无手册。上机验证三步见
`motor-command-review.md` §2.2.1，脚本 `.scratch/prod-deploy/travel-speed-check.py`。

---

### 4.5 第一次真机运动挖出的两个缺陷（2026-09-13，已修并回归）

这两条**只有真的让轴动起来才会暴露**：此前所有"成功"的 SDK 调用只用 `int` 与指针，
单元测试又全部喂假库，所以一路全绿、文档也以为"指令复核完成"。

| # | 缺陷 | 症状 / 风险 | 处置 |
| --- | --- | --- | --- |
| F9 | `load_library()` 是裸 `CDLL()`，**从未声明 `argtypes`**。C 侧运动参数是 `float`，无声明时 Python `float` 被当 `double` 传 | 第一次点动即抛 `ArgumentError: argument 3`；**更坏的是不报错的情形**——参数错位读成"另一个数"，函数照样返回成功 | 新增 `patrol/fmc/sdk.py`（24 条签名，逐条抄 `FMC4030.h`）；`test_fmc_sdk.py` 守护"新增调用必须登记签名" |
| F10 | 指令下发后约 **30 ms 的「起转窗口」**里，状态字全是上一条指令的残值 | `wait_stop` 立刻返回"到位"（0.00 s）⇒ 下一条同轴指令被 SDK **静默丢弃**；`wait_home` 读到**上一轮的** `home_done`（它表示"坐标系已建立"，普通点动不作废）⇒ 误判"已回原点"，后续绝对坐标**整体偏移** | 两处等待改成两段式握手（先确认起转，再等结果）；假库补上回零过渡建模 |

顺带确认 `−10` 的实测语义：**对仍在运动的轴下发回零 = 轴忙拒绝**（厂商表只到 −8）。

完整推导与逐帧证据：ADR-0010。

---

## 5. 现场资产（含本轮新发现）

### 5.1 采图服务（`Mushroom_CLI` @ `:7003`）

```
GET /             -> {"service":"Mushroom_CLI","endpoints":["/dynamic_capture","/fast_capture","/pool_capture","/capture"]}
GET /healthz      -> 200 {"ok":true,"init_done":true,"ready":true}
GET /readyz       -> 200 同上
GET /docs         -> Swagger UI      GET /openapi.json -> FastAPI / openapi 3.1.0
```

**OpenAPI 参数口径（实测，四个端点完全一致）**：

| 参数 | 类型 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ip` | string | `''` | 相机 IP |
| `user` | string | `'admin'` | 固定 admin |
| `pwd` | string | `''` | **空**（相机无密码，已确认） |
| `storage` | string | `'local'` | `local` 存服务端 / `cloud` 直传 MinIO |
| `filename` | string | `''` | 支持**子目录前缀**，如 `611/20260912/611_1921681238_xxx`；服务自动补 `.jpg` |
| `channel` | integer | `0` | 通道号 |

**正确调用形式**（与本轮用户给的 curl 一致）：

```bash
curl "http://127.0.0.1:7003/dynamic_capture?ip=192.168.1.238&user=admin&pwd=&storage=local&filename=sweep_237e"
```

我们 `capture/INTEGRATION.md` 约定的 `/pool_capture` **存在** ✅，参数口径一致；
健康检查 `/healthz`、`/readyz` **实际存在且返 200** ✅（上一轮误判为 404，已更正）。

### 5.2 采图 500 的根因 —— ✅ 已修复（ADR-0009）

原始失败响应（保留为证据）：

```
{"success":false,"message":"Failed to capture screenshot","error_code":-1239510,
 "error_detail":"无头模式截图失败，可能需要虚拟显示或不同的截图方法",
 "play_handle":548405503,"headless_mode":true,"display":"","x11_window_handle":null,
 "snap_method":"MediaSnapImage", ...}
```

`display:""` 是**容器根本没设置 `DISPLAY`** 的直接证据——对应镜像原 `/entrypoint.sh` 里
「Xvfb 起不来 → `unset DISPLAY` → 继续启动服务」那条分支。容器实况：`DISPLAY=`（空）、
内部**无 X 服务进程**、但**镜像里装了 `/usr/bin/Xvfb` 与 `xvfb-run`**。

**四个结构性缺陷**（各自独立成立，与具体触发原因无关）：

| # | 缺陷 | 后果 |
| --- | --- | --- |
| 1 | Xvfb 起不来时**只打一行警告就继续启动** | 服务进入 headless，`DISPLAY` 未设 |
| 2 | 就绪判定用 `[ -S socket ]`，**残留 socket 会骗过它**（实测：`bind`+`close` 后仍满足 `-S`，但连接被拒） | 可能"假就绪" |
| 3 | Xvfb 是**无人监管**的后台进程，它死了 python 照跑 | 运行期静默降级 |
| 4 | `/healthz` **不反映显示器**（`DOCKER.md` 自述"只表示进程存活"） | 所有探针都显示正常，`restart` 策略**永不触发** |

⇒ 故障**不可见 + 不可自愈**，只能靠人手工重启。**已按 `docs/adr/0009` 加固并实测**：

| 手段 | 效果 |
| --- | --- |
| 真连接就绪探测（`connect()` 到 `/tmp/.X11-unix/X99`） | 残留 socket 不再误判 |
| **fail-closed**：Xvfb 10 s 未就绪 → `exit 1` | 静默 500 → **可见的 crash loop** |
| 运行期 watchdog：display 不可连 → 结束容器 | 被 `restart: unless-stopped` 自动拉起 |
| 独立 healthcheck（X socket + HTTP **`ready`**） | 首次真正覆盖显示器与 SDK 就绪 |

实测回归：`docker restart` → **15 s 回 `healthy`**；**运行期杀掉 Xvfb → `RestartCount` 0→1
自动拉起并恢复 healthy**；端到端 `dynamic_capture?ip=192.168.1.238` → **200**，
`"display":":99"`，`snap_result=0`，落盘 882–891 KB。

**顺带更正两处**：`/healthz`、`/readyz` 实际**存在且返 200**（更早一轮误判为 404）；
但 `/healthz` 在 `ready:false` 时**也是 200 且 `ok:true`** —— 只看它会把"SDK 未就绪、
采图必然 503"判成健康，**判定必须看 `ready`**。

对照留档：宿主另有一份姿势正确但**已停用**的单元
`/etc/systemd/system/xcloud-capture.service`（`xvfb-run -a` + `DISPLAY=:99`，指向**另一套安装**
`/home/sysadmin/algorithm/image_capture`）。它**与容器抢同一个 `:99` 和 `:7003`**，
当前 `disabled` 所以不会启动，**但不要 enable 它**。本次**保留容器**（其挂载与配置已验证），
只加固其入口。

### 5.3 旧静态多相机系统 —— 同一根因，同步恢复

每小时的 `:01:30` 经 7003 调 **6 台相机**。调用方在宿主侧：源 IP 是 docker 网桥网关
`172.25.0.1`，对应 `/home/sysadmin/algorithm/image_capture_tt`。

- **修复前**：整批 6 次**全部 500**（与 §5.2 同一个 xvfb 根因）
- **用户重启 xvfb 后**（22:01:30 那一批实测）：**6 次全部 200** ✅
- **本次加固后**：该状态在容器重启/宿主重启后**仍能保持**（此前每次重启都可能退回静默 500）

```
GET /dynamic_capture?ip=192.168.1.238&user=admin&storage=cloud&filename=611/20260912/611_1921681238_...  → 200
GET /dynamic_capture?ip=192.168.1.235&...&filename=612/...                                              → 200
GET /dynamic_capture?ip=192.168.1.236&...&filename=612/...                                              → 200
GET /dynamic_capture?ip=192.168.1.233&...&filename=7/...                                                → 200
GET /dynamic_capture?ip=192.168.1.231&...&filename=8/...                                                → 200
GET /dynamic_capture?ip=192.168.1.232&...&filename=8/...                                                → 200
```

**相机 ↔ 框映射**：`611→238`、`612→235`、`612→236`、`7→233`、`8→231`、`8→232`。

> **共存约定**：新系统一律走同一个 `:7003`，由服务的**单 worker 串行**天然互斥；
> 时间上**避开 `:01:00–:02:00`**（旧系统整批 6 次 × 实测 5.5 s ≈ 33 s）；客户端超时 **≥ 60 s**。
> **维护 7003 容器时同理**——重建会让端口短暂中断，别撞上这一小时一次的窗口。

### 5.4 MinIO（已在跑）

`tools_minio` 容器 → `192.168.1.250:9000`（host 网络）。配置在
`.../xcloudsdk_py_offline_20260120_175307/minio_config.json`：

```
MINIO_ROOT_USER / AWS_ACCESS_KEY_ID  = admin
bucket                               = mogu
MLFLOW_S3_ENDPOINT_URL               = http://192.168.1.250:9000
```

（密码字段存于此文件，勿复制进仓库。）

### 5.5 已有的 xvfb 相关单元（供实施参考）

| 文件 | 路径 | 状态 |
| --- | --- | --- |
| `xcloud-capture.service` | `/home/sysadmin/algorithm/image_capture/XCloudSDKDemo_CLI --http 7003` | `disabled` / inactive |
| `xcloud-capture-tt.service` | `/home/sysadmin/algorithm/image_capture_bak/…` | inactive |
| `.bak` | 两份 `.service.bak` | — |

> ⚠️ **不要 enable 它们**。它们用 `DISPLAY=:99` 且绑 `--http 7003`，
> 与 **容器**抢同一个 display 和端口。现场服务形态是**容器**（见 ADR-0009），这些单元只是历史遗留。
> 它们只有 `After=network.target`、没有 `Wants=docker`，开机顺序下谁先起来都不确定——
> enable 它们会引入一个**只在重启时出现**的偶发冲突，正好是最难查的那类。

---

## 6. C 类 · 机械标定（必须现场示教）—— 未变

当前站位坐标是**从行程均分推导**的（`layer_z` / `col_y`），这只是初值。

| # | 信息项 | 现状（推导值） | 影响 |
| --- | --- | --- | --- |
| C1 ★ | **各层实际 Z** | 5 层均分 212mm → 层距 42.4mm，层心 −21.2…−190.8 | 层高不等分则**整层偏移**，拍不到框心 |
| C2 ★ | **各框实际 Y** | 12 框均分 4492mm → 框距 374.3mm | 同上，横向偏移 |
| C3 | 实际框数 / 层数 | 12 × 5 | 改 `GRID_COLS` / `GRID_LAYERS` 两个常量即可，但要重示教 |
| C4 | 重复定位精度 | — | 决定标定（mm/px）能维持多久 |
| C5 | 到位后振动衰减时长 | 0.4s（沿用旧值） | 短了 → 运动模糊，长了 → 拖慢节拍 |
| C6 | 单轮巡检节拍要求 | 纯运动下界 ≈ 86 s（见指令复核 §2.5） | 决定速度档整定 |
| C7 | 相机工作距离 / 单帧视野 | — | 一帧能不能覆盖一框 |
| C8 | 蘑菇尺寸范围 | — | 决定视野与像素当量 |

---

## 7. D 类 · 你或供应商提供 —— 大部分已关闭

> **2026-09-13 晚补充（重大）**：入库日期与库房映射**已从生产系统找到并核实**，
> 不需要再靠人工提供。见 §11「入库数据源与库房映射」。

| # | 信息项 | 线索 |
| --- | --- | --- |
| D1 | 相机**密码** | ✅ **无密码**（`user=admin`，`pwd` 空）。旧系统脚本显式写 `pwd=` |
| D2 ★ | 相机是否锁定**手动对焦 / 固定曝光 / 固定白平衡** | ⏳ 未验证。**这一条直接影响测量可比性**，必须确认 |
| D3 | MinIO 凭据 | ✅ 已有（见 §5.4） |
| D4 | 补光灯 **OUT0 电平极性** | 说明书 + SDK详解 已给出结论：`status=1` → 低电平 → 导通 → 灯亮。⏳ 仍建议上机实测一次（实测 `OUT` 全 0，灯当前灭） |
| D5 | **IN0–IN3 的含义** | ⏳ 未确认。说明书 §三.3"若被选中，则表示输入口为**低电平**，为有效输入" → **低有效**。实测 `inputStatus=0x000F`（4 路全低），可能悬空下拉也可能真有信号 |
| D6 | **环控系统**接口形式 | 🟡 **部分解决**：环境数据（温度/湿度/CO₂）就在 `mushroom_farm.mushroom_operating_record` 表里（含 `temperature`/`humidity`/`co2`/`env_record_time`）。是否还有别的接口待确认 |
| D7 | **`.elo` 二进制格式说明** | ❌ **厂商未提供**；三份 PDF 都不含。建议直接向厂商索取 —— 决定 M2 能否实现 |
| D8 | 凭证管理方式 | 建议 systemd `EnvironmentFile`（密码不入仓库） |
| D9 | 现场对接人与响应时间 | 首次回零/急停需要人在场 |

---

## 8. 需要澄清的架构问题

| # | 问题 | 现状 |
| --- | --- | --- |
| G1 ★ | `analysis` 部署在哪、用什么端口 | ✅ **已关闭（2026-09-13 23:0x）**：`analysis` 从来没被部署过——`:8000` CLOSED 是"没装"，不是"端点不对"。现已装在 `/opt/mushroom-analysis`（venv + `mushroom-analysis.service`），`http://10.77.77.39:8000/ingest` 上线，`sync.PROD_INGEST_URL` 的默认值就是它。历史 outbox 已用 `deploy-flush-outbox` 补传 174 行，`/rounds` 5 条、`/images` 169 行 |
| G2 | 控制器**是否允许并发连接** | 实测当前**无任何活动连接**到控制器 ✅ 但并发准入仍需确认（M0 与 M1 同时连会互相踢线） |
| G3 | 旧静态多相机系统的去留 | **本轮定：238 保留给当前系统，两套共存**。但旧系统当前全 500（xvfb 根因），修不修要定 |
| G4 | **M2（`.elo` 脱机脚本）走哪条路** | ✅ **已决策：暂缓**（用户 2026-09-12 定）。M1 主机驱动已能独立跑全流程；将来若要恢复，**先向厂商索取 `.elo` 格式说明**，不要靠 6 个样例反推操作码 |
| G5 | console 后端是否本期做 | issue 01 仍是 `ready-for-agent` |

---

## 9. 一期最小可跑集 —— ✅ 已在目标机跑通

`patrol.debug` 的 import 闭包是**纯标准库**（`debug → patrol.fmc → motion_profile`，
不 import `yaml`、不 import `httpx`），所以目标机上**不需要 venv、不需要装任何 pip 包**。

已在目标机完成部署并验证：

```
/opt/mushroom-patrol/
├── lib/libFMC4030_2009_1.so     ← 已上传，ldd/CDLL 均通过
├── src/patrol/…                 ← 已解包，import 通过
├── m0-readall.py                ← 只读采集脚本（设备参数 + 状态 + IO）
└── patrol-src.tgz               ← 源码包

运行方式：
  FMC4030_LIB_PATH=/opt/mushroom-patrol/lib/libFMC4030_2009_1.so \
  PYTHONPATH=/opt/mushroom-patrol/src \
  python3 -m patrol.debug --ip 192.168.1.239 --port 8088 --device 1
```

---

## 10. 推进顺序（只读 → 能动 → 能拍）

| 步 | 动作 | 状态 |
| --- | --- | --- |
| S1 | 远程只读盘点（A 类） | ✅ 已完成 |
| S2 | `para` 读回控制器参数（B 类） | ✅ 已完成 |
| S2.5 | **电机控制指令逐条复核** | ✅ **本次完成**（`motor-command-review.md`） |
| S2.6 | **采图服务加固**（ADR-0009） | ✅ **本次完成**：fail-closed + 真连接就绪探测 + 运行期 watchdog + 独立 healthcheck；两条故障路径回归通过 |
| S2.7 | **巡检档整定 + 段序修复** | ✅ **本轮完成**（代码/测试/文档）→ Y 50→150，段序 bug 修复，整轮 36.8→4.6 min。**150 mm/s 现场验收通过**（用户"速度可接受"）。🔴 **但丢步/短停复验（2026-09-13 下午）在 374 mm 上量到 2.5% 的静默短停**——见 §10「新发现」，**不是丢步**（计数器诚实、不漂移），是运动控制层缺陷；M1 无人值守因此暂不放行 |
| S2.8 | **IO 实测**（补光灯极性 / IN0–IN3） | 🟡 **本轮做到"可判定边界"**：OUT0 对成像无可测影响、IN 四路恒低且无消费者。定论各需现场一个动作，见 `motor-command-review.md` §2.6 |
| S2.9 | **两段速 `goto()` 上机验证** | ✅ **完成（2026-09-13）**：纯 Y 一个框距往返三趟全通过——巡检段 150/1500、接近段 30/300、换挡点精确在 `目标−5 mm`、落点误差 0.000 mm、总耗时 1.03× 理论值。**M1 运动层就此打通**（此前真机只覆盖过 M0 单段）。见 §2.2.2，脚本 `two-phase-goto-check.py` |
| S2.10 | **M1 装配 + 整轮上机** | ✅ **完成（2026-09-13）**：`deploy` 常驻包上线，**整轮 60 站位无人值守跑通**——见下方「整轮实证」，有逐站取证与 outbox 行数支撑。单站均 ~9.7 s（p50 8.8 s，含运动 + 采图），**整轮 652.7 s**。图像索引逐帧落 outbox。**实测单次采图 5200 ms**（非先前估 1 s），轮时长以日志 `elapsed_s` 为准。见 ADR-0011 |
| S3 | `home` 首次回零 | ✅ **已完成（2026-09-13 00:48）**：先点动到 Y=+100 / Z=−40 造出可判别距离，再回零——Y 3.62 s、Z 4.82 s，落点均 0.000，方向实证通过。**同轮还完成了首次真机运动**，过程中发现并修复两个只有真机才暴露的缺陷（ctypes 签名缺失、起转窗口竞态），见 §4.5 与 ADR-0010 |
| S4 | `jog` 点动 | ✅ 已完成：Y +100 mm 用时 10.10 s、Z −40 mm 用时 10.15 s（与 M0 档 10 / 4 mm/s 的理论值一致） |
| S5 | 单站采图 | ✅ 已完成：首站 S101 定位到 `(187.148, −21.200)`（偏差 ΔY −0.019 / ΔZ 0.000 mm，远小于一个整步 0.475 mm），HTTP 200、373 KB、`display=":99"` |
| S6 | M0 全流程脚本 | ✅ `.scratch/prod-deploy/m0-commission.py`：默认只读演练，`--go` 才动作；每步前查轴是否静止，异常即急停；用**耗时数量级**判回零方向，不需要额外仪表 |
| S4 | `jog` 示教第 1 层第 1 框 → `record_station` | ⏳ 机构会动，需在场 |
| S5 | 单站采图联调（capture + 相机） | ✅ **前置已解除**：238 实测 `dynamic_capture` → 200、`snap_result=0`（§5.2）。⚠️ 避开 `:01:00–:02:00`，客户端超时 ≥ 60 s |
| S6 | 12×5 全量示教 → 生成 M1 站位表 | 🟡 **站位表已由 `build_grid` 生成**（60 站，`stations.yaml`）；**坐标仍是行程均分推导值**，C 类机械标定未做 |
| S7 | M1 装配（J1/J2/J3）+ systemd | ✅ **装配完成（2026-09-13）**：`deploy` 包 + `HttpxTransport` + `patrol-m1` 入口 + 相机 IP 单一化 + 图像索引落库（ADR-0011）。**整轮 60 站位上机跑通**。⏳ systemd 单元（J5）未写 |

### 整轮实证（2026-09-13 09:59–10:10，`--once --no-sync`，detached）

| 项 | 实测 |
| --- | --- |
| 结果 | `status: "ok"`，`n_results=60`、`n_failures=0`、`aborted=false` |
| 轮时长 | **652.7 s**（10m53s）；单站 p50 **8.77 s**、min 8.50、max 23.30、均值 9.72（Σ583 s） |
| 取证 | `runs/20260913-095938-2378816.jsonl`：`start` ×1 + `station` ×60 + `end` ×1，**fsync 逐行落盘** |
| outbox | **61 行** = 1 条 `round` + 60 条 `image_index`（ok=60 / fail=0）；journal 与 outbox 的站位集合完全一致 |
| 采图服务 | 本轮 81×200 + **4×500**；其中 2 次属本轮（S101 首次、S110），均被站位级重试救回 ⇒ **零站失败** |
| 回原位 | 末站 S512 在 Y=4304.8，回零行程 4118 mm，实测 27 s（理论下界 25.8 s）⇒ 回原位真实发生 |
| 末态 | **已显式回零并确认落点 (0.000, 0.000)** |

**⚠️ 同日上午 09:14 的那次尝试并未跑完，别与上表混为一谈。** 它跑到第 14 站（S211）被中断，
而当时 **outbox 里零记录**：`PatrolDaemon.run_cycle` 只在 `PatrolRound.run()` 正常返回后才写第一行，
于是"整轮被中断"表现为零记录、零文件、零痕迹——现场唯一能确认的是"它死了"。
本次已修（`patrol/journal.py` 轮次取证 + `--log` 落盘），并把**失败分级**纠对：
运动失败立即作废本轮（`FmcRoundAbort`），采图失败才走"连续 3 次"门槛。
上面那次中断的具体原因**无法从现场判定**（stdout 只存在于启动它的会话管道里），
这正是加日志与取证的直接动机。

### ⛔ 新发现（2026-09-13 下午）：`goto` 会**静默短停**，M1 无人值守暂不可放行

> **2026-09-13 晚更正**：本节早先的表述有两处过头，已按后续实测改写——
> ①「轴停在 Y=X」错：控制器**无位置反馈**，只能断言"计数器与指令不符"；
> ②「58% 短停率」混入了**误报**：`wait_stop` 在 running 清零就返回，而那一刻
> `realSpeed` 还有 10.5 mm/s，于是 `goto` 的接近段撞上未静定的控制器（实测 `-7`），
> 到位校验又读到未收敛值 → 假"短停"。详见下方「根因与修复后复测」。

**症状**：`Line_2Axis` 下达的目标，轴只走一部分就停；`running` 位正常清零、
`Check_Axis_Is_Stop` 正常报"已停"，**没有任何错误码**。不主动比"指令 vs 控制器计数"
就完全看不见它（已修：`fmc/client.py` 的 `_verify_arrival` → `TravelShortfallError`）。

**根因（线程采样把 wait_stop 与状态时间线对齐后确定）**：

```
t=0.001s RUN 置起，speed 1.49 → 149.76
t=2.594s RUN 仍置起，speed=10.48     ← 减速尾巴未走完
t=2.613s RUN 清零                    ← wait_stop 在这里返回
```

`wait_stop` 一见 running 清零就返回，而**运动尚未结束**。后果两条同源：
`goto` 紧接着下发接近段（同轴指令）→ 控制器未静定 → `接收数据错误 (code=-7)`；
同时读位置拿到未收敛值 → 到位校验误报（见过 1.8 mm / 238.9 mm 这种无意义读数）。

**修复**：`wait_stop` 在 running 清零后**再等速度降到 1 mm/s 以下并保持 0.2 s**；
新增 `read_settled_position()`（读失败重试）；`_verify_arrival` 改用它。

**修复后复测（2026-09-13 晚，`probe-verify-fix.py`，16 段移动）**：

| 类别 | 结果 |
| --- | --- |
| 成功段 | **12/16**，偏差 **max 0.000 mm**、p50 0.000（此前是 1.8 mm 量级假读数） |
| 耗时 | 短程 p50 3.15 s（理论 2.6+停稳），长程 p50 **30.39 s**（理论 30.0）⇒ 完整执行 |
| 失败段 | 4 段：`短程 374→0` ×3（偏差 −301 / −292 / −4.8）、`长程 0→4442` ×1（偏差 +4432） |

**★ 失败段的共同特征：耗时异常短**（0.73 s vs 正常 3.3 s；0.23 s vs 30.4 s）。
⇒ **控制器是在极短时间内自己终止了这次运动**，然后照常报"已停"、不报错。
这解释了 A/B 里那些 0.7 / 5.1 / 8.9 s 的"完成"——它们是**真实的控制器侧异常终止**，
不是丢步、不是机械卡阻、也不是测量误差。

**已排除**（各有实测）：

- **不是丢步**：拉长测量腿（2000 mm）后，触限耗时与基准差 ≤1.6 ms。
- **不是机械卡阻**：10 mm/s 走完 0→4442 全程两个方向零异常（250 mm 步长逐格验证）。
- **不是并发占用**：异常时段内采图服务最后一条 `pool_capture` 是自己那轮，`deploy.m1` 无进程。
- **回零可靠**：`LIMIT_N` 每次回零置起两次，触限点稳定；回零落点始终 0.000。

**残留频率（修复后、已排除误报）**：16 段里 4 段异常 ≈ **25%**（短程 2/8、长程 1/6，
样本小）；比早先的 58% 低，但那 58% 含误报，两者不可直接比。

**这与驱动/供电假设的关系**：降速 A/B 显示 150/90/50 三档异常率相近（~50%，含误报），
所以"降速"不是解法；现在的证据指向**控制器会自发终止某些运动**（内部保护/总线/驱动器
反馈），仍需现场查驱动器电流与供电、并听异响。

**为什么必须挡住**：静默短停意味着相机在**错误的位置**拍图，而元数据照写该站位编号
——错误照片带正确标签进入测量与生长曲线，比"少拍一帧"坏得多。修完之后，
短停会变成明确的 `TravelShortfallError`（该站记失败、跳过、可查），不再污染数据。

**处置建议（已做受控 A/B，结论：降速无效）**：2026-09-13 下午做了三档速度 × 两种距离的
A/B（脚本 `.scratch/prod-deploy/speed-ab.py`），每档先 `home_all()` 把坐标系拉回硬基准：

| 距离 | 150 mm/s（158 kHz） | 90 mm/s（95 kHz） | 50 mm/s（53 kHz） |
| --- | --- | --- | --- |
| **4442 mm**（换层） | 短停 **7/12（58%）** | **6/12（50%）** | **6/12（50%）** |
| **374 mm**（站间框距） | 0/24（0%） | 1/24（4%） | 1/24（4%） |

**⇒ 降速解决不了长程问题**（三档都在 ~50%）。再叠加另外两条实测：

- **10 mm/s（点动档）走完 0→4442 全程、两个方向，零短停**（`probe-stall-band.py`，
  250 mm 步长逐格验证）⇒ **不是机械卡阻**（否则低速也该卡）。
- 374 mm 在 150 mm/s 上 24/24 通过 ⇒ **短程没问题**。

合起来的事实是：**"长程 + 巡检档速度"的组合才失败，且与速度档位不敏感**。
这与"驱动器在某个脉冲频率上收不下"的朴素预期不符——**疑点转向驱动器的持续电流能力/供电
（长程持续高电流 vs 短程脉冲式），或机械阻力在长程上的累积（拖链阻力随位置增大）**。

**⚠️ 一个必须修正先前发现的读数口径**：A/B 里 90/50 两档显示"成功 6/12"，但那些"成功"
是**幻影成功**——轴卡住后计数器停在指令值，于是"回到 0"的指令变成原地不动的空操作、
瞬间"成功"（成功段均 1.69 s，而 4442 mm 在 90 mm/s 上至少需要 ~50 s）。
**⇒ 长程真实成功率比表格更低**；且这说明**到位校验有盲区**：它比对目标与实际读数，
但"实际读数"本身在短停后可能已不可信（见下条）。

**给现场的具体动作建议**：

1. 听/看长程快走时有无异响、皮带拍打、联轴器打滑（机械侧最省事的排查）。
2. 查驱动器电流设定与供电（FMDD50D40NOM 无手册，需厂商或现场记录）；若能把电流调大一档
   再复跑 `speed-ab.py`，是**最直接的判定**（电流够 ⇒ 故障消失）。
3. 手动盘车感受 Y 全行程阻力是否均匀（断电后推滑块），确认拖链/导轨有无局部阻力。
4. 在查清之前：**M1 无人值守不放行**。巡检档暂维持 150（降速无效，且 374 mm 上表现最好）。

### 与网络无关、可立即开工的胶水

| # | 缺的东西 | 说明 |
| --- | --- | --- |
| J1 | ~~**M1 装配入口**~~ | ✅ **已关闭（ADR-0011）**：`deploy/src/deploy/m1.py`，`patrol-m1` 命令。含 `--once/--check`、`--at-minute`（默认 :05，避开老系统 :01–:02 采图窗）、`--interval/--max-rounds`、`--stations/--outbox/--camera-ip/--ingest`、常驻模式 SIGINT/SIGTERM 分片睡眠 |
| J2 | ~~**真实 `links.Transport`**~~ | ✅ **已关闭（ADR-0011）**：`deploy/src/deploy/transport.py` 的 `HttpxTransport`——主机白名单、超时 90 s、`accept_json_errors` 把带信封键的非 2xx 返回业务层；`RetryingTransport` 提供重试语义。⚠️ 采图服务**用 HTTP 500 表示业务失败**（响应体仍是完整信封），必须与链路故障区分 |
| J3 | ~~**stations 加载 + 示教 CLI**~~ | ✅ **已关闭**：`m1.load_station_list(path, camera_ip)` 不存在则 `build_grid` 生成并落盘。**示教 CLI 仍未做**（依赖 C 类现场标定） |
| J4 | ~~**相机 IP 单一化**~~ | ✅ **已关闭**：`patrol/stations.py` 新增模块常量 `CAMERA_IP = "192.168.1.238"`，`Station.camera_ip` 默认取它 |
| J5 | ~~**patrol 的 systemd 单元**~~ | ✅ **已关闭**：`deploy/systemd/patrol-m1.service` + `mushroom-analysis.service` 落库并装到现场。**顺带挖出三个真问题**（见 §12）：系统 python3 缺 httpx（照文档跑必崩）、门禁关着时常驻进程会退出（systemd 不再拉起）、准入依据被冻结在启动那一刻 |
| J6 | **`spec.md` 与代码不一致** | `.scratch/fmc4030-camera-scan/spec.md` §4.1 仍写旧轴设计 |
| J7 | **console 后端服务** | issue 01 未实现 |
| J8 | ~~`capture/INTEGRATION.md` 健康检查口径~~ | ✅ **已更正**：`/healthz`、`/readyz` 实际存在且返 200 |
| J9 | **`client.jog()` 加班判据** | M0 CLI 已挡（`check_stop` 前置）；裸调用无保护，REST 路径会踩（见指令复核 F5） |
| J10 | ~~`elo/generator.py` 与 `deploy()`~~ | 🟡 **部分关闭**：本轮修了它的**段序**（F6，与 `goto` 同源缺陷，与 `.elo` 格式无关）；`.elo` 二进制格式（F1/F2）仍待 G4 决策 |
| J11 | **厂商库调用一律包 timeout** | F7 教训：库本身无超时，卡死只能 `kill -9`。新用到的 SDK 函数第一次上机都要用 `timeout` 跑（`probe-io.py` 就是这么定位的） |
| J12 | **采图服务连接池 idle 失效** | ⏳ **未根治**：vendored `capture` 服务在 idle 后连接池失效，**首次调用必然失败**（`error_code=-1239510`）。当前靠 patrol 侧**站位级重试**覆盖，代价是每轮约一次失败重试。根治需改 vendored capture（属另一交付物），本轮记此不越界 |
| J13 | **影像索引已落地（ADR-0005）** | ✅ **已关闭**：`analysis` 新增 `images` 表 + `/ingest` 的 `image_index` kind + `/images?station_id=` 查询；patrol 轮结束把每帧元数据落 outbox（成功/失败各一行）。⚠️ `analysis` 的 `PROD_INGEST_URL` 仍待 G1 确认 |


---

## 11. 入库数据源与库房映射（2026-09-13 晚，已核实）

### 11.1 相机 ↔ 库房（决定性证据：MinIO 对象名）

旧系统每小时 **:01:30** 抓拍一次，对象名把三个要素都编码进去了：

```
611_1921681238_20260316_20260913010130.jpg
└┬┘ └───┬────┘ └──┬───┘ └──┬─────┘
 │      │         │        └ 抓拍时刻 2026-09-13 01:01:30
 │      │         └ 入库日期 2026-03-16
 │      └ 相机 IP：192.168.1.238
 └ 库房号：611
```

| 库房 | 相机 | 入库日期（对象名） | 今日对象数 |
| --- | --- | --- | --- |
| **611** | **192.168.1.238 ← 本机滑块上的那台** | **2026-03-16** | 22 |
| 612 | 192.168.1.235 | 2026-04-05 | 44 |
| 7 | 192.168.1.233 | 2026-03-22 | 22 |
| 8 | 192.168.1.231 | 2026-02-28 | 44 |

⇒ **238 相机属于库房 611**。（与 §5.3 的旧推断 `611→238` 一致，现已有对象名作硬证据。）

### 11.2 入库日期与在库天数的权威来源

`mushroom_farm.mushroom_operating_record`（**460 万行真实数据**）：

| 字段 | 含义 | 实测 |
| --- | --- | --- |
| `batch_code` | **库房号** | `611` / `612` / `7` / `8` |
| `in_time` | **入库日期** | 611 库 = `2026-03-16`（另一批 `2025-12-24`） |
| `in_day_num` | **在库天数** | 611 库批次为 `01`–`25` |
| `device_name` | 设备名 | `611库-冷风机`、`611库-补光灯`… |
| `temperature`/`humidity`/`co2`/`env_record_time` | **环境数据**（D6 部分解决） | 真实值，如 18.8 / 91.50 / 2433 |

**★ 天数口径（差点搞错，已钉住测试）**：系统的 `in_day_num` **从 1 开始**——
入库当天就是 `01`。我们内部从 0 开始数，所以：

    入库当天 = 我们第 0 天 = 系统第 1 天

差一位会让"前 2 天不动"悄悄变成"只挡第 1 天"。`RoomState.verdict()` 现在**同时输出
两套口径**，`test_our_day_zero_is_the_systems_day_one` 钉住它。

**★ 与用户规则的吻合**：系统自己的采集窗口是 `01`–`25`。用户要求"前 2 天不动、
第 26 天起不自动巡检" ⇒ **恰好等于"只保留系统第 2–25 天"**，即去掉入库头两天、
去掉超出生长周期的尾部。两条规则是同一件事的两种说法。

### 11.3 ems 能管平台"不通" —— ✅ 已定位并修复（2026-09-13 晚）

#### ⚠️ 先更正本节早先的结论（那条是错的）

早先写的是"前端把 `http://localhost:8000/` 硬编码进 bundle ⇒ 打到自己电脑的 8000"。
**错在认错了前端**：那个 `localhost:8000` 来自容器 `frontend:1.0.0`，它发布在
**8081**，不是用户访问的入口。真正在 `:80` 后面的是另一个应用：

```
tools_nginx（0.0.0.0:80，server_name 10.77.77.39）
  location /        → http://172.17.0.1:7000   = 容器 primary-app:1.0.0  ← 页面标题「能管系统」
  location /service/ → http://172.17.0.1:7031   = ems-gateway
  location /h5/service/ → 172.17.0.1:7033（ems-security）
  location /minio/  → 172.17.0.1:9000
  location /scada*  → 172.17.0.1:9100 / 9691
```

`primary-app` 的三套环境配置（`local`/`test`/`prod`，按 `location.hostname` 选）：

```js
createUrlConfig(){const e=window.location.hostname;
  return e==="localhost"||e==="127.0.0.1"?Le.local.getUrls()
       : e==="10.66.66.28"?Le.test.getUrls():Le.prod.getUrls()}
prod: url:`${location.protocol}//${location.host}/service`   ← 10.77.77.39 走的正是这条
```

⇒ 浏览器实际请求 `http://10.77.77.39/service/security/auth/login`，经 nginx 到网关，
**地址是对的**。所以"前端配错地址"不成立。

#### 真正的原因：Redis 挂了 ⇒ 登录接口卡死 ⇒ 前端 10 s 超时

| 证据 | 内容 |
| --- | --- |
| `tools_redis` 状态 | `Restarting (1)`，**Restarts=4791** |
| 日志 | `# Bad file format reading the append only file appendonly.aof.588.incr.aof`、`# Failed to write PID file: Permission denied` |
| `ems-security` 日志 | `redis.clients.jedis.exceptions.JedisConnectionException: Could not get a resource from the pool` / `Caused by: java.net.ConnectException: Connection refused`（`172.17.0.1:26379`） |
| 前端代码 | `axios.create({baseURL:yo, timeout:1e3*10})`，超时文案「请求超时」 |

登录要写 Redis（会话/token 存储），Redis 连不上 ⇒ 请求挂住 ⇒ 10 秒后 axios 报
「请求超时」——这就是用户看到的"登录超时"。

**修复（已完成）**：

1. 备份 `/data/lenovo/tools/redis/data/appendonlydir` → `…bak.20260913_221306`；
2. `docker update --restart=no tools_redis`（防止修复中被拉起来）；
3. `redis-check-aof --fix appendonly.aof.588.incr.aof`（`ok_up_to=42783497`，截掉 3319 字节的坏尾）；
4. 启回容器并把重启策略恢复 `always`。

修复后：`Status=running Exit=0 Restarts=0`、`LISTEN 0.0.0.0:26379`、`PING` → `PONG`。

#### 认证头：`X-AUTH-TOKEN`（这条也纠正了早先的"14 种头都被拒"）

登录页把 `accessToken` 存进 cookie **`frontend_tenantId`**（名字很误导），axios 拦截器再把它塞进
`X-AUTH-TOKEN`：

```js
const o=Z.get("frontend_tenantId")||""; ...
o&&(t.headers["X-AUTH-TOKEN"]=o),
t.headers["X-AUTH-CLIENT"]="web", t.headers["X-AUTH-TENANT"]=r,
t.headers["X-AUTH-SCOPE"]=n, t.headers["X-LOCALE"]=d
```

实测（`POST /service/security/auth/login`，口令需 **MD5**）：

| 请求 | 结果 |
| --- | --- |
| `{"accountCode":"18912345678","password":"e10adc39…"}`（md5("123456")） | **200**，`data.accessToken` |
| 同请求但口令写明文 | `{"code":400200,"message":"帐号或者密码错误"}` |
| `X-AUTH-TOKEN: <token>` 调业务接口 | **通过认证**，进入业务层 |
| 不带 token / 头名不对 | `{"code":400100,"message":"认证失败"}` |

端到端实测（修复后）：

```
POST /service/monitor/device/connect/state/page/list  {"pageNum":1,"pageSize":3}
  → {"code":200,"message":"成功","data":{"total":28,
     "list":[{"deviceCode":"TD1_Q1MDINFO01","deviceName":"611库-育菇房信息",
              "state":1,"installationSite":"611库"}, …]}}
```

**注意**：`/monitor/**` 下多数接口是 **POST**；用 GET 打会得到 `code 999999 系统异常`
（服务端 `HttpRequestMethodNotSupportedException`），别把它当成后端故障——这条也踩过一次。

`mushroom_algorithm` 库里与识别相关的表（`mushroom_image_quality` 有
`image_path`/`room_id`/`in_date`、`model_inference_results` 有 `room_id`）**当前都是 0 行**
⇒ "定期抓图送识别"那条链路**当前没有产出**（与本次登录故障无关，是另一件事：没有在库批次）。

### 11.6 环境数据的权威来源 —— ✅ 已打通（TDengine `data1`）

早先只知道 `mushroom_operating_record` 里有 `temperature/humidity/co2`。实际时序库是
**TDengine**（容器 `tools_taos`，REST `:6041`，库 `data1`）：

| 项 | 实测 |
| --- | --- |
| 超级表 | 28 张 = 4 库房 × 7 设备，命名 `SUP_<deviceCode>`，如 `SUP_TD1_Q1MDTM01` |
| 子表 | `TD1_Q1MDTM01`（611 库温湿度）… 与 `device_register_ledger.device_code` 一一对应 |
| 列 | `ts`、`Hatmosp`（湿度 %）、`Co2`（ppm）、`Tatmosp`（温度 °C），tag `device` |
| 行数 | 611 库温湿度 **155,930** 行 |
| 最新一条 | `2026-04-09T10:53:38Z`：温度 18.5 / 湿度 90.68 / CO2 2378 |
| 其它三库 | 612 `10:54:03`、7 库 `10:53:58`、8 库 `10:53:39` —— 同一分钟停 |

⇒ 环境链路本身是通的，**停在 2026-04-09 是因为那批蘑菇采完了**（`mo_gu_batch` 里
611 的最后一天正是 `in_day_num=25`），不是故障。新批次入库即恢复。

查询要点：表名是**大小写敏感**的标识符，TDengine 里要用**反引号**（双引号是字符串字面量）：

```sql
select * from data1.`TD1_Q1MDTM01` order by ts desc limit 3;   -- ✅
select * from data1."TD1_Q1MDTM01";                            -- ❌ syntax error
```

REST 侧的历史数据接口是 `POST /service/monitor/device/insight/history/monitor`
（`mushroom_solution` 的 `history_data1`），参数 `metrics` 要的是
`TopologyTemporalResult` 对象数组（传字符串数组会 `code 100100`）——字段形状还没定下来，
**但从 TDengine 直读已经够用**，不阻塞任何事。

### 11.7 准入依据（入库日期）的取数脚本 —— ✅ 已落地并跑通真机

新增 `deploy-fetch-room`（`deploy/src/deploy/fetch_room.py`，28 个单测）：

```bash
# 现场直接跑（从 docker 里的 MySQL 读，口令只从容器 env 取，不落命令行历史）
deploy-fetch-room --room 611 --out /opt/mushroom-patrol/room.yaml
deploy-fetch-room --room 611 --print        # 只看不写
deploy-fetch-room --room 611 --from dump.tsv --today 2026-09-13   # 离线演练
```

**首次对真机库房跑的真实输出**（2026-09-13 22:37，`--print`）：

```
库房 611：入库 2026-03-16（批次 mogu-100，9792 包）
准入判定：第 181 天（入库当天算第 0 天；生产系统口径第 182 天，入库 2026-03-16）：
         已超过第 25 天，本周期不再自动巡检 ⇒ 本轮不巡检
```

数据源是 `mogu.mo_gu_batch`（不是早先以为的 `mushroom_operating_record`——后者是逐日
环境+管理记录，前者才是**批次台账**，一行一个批次）：

| 字段 | 含义 | 611 实测 |
| --- | --- | --- |
| `code_num` | 库房号 | `611` |
| `in_time` | **整库入库日期**（准入唯一依据） | `2026-03-16` |
| `in_day_num` | 系统口径在库天数（停更值=采完那天） | `25` |
| `in_num` | 入库包数 | `9792` |
| `info_code` | 该库「育菇房信息」设备号 | `TD1_Q1MDINFO01` |

脚本的安全约束：未来日期拒绝写入、字段数不符即报错（不静默错位）、原子落盘
（同目录临时文件 + `os.replace`，daemon 不会读到半截 YAML）、`entry_date` 变化明确
打印旧值→新值、生成后先过一遍 `patrol.room` 校验再落盘。


### 11.4 数据链路验证 —— ✅ 闭合

3 站位实测（`--stations stations_chain.yaml`，照片 `storage=cloud` 直传 MinIO）：

| 检查 | 结果 |
| --- | --- |
| 索引行 ↔ MinIO 对象 | 3/3 对象**真实存在**（MinIO 侧逐个 `test -d` 核对） |
| 元数据完整性 | 时间 / 库房 / 入库日期 / 批次 / 站位 / 框 / 坐标 / 角度档 / 对象名 / cloud_url **无缺失** |
| 可回溯性 | 给定「库房 611 + 站位 S101 + 时刻」→ 直接定位到 `http://192.168.1.250:9000/mogu/20260913/B101_S101_top45_215257.jpg` |
| round 汇总 | 带 `room_id=611`、`entry_date`、`batch_no` |

样例索引行：

```json
{"kind":"image_index","room_id":"611","entry_date":"2026-09-11","batch_no":"TEST-20260911",
 "ts":"2026-09-13T21:52:56","ok":true,"box_id":"B101","station_id":"S101",
 "yz":[187.167,-21.2],"angle_profile":"top45","camera_ip":"192.168.1.238",
 "object_name":"20260913/B101_S101_top45_215257.jpg",
 "cloud_url":"http://192.168.1.250:9000/mogu/20260913/B101_S101_top45_215257.jpg",
 "elapsed_s":13.1}
```

**最后一跳也已闭环**（2026-09-13 23:0x，详见 §12）：`analysis` 装到 `/opt/mushroom-analysis`
并起了 systemd 服务，历史 outbox 补传 174 行进 `analysis.db`。链路现在是完整的一段：

    patrol 采图 → MinIO + outbox(JSONL) → /ingest → analysis.db → GET /images?station_id=

### 11.5 room.yaml 已切换为**真值**，测试替身另存 —— ✅ 已落地（2026-09-13 22:42）

611 库的真实入库日期是 **2026-03-16** ⇒ 今天第 181 天 ⇒ **按规则拒绝自动巡检**。
用户确认后按"诚实 + 可测两者都要"落地：

| 文件 | 内容 | 判定（2026-09-13） |
| --- | --- | --- |
| `/opt/mushroom-patrol/room.yaml` | **真值**：由 `deploy-fetch-room` 生成，`entry_date: 2026-03-16`，`batch_no: mogu-100`，`packages: 9792` | 第 181 天 ⇒ **不巡检**（fail-closed 成立） |
| `/opt/mushroom-patrol/room.test.yaml` | 原测试替身，顶部加了"这是替身、真值在 room.yaml、用法 `--room`"的说明 | 第 2 天 ⇒ 会巡检 |

两份都实测过：`load_room_state` + `verdict()` 各跑一遍，输出与上表一致。

**在库房空置期要继续测链路**：`patrol-m1 --room /opt/mushroom-patrol/room.test.yaml`。
不带参数跑永远会被真值挡住——这正是我们要的性质，别用改真值的方式让测试跑起来。

门禁逻辑一行未改，`deploy-fetch-room` 跑一次即可换批次（`entry_date` 旧值→新值会打印出来）。

---

## 12. 打通"最后一跳"时挖出的三个真问题（2026-09-13 23:0x）

装 `/ingest` 与 systemd 单元的过程中发现的，都不是配置问题而是**代码缺陷**：

### 12.1 照文档跑必崩：系统 python3 没有 httpx

README §2 原话是"源码按 PYTHONPATH 直接用（M1 不需要 venv）"，但 `deploy.m1` 顶层就
`import httpx`（`deploy.transport`）。实测：

```
PYTHONPATH=/opt/mushroom-patrol/src /usr/bin/python3 -c "import deploy.m1"
→ ModuleNotFoundError: No module named 'httpx'
```

也就是说照文档写的那条 `python3 -m deploy.m1 --check` **一次都跑不起来**；之前几轮上机
用的是临时环境（`/root/.cache/uv/...` 里那套），现场并没有可复现的运行方式。
**修**：`/opt/mushroom-patrol/.venv`（httpx + pyyaml），systemd 单元指向它。

> 顺带更正一条早先的判断：目标机**能连 PyPI**（`pypi.org` 与阿里云镜像都 200），
> 离线 wheel 不是必须的（留作后备）。

### 12.2 门禁关着时，常驻进程会**退出**（新批次来了没人等）

`main()` 里原本是：

```python
if not allowed:
    return 0     # 明确不动 = 正常退出（别让 systemd 判成 failed 反复重启）
```

单轮（`--once`）这么写没问题，但**常驻**模式下 systemd 看到的是"正常退出"，
`Restart=on-failure` 不会拉起 ⇒ 库里空着的那段时间进程根本不在了；等新批次入库、
窗口打开，机器上没有任何东西会开始巡检。README 里"天数走进窗口后无需重启即开始巡检"
在这条路径上是**假的**。

**修**：常驻模式不再因门禁退出——进入循环，每个调度时刻重新判定；判定不过就只记
`skipped`，一次都不连控制器。实机验证（门禁关，`--interval 5 --max-rounds 1`）：

```
[23:07:12] 准入未通过：跳过启动预检（本轮不碰控制器）
[23:07:12] 进入常驻模式：固定间隔 5.0 s，站位 60，单轮约 6–7 min
[23:07:12] 准入未通过：进程保持常驻，每个调度时刻重新判定（期间不碰控制器）
[23:07:17] 本轮不巡检：第 181 天 …
[23:07:17] 第 1 轮结束：skipped，耗时 0.0 s
```

### 12.3 准入依据被冻结在进程启动那一刻

`PatrolDaemon(room_state=<读好的对象>)`：`room.yaml` 会被 `deploy-fetch-room` 换掉
（换批次是常态），而常驻进程手里还是旧日期的对象，天数是错的，且它不会报错。
**修**：`room_state_path=` 让 daemon **每轮现读**文件，读失败的原因原样带进日志。
实机验证（跑起来后把文件从 `2026-03-16` 换成 `2026-04-09`，不重启）：

```
[23:07:35] 准入判定：第 181 天 …（旧文件）
[23:07:42] 本轮不巡检：第 157 天 …（新文件，已在跑的第 1 轮就用了新值）
```

### 12.4 附带：软限位校验从"启动一次"改成"每轮一次"

ADR-0008 的软限位校验原先只在 m1 启动预检里做。常驻进程里"启动时对、之后被改错"
没人管。现在 `PatrolDaemon._connect()` 连上就校验，不过就断开、本轮判失败——
**一个运动指令都不发**（`test_daemon_refuses_to_move_when_soft_limits_are_not_commissioned`
钉住这条）。校验不过不重试：软限位是配置错了，重试三次只是把一个错误说三遍。

### 12.5 `--check` 在门禁关闭时会"假通过"

同一条路径的另一面：`--check` 早先走 `if not allowed: return 0`，于是库里没有批次时
运维跑 `--check` 看到的是 rc=0，以为"预检通过"，其实**一次都没检查**。
**修**：`--check` 是显式运维动作，不受门禁影响，始终做只读预检，并把当日准入结论
一并报出。实机验证（门禁关，位置 Y=0.00 Z=0.00、软限位通过、60 站位、rc=0）：

```
[23:06:45] 连接控制器 192.168.1.239:8088 …
[23:06:45] 控制器就绪：位置 Y=0.00 Z=0.00，模式=manual
[23:06:45] 预检通过（60 站位，摄入端点 http://10.77.77.39:8000/ingest，同步启用；
           今日不在准入窗口（不影响预检结论））
```

### 12.6 补传入口：`deploy-flush-outbox`

"端点确定后用同一个 store 补传"此前没有正经命令，只能即兴写 Python。新增
`deploy-flush-outbox`（13 个单测）：推成功才动文件、失败一个字节不变、成功则归档到
`sent/`（补传是一次性动作，事后要能回头核对送出去了什么）。现场实跑：

```
✔ outbox_run2.jsonl：已补传 61 行 {'round': 1, 'image_index': 60} → 归档 sent/outbox_run2.jsonl
✔ outbox_chain.jsonl：已补传 4 行 …      ✔ outbox_full.jsonl：61 行
✔ outbox_run.jsonl：47 行                ✔ outbox_smoke.jsonl：1 行
补传完成：174 行，0 个文件失败
```

结果（`analysis.db`）：`/rounds` 5 条（含 2 条 partial/aborted 的历史轮次），
`/images` 169 行；`/images?station_id=S101` 返回 4 张，坐标已摊平成 `y`/`z`，
`object_name`/`cloud_url` 齐全 ⇒ 按「站位 + 时间」回溯照片这条路走通了。



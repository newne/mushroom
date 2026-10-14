# Bot 分工契约（蘑菇项目）

2026-10-13 定，用户已确认。团队由 5 个 Bot 组成，Chief 是群 admin 与跨域仲裁者。
群聊：**蘑菇项目**。本文件是单写者与权限的权威来源；与群通知冲突时以本文件为准。

闭环：**相机移动拍照 → 算法识别生长（每天增长量 / 菌杆长度 / 菌盖直径 / 菌簇范围）→ 结合温湿度与历史调控经验 → 输出调控指令**。

## 单写者清单

同一文件只有一个写者。跨域需求由提出方写票据，归属方实现；任何人都不顺手改别人的目录。

| Bot | 领地（可写） | 负责 |
| --- | --- | --- |
| 巡检手 | `patrol/**`；`deploy/**`（除 `deploy/src/deploy/console.py`）；`docs/patrol/fmc4030-camera-scan/**` | FMC4030 双轴运动、货架网格与站位锚点、巡检轮次与调度、采图与 outbox、角度档、capture / preview、`images` 索引写入侧 |
| 测量师 | `measure/**`、`src/**`、`models/**`、`notebooks/**` 及其测试 | 分割 / 实例分割、像素→毫米标定、四个生长量的计算与评估、测量结果生产 |
| 环境官 | `analysis/**` | 环境数据入库（小时聚合）、批次台账、操作日志、区间表 `ranges`、"调控指令"建议层 |
| 界面师 | `web/console/**`、`deploy/src/deploy/console.py` | 巡检台三模式（实时 / 历史 / 环境）页面与 console 反代接口 |
| 发版官 | `docker/**`、部署编排与 `.env` 镜像 tag、systemd 单元、生产运行态 | 镜像构建与推送、compose 重建、systemd 单元、发布后复验、事故回执 |

`deploy/src/deploy/console.py` 与 `web/console/index.html` 是历史上冲突最热的两个文件（曾出现拦写，以及把他人未提交改动一起带上生产）。**opencode 若要改这两个文件，先在群里 @Chief**，由 Chief 协调，不得并发写。

**`deploy/**` 的两处 carve-out**（2026-10-13 裁定）：`deploy/src/deploy/console.py` 归界面师；`deploy/systemd/**` 与生产上的安装/运行态归发版官独占；`deploy/**` 其余仍归巡检手。

**但 systemd 已废**（用户红线，见下「部署红线」）：`deploy/systemd/**` 不再作部署路径，只保留语义记录。"服务怎么启动"改由环境官出**容器化启动语义**票（票 12：ExecStart 等价物、WorkingDirectory、venv、`EnvironmentFile=/etc/mushroom/ems.env`、游标与「整点重算当前小时」），发版官按票出 compose 定义，不自行发明启动参数；依据只取线上实测与 `docs/patrol/env-analysis/issues/01:22`、`09:23`，缺的写「未知」不许猜。

## 外部协作者 step code（2026-10-13 用户指定）

- 不在这份 Bot 名册里（无法用 `message_bot` 直达，派活与改线经用户转达），但在**同一个仓库**里工作、可被派活。
- **独占文件**：`third_party/capture/**`、`patrol/src/patrol/capture_client.py`、`patrol/src/patrol/framing.py`、`deploy/src/deploy/framing_wire.py`、`deploy/src/deploy/preview.py`。
- **当前任务**：[`docs/patrol/imaging-quality/issues/01-camera-imaging-quality.md`](../patrol/imaging-quality/issues/01-camera-imaging-quality.md)（成像质量与取景）。
- **规矩**：要改上述之外的文件 → 写票据给归属方，不直接改；`web/console/**` 与 `deploy/src/deploy/console.py` 归界面师，不许碰；发版由发版官按 docker compose 流程执行，它不碰生产；产出由**巡检手**复核。
- 现场日后若被"计划外"改动，先确认是不是它。

## 工作规则

1. **先读后写**：改动前读 `AGENTS.md`、`CONTEXT.md`、相关 spec 与 ADR。
2. **遇到 `FS_STALE_VERSION`**：重读最新内容，在其上重做自己的改动，绝不覆盖别人的改动。
3. **证据**：每个改动要带测试或可复验证据（命令 + 结果）。没验证过的不许说已验证；不确定的必须标"不确定"。
4. **票据**：跨域需求与新增工作写 `docs/patrol/<feature>/issues/NN-*.md`（编号先看目录，避免撞号）。
5. **汇报**：在群里一句话说结论（做了什么 / 证据 / 下一步 / 卡在哪）；跨域冲突 @Chief。
6. **提交（2026-10-13 两次事故后加固）**：一律用**路径限定提交**——`git commit -m "…" -- <你的路径>`；**只 `git add` 具体文件**（禁止目录/glob）；**add 完立刻提交**，中途要停就先 `git restore --staged`（不留暂存）；提交前 `git diff --cached --name-only` 核一眼 index 里有没有别人的东西。**共享分支上禁止 `reset` / `rebase` / `commit --amend`**，回退找发版官用 `revert`。事故记录：① 有人先 `git add` 后没提交，留下的暂存文件被下一个人 `git commit` 一起带走（`dce88d8` 带走 3 个别人的文件），随后又出现一次 `reset HEAD~1`，靠再提交才捞回；② 5 个域共用一个 index，"谁 add 谁被顺走"——`5658570` 扫走了巡检手的票、`2502574` 扫走了测量师的 spec 段（内容一字未改）。若第三次再犯，改用每人一个 `git worktree`。**并发 `index.lock` 会让 `git commit` 返回非 0**——撞锁必须**重试到成功**，绝不"提交失败就放着不管"（2026-10-13 那次暂存被顺走，起因之一就是一个被锁撞失败的 commit 被搁下了）。`dce88d8` 是**悬空提交**（不在 HEAD 祖先链上），内容已被其它提交覆盖：**任何人不要 cherry-pick，也不要 gc**。

## 当前目标：菌簇量测 + 比例标尺 + 环境记录（2026-10-14 用户口径，含当日修订）

用户原话（09:4x）：「当前已有部分巡检图像，设计图像分割算法，根据菌包直径与菌杆长度的比例，推测当前时间菌杆的平均长度、簇长势以及后续菌帽长度；同时优化巡检逻辑，随着菌杆长出菌袋表面，应保持与蘑菇平行的视角来看蘑菇，这样测量更准确。」
**当日修订（09:5x，收窄）**：「菌包的直径和长度是标准的，由于是单目相机，所以评估**菌簇平均长度、直径**即可。」

**交付四块（修订后）**
1. `measure/**`：菌包 / 菇体分割（**菌包实例是标尺的前置**）；用**标准菌包尺寸常数**做帧内标尺（按排/同深度用局部尺度，**禁止全画面单尺度**）；一期交付量 = **菌簇平均长度与直径**（复用 `mean_cluster_h_mm` / `mean_cluster_w_mm`，单位 mm，走 estimate + 不确定度）。**菌杆长度估算与菌帽长度预测降为探索项，不进验收。**
2. `patrol/**`：取景随菌秆长出菌袋保持"与蘑菇平行"的侧视——现役固定角度下**主要是 Z 相位问题**（现场 5 mm 步距找"看得见袋顶 + 菇"的相位、按真实层距≈50 mm 铺锚点、抓图链路复核）；**相位容忍仅 10 mm**，建议每站两相位；只有换角度档才需要现场动支架。
3. `analysis/**` 记录重点：**入库以来的环境反馈值 + 关键节点调整值**（`/operations` 权威、逐条不聚合；`device_setpoint_changes` 只作交叉验证），用于后续直接输出调控指令；**入库时间以平台读取为准**（不从 0 自推）；**入库首日温湿度剔除**（暂定平台入库时刻起首个 24 h；**原始数据保留在库不删**，只在 `ranges`/统计/学习中排除并标记 `first_day`）。
4. `web/console/**`：菌簇长度/直径优先，带量纲与不确定度；首日剔除段必须**显式标记**，不许静默消失。

**验收**：菌簇长度/直径出非零行且带 estimate + 不确定度（升格条件照票 15）；远排/低置信写 NULL + `unreliable`、缺标尺写 `no_scale`；有可复跑测试与评估。现成帧：`218` 张合格（48 站位）+ `109` 张坏帧样本 + `10` 张预览，源帧 **2880×1616** 灰度。

**红线**：估算值绝不进 `verified` / 区间表 / 建议层；不静默降级、不写 0；不连业务库（`tools_mysql` 禁用）；生产只读归发版官。

## 当前优先级（2026-10-13 用户口径）

1. **巡检成像质量（首要）**：清晰拍到各阶段蘑菇的长势，为后续测量与决策积累数据基础。成像条件（对焦/曝光/白平衡锁定、避让加湿期过曝、可用帧判定）比测量算法更急。
2. **测量只做最小接线**：第 13 天菇刚长出菌袋表面、菌杆矮且无菌帽，测量方案等长高后持续优化——现在只保证管道通、每行带 `quality` 标记，不追精度、不投入标定与训练。
3. **环境数据持续积累**：小时聚合入库、批次台账、操作日志保持稳定。
4. **建议层与自动下发推后。**

## 部署红线

- **发版一律走 docker compose 在 prod 运行，严禁使用 systemd**（2026-10-13 用户口径）。线上现存的 `mushroom-analysis.service`、`mushroom-env-ingest.service`、`mushroom-audit-ingest.{service,timer}`、`mushroom-batch-ingest.{service,timer}` 全部迁成 compose 服务；单元内容只作语义记录（迁移输入），**不作部署路径**。定时类作业用容器内常驻轮询或容器内定时，沿用"一镜像多角色 + `docker/patrol-entrypoint.sh`"模式，不引新机制。

## 生产与安全

- **只有发版官**能连生产 `10.77.77.39`、能在 WSL 里构建并推送镜像。其它 Bot 只产出代码与测试，发版由发版官执行并回执（镜像 tag、sha256 / digest、健康检查结果）。
- **凭据**用 `request_secret` 存成环境变量后读取，禁止出现在聊天、仓库、票据与命令行历史里。
- **调控指令 v1 只输出"建议 + 依据"**，由人确认后现场执行，**绝不下发设备**。自动下发属于难回退决定，须先写 ADR 并单独评估。
- 已知坑：`docker cp` 热替换会在镜像重建时丢失（须回写进镜像）；页面体积超命令行上限，必须分块 base64 上传并核对 sha256（`plink "cat > 远端" < 本地` 静默失败过）；需要 pandas 的模块发到 prod 会打挂 analysis（prod venv 无 pandas）；编排文件叫 `mushroom_solution.yml`。

## 试点范围

- 试点只押 **611 库 + 当前批次 `mogu-107`**（入库 2026-09-30）。其它三个库房只做数据、不做回填。
- 第一优先：**让 `analysis.measurements` 出现非零行**——"最优参数区间"与调控决策都被它堵着。

## 已定契约口径（2026-10-13）

- **measurements 写入契约**：字段唯一清单 = `measure/src/measure/record.py` 的 `RECORD_FIELDS`，与 `analysis/src/analysis/db.py` 的 `MEASUREMENT_FIELDS` 一字不差，禁止第四套命名。**`room_id` 必填、由生产者填**（不许 analysis 侧从 `box_id` 反推库房）；幂等键 `(ts, room_id, box_id)`，同一站位有多角度档时再加 `angle_profile`。长度一律 mm；缺失 = NULL，**绝不写 0**；失败帧不产行。`ts` = 采图时刻本地 ISO8601，与 `rounds`/`images` 同口径，生产侧不预聚合。`quality` 是逗号 flag 串，枚举要登记进契约，**区间表与决策层只吃 `verified`**。measure 的验收标准是 **`/measurements` 出现非零行**，不是 `ranges` 有行。
- **术语**：`box_id` 是**菇框号**（`B105` = 第 1 层第 5 框），站位号是 `S105`；当前 1:1，但关联一律用 `(room_id, box_id) + ts`，不依赖 1:1 假设。
- **setpoints**：`/setpoints/changes` 砍掉（wontfix），只做 `/setpoints/daily`（每日设定末态）。变更频次/幅度读已有的 `/operations`——权威源是命令审计，`device_setpoint_changes` 仅作交叉验证。
- **对齐**：`align.py` 按 `(zone, hour)` 分组，分箱口径换成 spec §2.7 的 D 分档（图像 4 阶段作第二切片）。
- **测量口径**：主口径 = 每站位 `--mm-per-px`；帧内标尺用**菌包标准尺寸**——`BagReference` = **宽 240 mm（画面水平）× 高 140 mm（竖直）**（来源 `src/segmentation/geometry.py:37`、`src/scripts/processing/run_segmentation_inference.py:440,443`、`src/segmentation/README.md` §5），按排/同深度用局部尺度、禁止全画面单尺度；现场只需确认"画面里的水平可见宽度确实对应 240 mm"（袋是卧放还是立放）。
- **角度档 / 平视下怎么量（2026-10-14 用户新口径，覆盖 10-13 的"不可测"写法）**：现役单档平视 `level0`，不做支架改造、不加转轴/云台，`GRID_ANGLES` 不动。用户 2026-10-14 要求：**用图像内「菌包直径 : 菌杆」比例**（菌包直径已知，240×140mm 为既有旁证）反推每帧 mm/px，据此**估算**菌杆平均长度、簇长势、菌帽后续长度（预测）。⇒ 平视下这两列不再写「不可测（无角度档）」，改标「**估算（estimate）**」：必须带 `scale_source=bag_diameter`（或等价标记）与不确定度，**未经验证不得进 `verified` 统计、不得进区间表与建议层**；升格条件由测量师给出。**仍然禁止把估算静默当验证值、禁止写 0**；整帧不可用（`no_light`/`low_confidence`/`no_detection`）照旧写「不可用」。取景随菌杆长出菌袋保持**与蘑菇平行**的侧视（巡检手出票，涉观察高度偏置/是否需现场作业）。若日后现场重做支架换角，**换角生效日 = 数据口径分界，跨角不可比**。
- **三态展示与"不确定度"口径（2026-10-14 裁，票 15/18；给页面与下游同一把尺）**：量分**三态**——**估算**（`estimate` + `estimated_fields` + `scale_source`）／**不可测**（没有任何可用标尺或角度档 ⇒ NULL + `no_scale`，页面「不可测（无标尺）」）／**不可用**（帧或值不可信 ⇒ NULL + `unreliable`/`no_light`…，页面「不可用（原因）」）；**三态各用各的词、都不进 `verified`**。展示规则：**一期不显示"不确定度"数字**（全库无 SD/CI 列、一期不新增列）——只显示「**估算（标尺来源）**」+ 量纲；样本够时另给**分布区间 P10–P90**，**明写"分布"**、`n<5` 只写"样本不足（n=X）"；**禁止**把分布叫"不确定度"、**禁止写成 `±`**。真要误差预算 = **标尺精度**（尺寸公差 → mm/px 比例误差）+ **分割误差**（±5px × 当前 mm/px ≈ ±1.4–3.8mm）+ **投影**（θ 未测前**必须标"未定"**），**公式由测量师给、优先用已有字段推导**，确实推不出来才开列（要单独报）。三态计数**按"列 × 行"分开**（估算 N · 不可测 M · 不可用 K），**估算绝不并入已验证**；**没有异常/没有估算 ⇒ 零痕迹**。
- **建议层 v1**：只输出「建议 + 依据 + 样本是否不足」，不写任何设备端点位。
- **轮内容完整性字段的形状（2026-10-14 定形，源头 `patrol/src/patrol/daemon.py:259/343-344`）**：`rounds.suspect_duplicate` 是**布尔**（真机 JSON `true`，SQLite 存 1/0 ⇒ **判标记要布尔与 1/0 都认**）；`rounds.dup_groups` 是 **JSON 数组、元素为对象** `{"sha256_12": <12 位>, "n": <帧数>, "stations": [<站位>…]}`（无重复时 NULL 或 `[]`）——**不是 `list[str]`**（曾有一次这样误记，一律按本条为准；渲染按对象、兼容旧字符串）；`images.frame_dup` 0/1、`images.dup_group` = sha12 字符串、`images.content_sha256` = 64 位十六进制。**"缺列 / 值为 NULL" ≠ "已核查干净"**。

## 已核事实（发版官只读盘点，2026-10-13 14:50，全部为实测）

- **库房主机与算法侧同机**：`hostname=ycfserver`，`eno1 192.168.1.250/24`（控制网）+ `tun0 10.77.77.39/24`（VPN）；巡检侧与算法侧同属一个 compose 项目 `mushroom_service`（8 个容器）。`CONTEXT.md:83-85` 与 `deploy/README.md:7` 不矛盾，是同一台机器的两个身份。
- **现役 48 站**（`configs/stations.yaml` 48 条、`/api/grid` 12×4、最近三轮 48/48）；仓库里多处"60"是 60 站时代的残留。
- **单轮实测 ≈7 分 40 秒**（470 / 462 / 457 s，0 失败）；`deploy/src/deploy/console.py:280` 的 `ROUND_TYPICAL_S = 11*60` 让页面预计时间偏悲观约 40%。
- **`.env` 的镜像 tag 已漂移**：pin `cde46ce`，实跑 `b64027b`（14:48 起）。此时任何人跑一次 `docker compose up -d` 都会把巡检侧回滚到 10-12 的版本。preview 另跑 10-08 的 `station-calibration-…` tag（`.env` 与仓库里都没有），而 `PATROL_IMAGE` 一把管三个容器——隐式耦合，待拆。
- **线上有 5 个不在仓库的 systemd 单元**：`mushroom-env-ingest.service`、`mushroom-audit-ingest.{service,timer}`、`mushroom-batch-ingest.{service,timer}`（每天 05:20 批次台账同步）。单元文本由发版官逐字转录落盘（标注"语义待票 12 确认"）。
- **`mushroom-audit-ingest.service` 有缺陷**：`Type=simple` + `--once` + `Restart=always` ⇒ 每 60 s 跑一次（24 h 内 2466 次），设计是每 10 分钟；持续打环控平台鉴权接口。
- **analysis 现役代码未纳入 git**：`analysis/src/analysis/{batch_ingest,batches,audit,audit_ingest,env/tdengine,environment}.py` 与 `analysis/uv.lock` 未跟踪。
- **prod venv 无 pandas/numpy**（Python 3.12.3，只有 fastapi/uvicorn）：发往 prod 的模块不许在模块级 `import pandas`。
- **14:48 的生产重建是外部协作者 step code 做的**（新镜像 `0.1.0-20261013144515-b64027b`，页面被换成 HEAD 版）；`root pts/3 ← 10.77.77.47` 就是它的会话。**冻结已解除**（2026-10-13 16:0x，用户确认它已完成发版、不再动容器与代码）。生产访问**含只读**仍全部归发版官。
- **`.env` 的 tag 漂移已修**（2026-10-13 16:33）：`PATROL_IMAGE` 对齐实跑值 `0.1.0-20261013144515-b64027b`，preview 拆出独立变量 `PATROL_PREVIEW_IMAGE`；现场 compose 与仓库逐字节一致，只 `compose config` 解析、未重建（五容器 uptime 未归零）。
- **迁移用镜像已构建并推送**：`0.1.0-20261013163437-52f6c03`，由干净 HEAD 的 `git archive` 构建（不含任何在飞改动），镜像内 `analysis/jobs.py` + `api.py` lifespan 自查通过。迁移时序见 `docs/patrol/prod-deploy/issues/01-analysis-compose-migration.md` §4 —— **Step 1 必须 API 与 audit 同步进容器**（`:8000` 独占，无法只迁 audit），env 留到 Step 2，batch 等直连只读账号落地。

## 当前开放问题（2026-10-13 只读侦察结论，待复核后逐条定）

1. ✅ **测量 0 行的根因（接线缺口）已解决**（2026-10-13）：`measure_fn` 已接上，真机帧产出 3 行测量并入库、同键重发仍是 3 行（幂等成立）、两个库房同 `box_id` 不互相覆盖（主键 `(ts, room_id, box_id)` 生效）。
2. ✅ **mm/px 口径已定**（ADR-0024）：主口径 = 每站位 `--mm-per-px`；`src/segmentation/geometry.py` 的菌袋外接框反推只作**旁证**。仓库内仍无标定数据文件——每站位 mm/px 待现场给，不阻塞接线。
3. 🔄 **平视下的量测口径已按 2026-10-14 用户新口径改写**：现役单档平视 `level0` 下，用「菌包直径 : 菌杆」比例（菌包直径已知）反推每帧 mm/px，**估算**菌杆平均长度、簇长势、菌帽后续长度（预测）。这些量标 `estimate` + `scale_source=bag_diameter` + 不确定度，**未经验证不得进 `verified` 统计 / 区间表 / 建议层**（升格条件待测量师）。取景随菌杆长出菌袋改为"**与蘑菇平行**"的侧视——由巡检手出票（票 09），涉观察高度偏置与是否需现场作业。换角仍属现场机械作业，**换角生效日 = 数据口径分界**（ADR-0024）。
4. **现役 vs 归档口径冲突**：CLIP + LLaMA-VL 图文链路**仍在生产调度里跑**（`src/scheduling/tasks/register_vision_jobs.py:22-35`），而用户口径是"老链路归档不做考虑"。需用户裁定后再动代码。
5. **写通道不存在**：仓库唯一的设定值写接口 `src/utils/get_data.py:475-479` → `settings.toml:16` 的 `edge/device/point/cmd/private` **零调用者**；`decision_analysis_dynamic_result` 的 `apply_time/apply_result/rollback` 只有列定义。这就是 v1 只出建议的原因。
6. **测量侧仍缺**：无分割 / 实例分割权重与 GT；**菌簇范围尚无列**（待环境官 DDL）；日聚合增长量未实现（`analysis/src/analysis/growth.py:12,42-53` 只有相邻两点差分）。检测器已落到 baseline（`measure/src/measure/baseline.py`），**不追精度**（用户口径：等菇长高再迭代）。混跑才失败的 2 条 analysis 测试（`ranges` 物化状态污染）待环境官修。
7. **第 3 条的准确化：换角是机械活**。没有云台/PTZ，也没有电动换角——相机固定在双轴导轨滑块上，spec 明确"不做电动变角"（`docs/patrol/fmc4030-camera-scan/spec.md:65,209`）。换角 = 现场重新固定支架并重扫 z（`station-retarget.md` §10.2/§10.3）。归巡检手，产出是**机械改动清单 + 代价 + 现场配合项**，不能承诺软件换角。
8. **没有服务端拼接图，也没有缩略图**：`mosaic` 是前端 `layer×col` 缩略图网格（`web/console/index.html:2224-2268`，`docs/patrol/console-ui/spec.md:193-197`）；巡检帧实为 **2880×1616**（`container-cutover.md` §21，2026-10-13 现场取证：此前"1080×648"是错的），**一屏 10 张原图 ≈7 MB**、48 格更甚，是已知欠账。缩略图/服务端拼图属新增能力，须开票据。
9. **没有"按站运动"接口**：只有 `/api/cmd` 的 goto 绝对坐标（`deploy/src/deploy/manual_exec.py:371`），页面"上一站/下一站"只是历史图翻页（`index.html:587-589`）。无独立位置反馈（`patrol/src/patrol/fmc/client.py:83-88`，`real_pos` 是脉冲自述）。
10. **待现场核实（未找到证据，不许猜）**：相机是否已锁定手动对焦/固定曝光/白平衡（`docs/patrol/prod-deploy/gap-list.md:372` 标 D2 未验证）——**不锁则所有跨轮测量数字不可比**；现役站位 48 还是旧文写的 60（`patrol_serve.py:7`、`register_patrol_jobs.py:5`）；单轮时长 11 min vs 6–7 min；库房主机与 prod 是否同一台 `10.77.77.39`；`mushroom-env-ingest.service` / `mushroom-audit-ingest.timer` **不在仓库**（只在 `issues/01:22`、`issues/09:23`），重建即丢。

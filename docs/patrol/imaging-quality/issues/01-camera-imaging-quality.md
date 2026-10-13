# 01 成像质量与取景：各生长阶段都拍得清，且跨天可比

- 负责人：**step code**（外部协作者，用户指定）　复核：**巡检手**　发布：**发版官**（docker compose，严禁 systemd）
- 立项：2026-10-13　状态：ready-for-agent

## 为什么现在做（用户口径）

当前重点是**优化巡检过程——清晰拍到各阶段蘑菇的长势，为后续测量和决策积累数据基础**。

现在种植在第 13 天左右，菇刚长出菌袋表面、**菌杆矮且没有菌帽，测不了量**；测量的精度优化推迟到长高之后。这一阶段唯一必须做对的事是：**图拍得清，而且跨天可比**。拍不清楚 / 不可比，后面所有测量与决策都是在噪声上建模。

## 交付

1. **相机成像参数能不能锁**（最高优先）
   对焦、曝光、白平衡、分辨率现在是否固定？`docs/patrol/prod-deploy/gap-list.md:372` 的 D2 至今标着"未验证"。**不固定则跨天图像不可比**。
   - 若可通过 XCloud SDK / 采图服务（`third_party/capture/**`）下发 → 给出代码改动 + 每站位/每角度的参数记录；
   - 若只能现场设置 → 给出**现场作业清单**，每项带验收方法（同一站位连续两轮对比样张，差异应在噪声量级）。
2. **加湿期坏帧**：加湿期水汽导致过曝、对比度低（`CONTEXT.md:22`、`docs/patrol/fmc4030-camera-scan/spec.md` §12）。给出"可用帧"判定——避让加湿段，或对受影响帧打标记并在下游剔除。**不许坏帧静默进入积累数据。**
3. **取景随生长阶段**：未出袋要俯拍看菌包表面，出袋后平视（`CONTEXT.md:20`）。给出当前阶段（第 13 天）的取景方案与验证样张。
4. **证据**：同站位跨天样张对比 + 参数记录 + 判定规则的单元测试。

## 文件边界（单写者，硬约束）

**step code 独占**：`third_party/capture/**`、`patrol/src/patrol/capture_client.py`、`patrol/src/patrol/framing.py`、`deploy/src/deploy/framing_wire.py`、`deploy/src/deploy/preview.py`。

**要改这些就走票据，不直接改**：`patrol/src/patrol/{orchestrator,round,stations,daemon}.py` 及其它 `patrol/**`、`deploy/**`（除上面列出的）→ 票据给**巡检手**。

**不许碰**：`web/console/**` 与 `deploy/src/deploy/console.py`（归界面师）；`docker/**`、`.env`、编排文件与生产（归发版官）。

## 验收

- 巡检手复核：`uv run pytest`（在 `patrol-workspace/` 下）+ 样张证据 + 判定规则单测通过；
- 发布由发版官按 **docker compose** 流程执行（**严禁 systemd**），发布后核对远端哈希与页面/接口 200。

## Comments

### 2026-10-13 step code · 第 1 项结论：软件路径不能可靠下发，转现场作业清单

**一句话**：对焦/白平衡在厂商 SDK 里没有接口；曝光/分辨率有一条 `DevSetChnConfig` 的口子但
**没有结构体定义**，且线上服务不是本仓库源码起的——所以不给代码改动，给现场清单（见下）。

#### 一、软件路径的事实（都能复跑）

| # | 事实 | 证据 |
| --- | --- | --- |
| 1 | 采图服务 4 个端点的参数只有 ip/user/pwd/storage/filename/channel，**没有任何成像参数** | `third_party/capture/src/xcloudsdk_py/server.py:160/178/196/215`；Query 定义 `server.py:162-167`（其余三处同） |
| 2 | SDK 封装只绑了 13 个函数，无一与成像参数相关 | `third_party/capture/src/xcloudsdk_py/sdk.py` 的 `_bind()`：Init/UnInit/SetLogTypeAndLevel/RegisterCallback/UnRegister/SetLocalUserNameAndPwd/DevLogin/DevLogout/MediaRealPlay/StopMediaPlay/MakeKeyFrame/MediaSnapImage/MediaSnapImageEx |
| 3 | 厂商 `.so` 有配置类接口，但**没有**对焦/白平衡/曝光符号 | 宿主上 `nm -D --defined-only libXCloudSDK.so`（20630 个导出符号）：有 `XCloudSDK_Device_DevSetChnConfig`/`DevGetChnConfig`/`DevSetSysConfig`/`DevGetSysConfig`/`DevChnConfig_NotLogin`/`DevSysConfig_NotLogin`/`EnableAEC`；`focus`/`white`/`exposure` 相关的符号**为零** |
| 4 | 库里成对的 exposure/brightness/contrast 字样来自**内置 ffmpeg/x264/FDK-AAC**，不是相机参数 API | 同一次 `strings`：`exposure_class`/`exposure_options`/`BrightnessValue`/`Contrast` 与 `FDK_*`/`x264` 的 `bitrate` 混在一起——是 AVOption 表 |
| 5 | **结构体定义缺失**，写不出正确的 ctypes 绑定 | 厂商离线包内 `find -name '*.h' -o -name '*.py' -o -name '*.md'` 只有 3 个 py + 2 个 md（capture-healthcheck.py / levels-fix/capture.py / README-capture-fix.md / DOCKER.md），无头文件、无演示 |
| 6 | **线上服务不是本仓库源码起的**，改仓库到不了生产 | `docker inspect xcloudsdk_py_offline_20260120_175307-xcloud-capture-1`：镜像 `xcloudsdk-py:0.1.0`（厂商）；`.../levels-fix/capture.py → /usr/local/lib/python3.10/site-packages/xcloudsdk_py/capture.py:ro` 是**宿主目录** bind-mount，不是本仓库 |

⇒ 第 5 条是关键：`DevSetChnConfig` 即便就是那个口子，没有结构体布局也不能绑——瞎绑有段错误
风险，而采图服务是单 worker 串行（`INTEGRATION.md:21`），一崩这一轮 48 张全没。所以**不写
投机代码**。第 6 条意味着即便写了也要宿主侧复制 + 重建容器，属发版官/现场动作。

#### 二、现状测量（用已存图量的，可复跑）

| 项 | 结论 | 数据 |
| --- | --- | --- |
| 分辨率 | ✅ **已锁** | 2026-09-13 → 10-13 一个月的存图**全部** 2880×1616（`/api/images` 拉 200 条逐帧量） |
| 白平衡 | ➖ **不适用** | 存图 R=G=B，通道差 **0.00**——相机处于红外单色模式，没有彩色信息可平衡 |
| 电平修正补丁 | ✅ **在线上** | 存图 min=5/9、max=255、0.86% 像素 >235；SDK 原输出会被压在 235，能摸到 255 说明 `_normalize_levels` 生效 |
| 曝光 | ❌ **明显不稳** | 同站位跨天均值 79.6 → 67.4 → 102.9（±25%）、近黑像素 0.50% → 8.77% → 0.00%、σ 41.4 → 53.7 → 41.7 |
| 对焦 | ❓ **存图判不了** | 同站位跨天梯度能量 1.71 → 2.20 → 4.79（2.8 倍），但它与对比度、以及蘑菇自身在长（第 13→14 天）混在一起，分不开——要现场用静止标定板判 |

#### 三、现场作业清单（每项带验收方法）

按性价比排序。前两项不做完，后面的验收都不过。

1. **在相机/DVR 端锁成像参数**（对焦手动、曝光手动；关自动增益、自动白平衡、背光补偿 WDR、
   宽动态；若有"夜晩/红外"模式开关，记录当前档位）。
   验收：同站位**连续两轮**（避开加湿段）各取一帧，平均亮度差 < 5%、σ 差 < 10%。
   现在这项差得远（±25% / 41→54），锁完应该落进噪声量级。
2. **确认补光灯到底照不照得进画面**（`station-retarget.md` §1 早有"实测无效/照不到视野"的记录，
   而巡检抓拍是会开 OUT0 的）。若灯没进画面，曝光就只能靠菇房蓝光，那永远随环控飘。
   验收：手动抓拍一帧开灯、一帧关灯，两帧同站位平均亮度差 > 10% 才算灯真的在干活；
   若差异 < 2%，先修灯再谈锁曝光。
3. **对焦锁定**：手动对焦拧到菌包袋面清晰后锁死（有锁紧环的锁紧），记录拧了多少圈。
   验收：袋面固定纹理（或贴一张静止标定板）连拍两轮，梯度能量差 < 10%；人眼复核不糊。
4. **锁完必须重测取景**：成像参数变了，景深与视场都变，原来定的 z 锚点要重扫
   （`station-retarget.md` §7.2 的流程，用**抓图链路**扫，不用预览）。
5. **参数快照归档**：把上面锁定的值写进 `stations.yaml` 的 `exposure_note`（或另建一份参数
   快照文件），否则下次换相机/重装又要从头摸。⇒ 这张要改 `patrol/src/patrol/stations.py`
   与站位表，**走票据给巡检手**，不在我的文件边界内。
6. **厂商要结构体**：把 `DevSetChnConfig`/`EnableAEC` 的头文件或演示向厂商索取。拿到之后
   "软件下发"才重新可做——那时我可以绑只读的 `DevGetChnConfig` 先验证结构体，再谈写。

#### 四、我没验证的

- `DevSetChnConfig` 到底能不能改分辨率/曝光（没有结构体，没法试；也不该在生産上瞎试）。
- 相机当前的对焦/曝光档位（SDK 没有 getter，只能人去相机端看）。
- 梯度能量那 2.8 倍 swing 里，对焦 drift 占多少（被对比度和蘑菇生长混住了）。
- 补光灯是否有效（第 2 项要现场开/关灯各抓一帧，我这边只能读到历史帧，没有可控的开关灯对照）。

---

### 2026-10-13 step code · 第 2/4 项：可用帧判定 + 单测 + 跨天样张（结论待两侧复核）

#### 一、判定落在哪、长什么样

`patrol.framing.judge_frame(gray) -> FrameQuality`：纯函数，返回 `(ok, flags)` + 统计指纹
（mean/std/p99/近黑占比/过曝占比）。`flags` **逗号分隔**，与 `measure.quality.frame_quality`
同形；要拼 `measurements.quality` 枚举串时按 `measure.contract.JOINER`（`+`）接。

**只判曝光轴，两个标记**：

| 标记 | 判据 | 阈值依据（人群 = 20 张巡检帧 + 10 张 sweep + 4 张仓库 fixture，全 2880×1616） |
| --- | --- | --- |
| `underexposed` | ≤16 像素占比 > **1.5%**，或 p99 < **150** | 正常帧 ≤16 占比 0.000–0.476%；坏帧 2.781–10.83% ⇒ 5.8 倍空档。p99：正常 183–247，唯一暗帧 99 |
| `overexposed` | ≥250 像素占比 > **5%** | **原样沿用测量师**（`measure.quality.is_overexposed` 默认参），不另立尺度；本地人群最高 2.580%，无一触发 |

#### 二、为什么"低对比度"没有单帧门控（这条是结论，不是偷懒）

加湿雾的签名是"均值 +33%、σ −32%"，但 **σ≈30 对这台相机是正常值**：

| 统计量 | 正常帧人群 | 雾帧 | 可分？ |
| --- | --- | --- | --- |
| σ | 22.5 – 46.1 | 31.2 – 37.0 | ❌ 完全重叠 |
| p99−p1（幅宽） | 79 – 224 | 128 – 155 | ❌ 完全重叠 |
| 上下半幅亮度差 | 1.5 – 41.0 | 1.5 – 14.9 | ❌ 场景相关，非雾相关 |

⇒ 任何绝对阈值都会误杀正常帧。**雾只能相对判**（同站位历史基线），配方已写进票 02；
预防靠加湿保持（hold）。判定与 hold / 补光灯窗的对应关系写在 `judge_frame` 的 docstring 里。

#### 三、跨天样张证据（同一站位、同一 z，只有天数不同）

S107（y=2433.17 全程未变，z=−90）：

| 日期 | 均值 | σ | p99 | ≤16 | LapVar | 单帧门控 |
| --- | --- | --- | --- | --- | --- | --- |
| 10-12 15:20 | 100.63 | 37.41 | 225 | 0.000% | 79 | 通过 |
| 10-13 10:05 | 125.62 | 31.18 | 200 | 0.000% | 52 | 通过 |

⇒ 均值 **+24.8%**、σ **−16.6%**、p99 **−11.1%**：**两帧都通过单帧门控**。这就是"雾必须
相对判"的直接实证——也是票 02 第 3 条的数据来源。

跨轮次（同一天 10-12，各 4 张）：09:27 那轮 3/4 帧 ≤16 占比 2.781–6.264%（**触发
underexposed**）；13:16 与 15:20 两轮 8/8 帧全过。20 张本地巡检帧里 3 帧被标记、17 帧通过。

#### 四、顺手发现并修掉的真 bug（`--framing` 接线漏了一层解码）

`deploy/framing_wire.py` 把 `bright_region_offset` 直接当 `evaluate` 用，而生产链路给的是
**JPEG 字节**、检测器要的是**灰度数组**。复现：

```
bright_region_offset(b"\xff\xd8\xff\xe0jpeg-bytes")
→ ValueError: could not convert string to float: b'\xff\xd8\xff\xe0jpeg-bytes'
```

即 `--framing` 一开就会在第一个站位把整轮带崩。因默认 `--framing off`，线上从未触发。
已补上解码层（`framing_wire.to_gray`），并留兼容分支保住 `deploy` 那个喂数组的既有用例
（票 04：把用例改成正交契约后删分支）。

#### 五、判定怎么用起来（不等别人）

`deploy/framing_wire.py` 已把判定包在检测器外面（`patrol.framing.gated_evaluate`）：
坏帧 ⇒ 检测器报"没找到目标" ⇒ 微调**不挪**（帧照旧交差、不丢图，只是不拿它当定位依据——
加湿期的雾会造出假的亮区质心，追着它挪只会把 trim 学歪）。默认开，
`PATROL_FRAME_QUALITY=0` 一键关回。

#### 六、单测（`patrol/tests/test_framing.py`，+14 项）

合成图一组：健康帧通过且带回指纹、近黑帧标记、**阈值落在人群空档里**（0.4% 过 / 1.6% 不过，
这是"可复算"的钉子）、暗帧（p99<150）单独触发、过曝用测量师的尺度、双标记顺序固定、
逗号分隔与测量师同形、非灰度图拒绝。

真实帧一组：仓库内已归档的 4 张真实相机帧（`lampcheck_on/off`、`m0_S101`、`S306-candidate`）
**必须全部通过**——不许误杀。

门控缝两组：坏帧 ⇒ `found=False` 且**不再喂给检测器**（省一次解码）；好帧原样透过。

#### 七、没验证的

- 阈值只在 34 张帧的人群上验证过（其中巡检帧 20 张、跨 3 个层位 × 2 天）。样本偏小，
  尤其"坏帧"只有 3 张同轮次的。**建议攒够 200 张后复算一遍**（脚本配方在票 02 第 3 条）。
- p99<150 这条只有 1 张帧支撑（sweep_236c），靠的是物理理由；样本增多后可能要调。
- 判定接进 `fine_tune` 的路径只有单测覆盖，**没有上机跑过**（`--framing` 需要先标定
  `--mm-per-px`，而它至今没标定——`station-retarget.md` §2）。
- 生产访问已按 Chief 要求全部停止，本轮没有任何线上读取。

# 06 同一轮多个站位拿到字节完全相同的帧：标记落地 + 轮次语义

- 提出：**step code**（外部协作者）　执行：**巡检手**（轮次/索引语义）+ **发版官**（服务侧生效）
- 立项：2026-10-13　来源：发版官全池只读扫描，`docs/patrol/prod-deploy/issues/05-frame-pool-tail-scan.md` §5
- 状态：ready-for-agent

## 事故（发版官实测，已由我本地复算确认）

2026-10-09 18:20 那一轮：前 13 站（S101→S112、S212）正常（666–737 KB），
**从 S211（18:22:05）起 35 帧字节完全相同**（sha256 `41685c09…`、92364 B、mean 2.31、
p99 2.0），横跨 S211→S401 历时 5 分 11 秒，21:20 那轮又恢复正常。
**而该轮标记 `ok=1 / 48 帧 / 0 失败`。**

对照：10-08 的近黑帧是 5 种不同字节（89837/90493/92060/94754/112448 B）——那才是各站
独立的真欠曝。**字节相同 ⇒ 没有真的逐站取流**，两者性质不同，不可混谈。

我已用 `.scratch/frame-quality-samples/tail/dark/` 的副本复算：35 帧同 sha、48 个不同
object_name、`all-metrics.csv` 里 18:2x 段 48 行。

## 机制（代码路径已核，见 `third_party/capture/src/xcloudsdk_py/capture.py`）

`_pool_capture_locked`（巡检走的就是 `/pool_capture`）把 `wait_play_data` 的返回值
**丢掉了**：

```python
self._sdk.make_keyframe(ip, channel=channel, stream_type=0)
self._sdk.wait_play_data(conn.play_handle, timeout_s=5.0)     # ← 返回值没人接
snap_rc = self._sdk.snap_image_ex(conn.play_handle, str(file_path), ...)
```

`wait_play_data` 等的是 `EUIMSG_END_BUFFER_DATA / PLAY_INFO / YUV_DATA /
EXSDK_DATA_FORMATE_FRAME` 四个消息之一（`sdk.py:334-342`）——**周期性的 PLAY_INFO 就能
点亮它**，所以它返回 True 并不证明"新帧到了"。底层视频一旦变黑/冻结，
`MediaSnapImageEx` 只是把解码器里那一帧反复写出来：文件就绪、上传成功、
`success=True`。客户端看不出问题——object_name 每站都不同（那一轮 48 站 48 个名字），
patrol 又是"库内零网络调用"，拿不到字节。**只有服务侧能算指纹。**

**已排除的假设**（都有证据，别重复查）：

| 假设 | 排除依据 |
| --- | --- |
| 服务端超时后回旧文件 | `_build_paths` 后先 `unlink`，`_wait_for_file_ready` 要求文件存在且大小稳定 |
| 对象名冲突导致互相覆盖 | 该轮 48 个站 48 个不同 object_name（`all-metrics.csv`） |
| MinIO 上传串对象 | 本地文件按本站 object_name 生成、按同一名字上传、传完删除 |
| patrol 侧复用同一帧 | 同上；且 35 帧的 object_name 两两不同 |
| 各站独立真欠曝 | 独立传感器的噪声不可能产出**逐字节相同**的 JPEG（10-08 即为反例） |

**未定**（要生产日志，归发版官）：相机停止送流 vs SDK 解码器卡死，两者症状相同。
旁证：故障是瞬时的（18:22 起、21:20 前恢复），且起点在一轮中途。

## 服务侧已改（step code，`third_party/capture/**`）

`_pool_capture_locked` / `_dynamic_capture_locked` 成功路径的响应里多三个字段
（外加把 `play_data_ready` 也报出来——它以前被丢掉，现在是诊断线索）：

| 字段 | 含义 |
| --- | --- |
| `image_sha256` | 上传前那份字节的 sha256（电平修正**之后**算，描述的就是入库字节） |
| `same_as_previous` | 与紧邻上一次成功抓拍是否同一份字节 |
| `duplicate_streak` | 连续第几份相同（1 = 首次出现；事故那一轮会一路涨到 35） |

**只标记、不改控制流**：不因为重复就判失败——那会把一次相机抽风放大成整轮中止
（`MAX_CONSECUTIVE_FAILURES=3`），而现场需要的是"这些帧别进统计"。
算不出指纹时（读不到文件）返回 `None` 且 streak 断掉，**不猜**。
单测：`third_party/capture/tests/test_capture_duplicate.py`（9 项，含事故形状的离线推演）。

⚠️ **与 `measure.quality` 的关系**：本次事故的冻结帧恰好近黑（p99=2.0），
`no_light` 判据已经能拦。但冻结在**白帧/正常帧**上时质量三判据全绿，只有字节比对能发现
——两者互补，不能互相替代。

## 交付（巡检手）

1. **落进元数据**：`patrol.orchestrator.StationCapture.run` 的 `meta` 里带上
   `image_sha256` / `same_as_previous` / `duplicate_streak`（来源：
   `CaptureResult.raw`——客户端不用改，`raw` 就是完整响应）。`deploy.manual_exec`
   的手动抓拍同样带上（`manual_exec.py:424` 有 `imaging_profile` 的先例）。
2. **轮次语义**（本条要你定，两种都合理，把取舍写进代码注释）：
   - **A｜`ok` 保持"抓图链路成功"原义**，另加一列 `frame_quality`（或 `suspicious`）
     落标记。代价：`ok=1` 依旧表示不了"图是真的"（事故的根因就在这）；
   - **B｜重复帧判 `ok=0`**。代价：`n_failures` 上涨会撞 `MAX_CONSECUTIVE_FAILURES=3`
     把整轮中止；要么把"重复"排除在连续失败计数外，要么单独计数。
     我倾向 **B + 单独计数**：现场一眼能看到"这一轮 35 帧可疑"，而不是去数一个 flag 列。
   - 无论哪种，**帧照旧入库**（不静默丢弃——这是成像质量票的硬要求）。
3. **`images` 加列**（若选 A）：DDL 在 `analysis/src/analysis/db.py:73-97`，
   照 `imaging_profile` 的加法（空 = 那时没判过，不编值）。
4. **补单测**：伪造一轮"前 13 帧不同、后 35 帧相同"的响应序列，断言轮次级标记。

## 交付（发版官）

线上采图服务是**厂商镜像 + 宿主 bind-mount 的 `levels-fix/capture.py`**，改仓库到不了
生产。生效步骤（照 `capture-fix/README.md` §三 的先例）：

1. **先 diff** 宿主上 `.../xcloudsdk_py_offline_20260120_175307/levels-fix/capture.py`
   与仓库版本——若有宿主本地改动，先合并不要直接覆盖；
2. 覆盖后 `docker compose up -d --force-recreate`（**不要**带 `-f`，否则跳过覆盖层）；
3. 复核：`curl 'localhost:7003/pool_capture?ip=192.168.1.238&storage=local&filename=probe'`
   连抓两次，第二次响应应带 `same_as_previous: true`；
4. 若暂时不生效：**不影响**既有行为（三个新字段只是多了，旧调用方不读它们）。

## 验收

- 单测绿（`third_party/capture` 12 项 + 巡检手新增的轮次级用例）；
- 用 10-09 那轮的 object_name 序列回放：35 帧被打标、且**没有**被丢弃；
- `patrol-workspace` 全量 pytest 绿。

---

# 发版记录（发版官，2026-10-13）

## A. "未定"已结案：**设备掉线（停流），不是 SDK 解码器卡死**

只读取证（`docker logs` 采图容器 + 宿主 `.../xcloudsdk_py_offline_20260120_175307/log/`
的 `mediacodec.*.log` / `xcloudsdk.*.log`，均为本地时间）：

| 时刻 | 事件 | 来源 |
| --- | --- | --- |
| 18:20:08–**18:22:13.952** | `notifyPlayInfo` 心跳**每秒一条**，`bits` 每秒变化（106 559–311 086）、`relativeTime` 每秒 +1000 ⇒ **流是活的** | `mediacodec.3.log` |
| **18:22:13.952** | **最后一条心跳**（`relativeTime`=124002）；此后到 18:27:16 一条都没有 | 同上 |
| 18:22:14.171 | S211 的 `CatchPicture`（冻结第一帧，正好压在心跳停之后 0.2 s） | `xcloudsdk.2.log` |
| 18:22:36.106 | SDK 才记 `HeartBeat-Timeout` → `Disconnect`，error **-99991** | 同上 |
| 18:23:10.051 | `NetDevice Timeout`(Spent:61) → `OnDisConnect` → `StartPlay_End`，**`device disconnect, result: -99991`** | 同上 |
| 18:22:38–18:32 | 重连一直失败：`NetConnect_End  Ret: '-1000(EE_NET)'` | 同上 |
| **18:32:33.436** | **`Login success. Ret: 0, SessionId: 3`** | 同上 |
| **18:32:35.052** | 心跳恢复，`relativeTime`=**125002**（接着 124002 走） | `mediacodec.3.log` |

⇒ **设备 `192.168.1.238` 整机掉线，中断 18:22:13.95 → 18:32:33（约 10 分 20 秒）**，
那一轮（18:22:05→18:27:16）**整段落在中断窗口内**，所以之后每一次 `wait_play_data` 只可能超时、
每一次 `MediaSnapImageEx` 都写解码器残留帧。交叉校验：18:28–18:59 心跳 **1644** 条 ≈ 18:32:35→19:00 的 **1644** 秒。

**两条副产品**：① **SDK 的掉线检测比实际停流滞后 22 秒** ⇒ 靠 SDK 报错拦不住，也解释了
"冻结头几帧日志里什么都没有"；② **"心跳停了"本身就是一个极廉价的看门狗信号**（不需要相机配合）。
③ 本改动正中要害：从 18:22:14 起 `play_data_ready` 只会是 False，**在第一帧就能抓住**。

**全池真 sha256 重扫**（发版官，只读）：全池 3147 帧里度量元组 size≥2 的组**只有 1 个**（就是那 35 帧）；
补下后**不同 sha256 数 = 1**、**跨轮跨天重复 0**、**假阳性 0** ⇒ **没有同型轮次**，处置范围收干净。

## B. 服务侧进生产的路线与证据（C1 镜像）

**已完成的构建与发布（2026-10-13 20:0x）**：

| 项 | 值 |
| --- | --- |
| 基础镜像（厂商） | `registry.cn-beijing.aliyuncs.com/ncgnewne/xcloudsdk-py:0.1.0`，digest `sha256:7f0a61a98fac1d60f3c8776947ca6f3a392318124bc5ca77f276c0f718b2976d` |
| 推前核对（三方同 digest） | 线上 `xcloudsdk-py:0.1.0` ID = **`sha256:10cad10c8826d6f92436410c9ba16d5da8248cab03ed50481896ca91ae3d2797`** ＝ 离线包 `image.tar.gz` 的 `manifest.json → Config` ＝ `docker load` 后镜像 ID<br>离线包 `/home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307/image.tar.gz`，sha256 `f0e09d66684e85723d37674ac879a9ff4f77374221fbae39e07c5b92938ec005` |
| 命名 | **只新增不覆盖**（推前确认 registry 无该 tag）；此镜像是**厂商镜像**，本仓只把它发布到自建 registry 以便可复现重建，不对其内容做任何修改，许可与分发口径以厂商离线包为准 |
| 衍生镜像 | `registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_capture:0.1.0-20261013200745-f584691`，digest `sha256:750f4e4294c4ba047851b57f8ebd6a894aed5d03e3fdba4875df8f59fc7af75c` |
| 构建源锁定 | HEAD `f584691`；`capture.py` 工作区 blob ＝ `HEAD:` blob ＝ `d3d62975…`；全量门禁 **939 passed / 0 failed** |
| **镜像内自查** | `/usr/local/lib/python3.10/site-packages/xcloudsdk_py/capture.py` sha256 = **`1ac2310d5dc78df8227696fc40f9de9d3f5a580e76bbb5730290ce5813896e15`**（＝仓库版）；`image_sha256`/`duplicate_streak` 字段在；Pillow 12.2.0 + libjpeg_turbo |
| `FROM` | 已钉 **registry 地址**（不再依赖任何机器上的本地镜像） |

**现场生效步骤**（在离线包目录，**不带 `-f`**）：
`docker-compose.override.yml` 改成 `image: <上面的 mushroom_capture tag>`，
去掉 `PYTHONPATH: "/app/levels-fix/pylib"` 与两条 `./levels-fix/*` 挂载；
备份 → 上传并**核对远端 sha256 与本地一致** → `docker compose config` → `docker compose up -d --force-recreate`。

## C. 回滚路径（**动手前先落这里**）

宿主 `./levels-fix/` 目录**保留不删**（只摘挂载，不删文件）。回滚＝把覆盖层换回备份并重建：

```bash
cd /home/sysadmin/algorithm/mushroom_docker/xcloudsdk_py_offline_20260120_175307
ls -la docker-compose.override.yml.bak-*            # 备份已先行生成
cp docker-compose.override.yml.bak-<ts> docker-compose.override.yml
docker compose config >/dev/null                    # ⚠️ 不带 -f
docker compose up -d --force-recreate
```

回滚后应回到"厂商 `xcloudsdk-py:0.1.0` + 宿主 `levels-fix/` bind-mount"那一套
（`capture.py` 变回 `c65a1aa9…`，**新指纹字段会消失**——这正是回滚的判据）。

## D. 验收口径（Chief 2026-10-13 修订，**替换**原文的"连抓两次验 `same_as_previous: true`**"**）

**同字节只在冻结时才发生**，正常流里抓两次多半都是新帧 ⇒ `same_as_previous: true` **不是硬验收**
（那会得到一个"总是失败"的判据）。真正的重复检测能力已由 step code 的假 SDK 离线复现证明。
现场验收改为：

1. 响应里**字段存在且形状正确**：`image_sha256` 为 64 位十六进制、`same_as_previous` 为布尔、
   `duplicate_streak` 为整数、`play_data_ready` 有值；
2. 巡检侧 `meta` 里能看到这些字段**落进索引**；
3. **帧照常入库**、`n_frames_recorded` 等于帧数。

另：**服务侧实时指纹与巡检侧事后复核（`content_sha256`/`--verify-content`）互补，不许互相替代**；
`--verify-content` 默认关**维持到服务侧上生产之后**。

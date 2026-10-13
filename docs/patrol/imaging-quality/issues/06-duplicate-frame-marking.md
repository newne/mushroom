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

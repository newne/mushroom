# 07 用 PLAY_INFO 心跳缺失做流中断看门狗（票据 + 调研结论，**本轮不改代码**）

- 提出：**step code**（外部协作者）　执行：待 Chief 批后由 step code 实现；**反应语义归巡检手**
- 立项：2026-10-13　来源：发版官只读取证（`issues/05-frame-pool-tail-scan.md` §5 的续查）
- 状态：ready-for-agent（等批准；批准前不动一行代码）

## 0. 一句话

进程内**有一个现成的钩子**（`EUIMSG_PLAY_INFO = 30006` 本来就进 `_on_message`），但
**没有任何证据表明它以稳定速率到达应用层**——现场恰恰否证了这点。所以本票据的结论是：
**先做仪表化（数消息），再定阈值**；在看门狗生效之前，`image_sha256` 那套已经能覆盖
本次事故的 34/35 帧，剩下第 1 帧由看门狗补。

## 1. 已确认的事实（代码路径，行号可查）

| # | 事实 | 位置 |
| --- | --- | --- |
| 1 | `EUIMSG_PLAY_INFO = 30006`，与 `END_BUFFER_DATA(30004)`/`YUV_DATA(30010)`/`DATA_FRAME(103)` 一起，**每一条都会进 `_on_message`** | `sdk.py:18`、`sdk.py:334-342` |
| 2 | 但这四个消息**只在有人等的时候被转发**：`waiter = self._playdata_wait.get(hObject); if waiter is not None and not waiter.event.is_set()` —— 没人等就**直接丢掉** | `sdk.py:336-337` |
| 3 | `_pool_capture_locked` 连 `wait_play_data` 的返回值都不接（票 06 已补报 `play_data_ready`，但仍不据此判任何事） | `capture.py` 连接池路径 |
| 4 | 现场否证：**健康抓拍的 `play_data_ready` 也是 false**，`capture_time_ms` 5200/5202 等满 5s 超时 ⇒ 稳态抓拍窗口内，这四个消息**一个都没到** | 发版官实测 |
| 5 | SDK 自己写日志（`XCloudSDK_SetLogTypeAndLevel(log_type=2, log_level=1)`），容器内 `/app/log` 已被 bind-mount（entrypoint `mkdir -p /app/log`，compose 有 `…/log -> /app/log rw=true`） | `sdk.py:87`、`docker/entrypoint.sh:14` |
| 6 | SDK 日志里有这次事故的完整时间线：`notifyPlayInfo` 心跳、`HeartBeat-Timeout`、`device disconnect result -99991`、`Login success` | 发版官摘录 |

## 2. 五点结论

### 2.1 进程内有没有比 tail 日志更稳的信号？各自把握多大

**有钩子，但把握不足，不能直接当看门狗用。**

- **`EUIMSG_PLAY_INFO` 计数（改 `_on_message`，在 waiter 判断之外加一个计数器）**
  把握：**低–中，且未验证**。事实 1 说明消息确实进进程；事实 4 说明稳态下 5 秒窗口内
  收不到任何一条——两种解释都成立：(a) PLAY_INFO 是低频信息 tick（秒级以下/随机），
  5s 窗口常常错过；(b) 应用回调只收到消息的一个子集。**在数到真实速率之前，任何阈值
  都是猜。** 成本几乎为零（一次自增），所以它是"第一优先做仪表化"的对象，不是
  "第一优先做看门狗"的对象。
- **`EUIMSG_PLAY_SAVE_IMAGE_FILE`（30007）**：每张截图触发一次，现在被回调直接忽略
  （`sdk.py:344-346`）。把握：**高**（它必然随截图到达），但它只证明"拍完了一张"，
  **不证明这张是新的**——单用没有价值，可作为"截图事件"时间戳的廉价来源。
- **主动探询 `XCloudSDK_Device_GetDevState`**（.so 里确实导出了这个符号）：把握：
  **无法评估**——仓库与厂商包里都没有结构体/头文件，绑不出正确调用。要等厂商给 ABI。
- **`wait_play_data` 复用**：把握：**零**。现场已否证（事实 4），它只报不用。

⇒ **建议的第一版不是看门狗，是仪表化**：给 `_on_message` 加按 `msg_id` 的计数器 +
最近一次各类消息的时间戳，通过 `/healthz`（或抓拍响应）暴露。跑一轮，拿到 PLAY_INFO
的**真实速率**，再回来定阈值。想一次到位的话，最小诊断动作是：**单次抓拍把
`wait_play_data` 的 timeout 从 5s 提到 60s**（一次性排障，不进常规轮次），
就能测出心跳周期——这是不碰生产、不改逻辑的最便宜实验。

### 2.2 若只能读宿主 SDK 日志

| 项 | 现状 | 必须现场确认的 |
| --- | --- | --- |
| 路径 | **推定** `/app/log`（entrypoint mkdir + compose 挂载都在），**具体文件名未知**——由宿主 `XCloudSDKTest_config.ini` 决定，而该文件被 gitignore、不在仓库 | 发版官一条 `ls -la /app/log` + 抓 20 行样例 |
| 轮转 | **未知**（厂商策略） | 是否按大小/按天轮转、是否 compress、旧文件是否删 |
| 时区 | **未知**。证据里的时间戳（`18:22:13.952`）不带时区；capture-fix 覆盖层把容器 TZ 设成 Asia/Shanghai，若 SDK 用 localtime 则为 CST | 与服务自身时钟对拍一次，差值写进代码注释 |
| 格式 | 自由文本行（`notifyPlayInfo …` / `HeartBeat-Timeout` / `device disconnect result -99991` / `Login success`） | 正则的锚点；**厂商内部格式，换 SDK/固件版本可能变** |
| 解析代价 | tail-follow + 少量正则；按证据推断 ≤ 数行/秒，一个常驻 fd 增量读，**每轮 IO 为 KB 级，CPU 可忽略** | 实测行速率 |

**读不到/解析失败时的行为（硬要求）**：一律 **fail-open 成 `unknown`**——
响应里 `stream_state: "unknown"`，**绝不允许**因为读不到日志就报 `alive`。
下游必须把 `unknown` 当"没判"处理，不许当健康。轮转要处理"文件被替换/truncate"
（inode 变化或 size 变小就重开）。

**日志方案的价值**：SDK 自己的检测滞后了 22 秒（最后心跳 18:22:13.952 →
`HeartBeat-Timeout` 18:22:36.106），应用侧看到 disconnect 更晚（18:23:10.051）。
自己算"距上次心跳多久"可以比 SDK 的内置超时**更早**发现，且能自己记录
**故障时间窗**（这次是发版官人工拼出来的）。

### 2.3 阈值与最小状态机

**阈值**：健康心跳周期**未知**（仓库内无数据）；唯一实测数是 SDK 自己的超时 ≈ 22s。
建议：可配置，默认 **`T = clamp(3 × 实测健康周期, 30s, 60s)`**，在仪表化数据出来前
**先按 30s 跑观察模式（只标记、不反应）**。

- 下限 30s 的理由：一轮约 450s、单站约 9s；阈值太低会把"慢但活着"打成死。
- 上限 60s 的理由：再大就意味着最多 60s 的冻结帧才被标记——可接受，因为这期间
  `same_as_previous` 已经在标记第 2..N 帧了（见 2.4）。

**最小状态机**（按设备 / play handle）：

```
                心跳恢复               超过 T 无心跳              超过 2T 或无心跳
   UNKNOWN ───────────────► ALIVE ───────────────► SUSPECT ───────────────► DEAD
      │                        │                      │                      │
      └── 信号源不可用           └── 心跳恢复 ──────────┘                      │
         （fail-open，           （记录 outage 窗口：起/止/时长）◄─────────────┘
          绝不当 ALIVE）
```

- `SUSPECT`：开始给**新抓的帧**打 `stream_suspect`；不拦、不重拍。
- `DEAD`：打 `stream_dead`；**是否中止轮次 / 是否重拍，归巡检手定，不在本票据**。
- 任何 → `ALIVE` 时记录一次 outage 窗口（start/end/duration）——这正是这次要靠人
  手工拼的 10 分 20 秒。

### 2.4 与 `image_sha256` / `same_as_previous` / `duplicate_streak` 的分工

| 信号 | 能覆盖 | 覆盖不了 |
| --- | --- | --- |
| `image_sha256` + `same_as_previous` + `duplicate_streak` | **第 2..N 帧**冻结（内容复制）；给出"这 35 帧是同一张"的硬证据；不依赖任何外部时钟或日志 | **冻结的第一帧**（没有同字节前驱）；冻结只影响**一帧**（两站之间断一下又恢复）时也漏；分不清"冻结"与"极端静态场景"（传感器噪声让后者几乎不可能，但不是零） |
| 心跳看门狗 | **第一帧就冻**也能标（心跳在拍照前已停）；给出**原因与时间窗**（设备掉线 10 分 20 秒）；覆盖"冻结但内容不是黑帧" | 心跳信号若不可达则整体失效（未验证）；相机在送流但画面被编码器卡住时，心跳可能仍然正常 |
| `measure.quality` 的 `no_light` | 冻结帧恰好近黑时（**本次事故正是**，p99=2.0） | 冻结在白帧/正常帧上时三判据全绿 |

⇒ **三者互补，不互相替代**：心跳解释"为什么"，sha 证明"是同一张"，质量门判
"这一帧有没有信息量"。本次事故里 `no_light` 其实已能拦，但它拦的是"这帧没信息"，
不是"这帧不是当站的"——后者只有 sha + 心跳能给。

### 2.5 成本与"不加 service"

- **进程内计数器**：`_on_message` 里一次自增 + 一个 dict 查询；CPU/IO ≈ 0。
- **日志 tail**：一个常驻 fd + 增量读 + 每次一个 `stat()` 判轮转；每轮 IO 为 KB 级。
- **不加新端口、不加新 compose service、不加新挂载**（用户口径明确禁止）：
  看门狗**必须活在现有采图服务进程里**（就是已经在 serve :7003 的那个），
  日志方案读的是**已经挂好的** `/app/log`。任何 sidecar / 新容器都是新 service，不做。
- 若最终要暴露状态：**扩展现有 `/healthz`** 或往抓拍响应里加字段，不开新端点也行
  （开一个 `/stream_state` 也不违反"不加 service"，但不必要）。

## 3. 接口（只定信号，不定反应）

抓拍响应（两条路径都加，同 `image_sha256` 的做法）：

```json
"stream_state": "alive | suspect | dead | unknown",
"stream_last_heartbeat_age_s": 12.4,
"stream_outage": {"start": "…", "end": "…", "duration_s": 620}
```

- `unknown` 是**一等状态**，不是错误；看不到信号源时就给 `unknown`。
- 拿到信号后**怎么标轮、是否重拍、是否算失败**，全部归巡检手（票 06 的 A/B 取舍
  在这里同样适用，且要先知道看门狗的误报率才能定）。

## 4. 明确不做（本轮 + 本票据范围外）

- **本轮不改代码**（等批）。批准后第一版也只做仪表化 + 观察模式，不接反应。
- 不碰生产（含只读）；日志路径/轮转/时区由发版官现场确认后回填本票据。
- 不新增 service / 端口 / 挂载；不改 compose、不动 `docker/**`。
- 不改 `wait_play_data` 的常规超时（5s 是抓拍节拍的一部分）；诊断性长超时只作为
  一次性排障脚本，不进轮次。

---

# 5. 现场只读回填（发版官，2026-10-13 21:0x）——三处"未知"的实测答案

每条都标了**实测**还是**推定**；实测有可复算的命令与数字。

## 5.1 文件：`/app/log` 里到底有哪些（**实测**）

容器内 `docker exec <capture> ls -l /app/log`（宿主侧同目录 `.../xcloudsdk_py_offline_20260120_175307/log`，
bind-mount rw，内容一致）：

| 文件 | 大小/时间（实测） | 说明 |
| --- | --- | --- |
| `xcloudsdk.log` + `.1` `.2` `.3` | current 9 279 584 B；历史 3 个 ≈10 MiB | SDK 主日志（`device_access_manager` / `NetDevice` / `SubSystem` / **`HeartBeat-Timeout`** / **`Login success`** 都在这一族） |
| `mediacodec.log` + `.1` `.2` `.3` | current 7 706 189 B；历史 3 个 ≈10 MiB | **心跳就在这一族**：`play_msg_pusher.cpp:95 notifyPlayInfo`，带 `relativeTime`——**看门狗要 tail 的首选** |
| `app_log.log` | 3 791 B，mtime **＝本次重建时刻**（20:18） | 每次容器启动**重建**，生命周期＝容器 |
| `xvfb.log` | **0 B**，mtime ＝重建时刻 | entrypoint `Xvfb … >/app/log/xvfb.log` 每次启动**截断** |

**⚠️ 更正 §2.2 的一处推定**：票里写"具体文件名由宿主 `XCloudSDKTest_config.ini` 决定"——**实测否证**。
该 config 全文是
`{"CFG_KEY_Language":"cn","ConfigPath":"./","InitType":0,"Language":"cn","LoginTimeout":15000,"LoginType":100,"PlatAppKey":"","PlatAppSecret":"","PlatMovedCard":0,"PlatUUID":"","SaveFileType":"mp4","ServerIP":"","ServerPort":34567,"TempPath":"./"}`
——**没有任何日志文件名/级别/大小项**。entrypoint 也只 `mkdir -p /app/log`（第 34 行）与写 `xvfb.log`（第 106 行）。
⇒ 日志名与轮转是**厂商 SDK `.so` 内置**，**读配置读不出来**，只能实测。

## 5.2 轮转策略（**实测 4 条 + 推定 1 条**）

**实测**：

1. **按大小轮转，阈值 ≈ 10 MiB**：6 个历史文件大小全落在 **10 485 568–10 485 697 B**（10 MiB = 10 485 760 B），
   即刚过 10 MiB 就切（数字见 `ls -l` 输出）。
2. **每流 4 个文件**（current + `.1`/`.2`/`.3`）⇒ **按数量保留 3 个历史**，每流上限 ≈ 40 MiB。
3. **不压缩**：全是纯文本 `.log`，没有 `.gz`。
4. **经验保留 ≈ 6 天**：`xcloudsdk.3` Oct 8 09:01 → `.2` Oct 10 04:00 → `.1` Oct 12 05:02 → current Oct 13 21:04，
   约 2 天/10 MiB。⚠️ **这是由产生速率决定的，不是"按天保留"**——轮次密度一变，保留时长跟着变。

**推定（未实测到一次轮转，标推定）**：轮转方式最可能是 **rename**（`current→.1`、`.1→.2`、`.2→.3`、`.3` 删除），
因此 **current 的 inode 会变**；但**不能排除 copy-truncate**（同 inode、size 归零）。
⇒ 票里"inode 变化或 size 变小就重开"这条要求**必须照做**，两种都要处理。
**补一条**：`app_log.log` / `xvfb.log` 会在**容器重启**时归零/重建，tail 逻辑别把它们当 `xcloudsdk/mediacodec` 那一族的续写。

## 5.3 时间戳时区（**实测**）：`18:22:13.952` 是 **CST（Asia/Shanghai，UTC+8）**，不是 UTC

三条实测证据：

1. 容器内 `date` → `Tue Oct 13 21:05:03 **CST** 2026`；容器 env **`TZ=Asia/Shanghai`**（compose 覆盖层设的）。
2. **与服务自身时钟对拍**（最硬的一条）：docker 的访问日志是 UTC，S101 那次 `pool_capture` 响应完成于
   `10:20:14.232Z`；SDK 日志里**同一次抓拍**的 `CatchPicture` 在 `18:20:13.892` ⇒
   18:20:13.892 CST ＝ 10:20:13.892Z，比响应早 0.34 s，**差正好 8 小时**。
3. 事故时段一致：`HeartBeat-Timeout` 记 `18:22:36.106`，同一时段 docker 侧请求在 `10:22:23Z`/`10:22:32Z` 的节奏上。

⇒ `age = now(容器时钟) − 最后一条心跳的时间戳` **可以直接相减**（同源同时区）。
补两条：**Asia/Shanghai 无 DST**（不存在跳变）；解析仍**不要假设时区**——用容器 `time.time()`
与日志时间戳同源比较，别引入 UTC 假设。

## 5.4 给实现的三个具体锚点（供 step code）

- tail 目标：`/app/log/mediacodec*.log`，锚点字符串 **`notifyPlayInfo`**；行首格式 `[YYYY-MM-DD HH:MM:SS.mmm]`。
- 心跳"活着"的判断建议看 **`relativeTime` 是否在推进**，而不是只看行数（事故前它每秒 +1000，实测）。
- `xcloudsdk*.log` 里 `HeartBeat-Timeout`(18:22:36.106) → `device disconnect, result: -99991`(18:23:10.051) 是
  **SDK 自己的**判定，比真实停流晚 **22 秒**——只作旁证，别当第一信号。

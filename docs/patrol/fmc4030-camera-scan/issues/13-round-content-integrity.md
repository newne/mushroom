# 13 — 采集可信度：`ok=1` 不证明"内容属于这一站"（同轮字节重复的取证与标记）

**归属：** 巡检手牵头（`ok` 语义属 `round`/`daemon`/`journal` 的地界）；采图侧取证 = **step code**
（`capture_client.py`/`third_party/capture/**`）；统计排除 = **测量师 + 环境官**；
全池只读重扫 = **发版官**。

**Status:** 取证完成 + 标记已落地；等 step code 的采图侧结论与发版官的全池 sha 重扫。

**依据：** `docs/patrol/prod-deploy/issues/05-frame-pool-tail-scan.md` §5（发版官 2026-10-13 只读实测）；
`.scratch/frame-quality-samples/tail/all-metrics.csv`（3147 帧度量，只读）。

---

## 1. 事件

- 2026-10-09 **18:20:05–18:27:16** 一轮 48 帧里，**35 帧 sha256 完全相同**
  （92364 B、2880×1616、`mean` 2.31、`p99` 2.0），横跨 S211→S401，历时约 5 分 11 秒；
- 而该轮 **round 行写着 `ok=1` / 48 帧 / 0 失败 / 0 中止**；
- 对比 2026-10-08 的近黑帧**各站字节互不相同**（89837 / 90493 / 92060 / 112448）
  ⇒ 那是各站独立的**真欠曝**；10-09 是**同一份字节被复制**。**两者性质不同，不混为一类。**

## 2. 本票新增的取证（全池同轮重复扫描，只读）

方法：读发版官的 3147 帧度量 CSV，按 ts 间隙 15 分钟切轮窗口，用
`(bytes, lapvar, p99, dark_frac, mean_luma)` 当**指纹代理**（CSV 没有 sha256），
统计每个窗口内"同一指纹"的最大组。

| 结论 | 值 |
| --- | --- |
| 切出轮窗口 | **76 个** |
| 可疑轮（同指纹 ≥5 帧） | **只有 1 轮**：`2026-10-09 18:20:05–18:27:16` |
| 该窗口 | 48 帧，最大同指纹组 **35 帧**（bytes 92364、lapvar 46.84、p99 2.0、mean 2.3） |
| 其余 75 个窗口 | 最大同指纹组 **= 1 帧**（无同型轮次） |

**修正一处既有表述（重要）**：不是"整轮同一张"。逐帧看这 48 帧——

- **前 13 帧是正常帧**：S101…S112 + S212，bytes 548 K–792 K、`lapvar` 60.85–85.58、
  `p99` 210–247、`mean` 74–120；
- **从第 14 帧 S211（18:22:05）起**（`S211→S201`、`S301→S312`、`S412→S401`）**全部是同一份陈旧近黑帧**（35 帧）。

⇒ 失效的**起始点在中轮的 S211**；"从头到尾一张"不成立。遍历序是蛇形
（`S101→S112` → `S212→S201` → `S301→S312` → `S412→S401`），**失效起点正好是第 2 层反向的第一个站**。
发版官已按此更正其票 05 §5（提交 `347f8da`）。

复跑（只读、不连生产）：`python .scratch/dup-scan.py`（读上面那份 CSV）；脚本落在 `.scratch/`。

### 2.1 生产原文（发版官只读取回，2026-10-13）

| 问题 | 答案 |
| --- | --- |
| round 行 | `ok=1` / `n_results=48` / `n_failures=0` / `aborted=0` —— **该轮自认全成功**；全部 67 轮里只有 5 轮报过失败，**这轮不在其中** |
| 48 站是否都记成功心跳 | **是，全记了**。run 原文 `/home/sysadmin/algorithm/mushroom_patrol/data/runs/20261009-182002-1.jsonl`（50 行 = start + 48 station + end），48 条 `event=station` **全部 `ok:true`、`error:null`、各有自己的 `object_name`**；末行 `{"status":"ok","n_results":48,"n_failures":0,"aborted":false}` |
| **耗时**（关键新证据） | 那 35 站 `elapsed_s` = **9.08–9.13 s**，与正常 13 站（8.24–10.05 s）**没有区别** ⇒ **不是跳过取流**：照常走完、耗时完全正常，却写进同一份陈旧帧 |
| 全池真 sha256 查重 | **做完了**（见 §2.2） |
| ⚠️ 日志通路 | `mushroom_patrol` 今日 14:48 被重建（json-file 驱动 + 换容器）⇒ 10-09 的 `docker logs` **取不回来**；`/opt/mushroom-patrol/runs` 只剩 09-13 三份。**run jsonl 是唯一原文** |

### 2.2 全池真 sha256 查重（发版官，只读）

方法：**字节相同 ⇒ 度量必然相同**，所以重复对必然落在同一个度量元组组里 ——
先用元组全局分组（本地零成本）挑候选，**只对候选补下字节算 sha256 落槌**（没有重下 3147 帧）。

- 元组 `size ≥ 2` 的组：**全池只有 1 个**（就是那 35 帧）；
- 补下后**不同 sha256 数 = 1**（`41685c09be7b5c63…`、92364 B）；
- **跨轮/跨天重复 0**、**假阳性 0**。

风险方向要记清：不是"字节相同而度量不同"（数学上不会发生），而是**度量四舍五入后不同帧可能撞车**
⇒ 所以 sha256 落槌是必要的（本次体检假阳性 0）。**结论：全池只有这一处同轮复制，没有同型轮次。**

**对实现的要求（结论）**：`frame_dup`/`dup_group` **必须按跨站内容指纹判**，
**不能依赖 `ok`、也不能依赖 `elapsed_s`**——这轮两个指标都完全正常。

## 3. `ok` 语义收紧（设计 + 已实现）

**现状缺陷**：`ok=1` 只证明"这一站取到了文件"，**证不了"取到的是它自己那份"**。

**做法：留帧 + 标记 + 原因，绝不静默丢。**

| 层 | 字段 | 含义 |
| --- | --- | --- |
| 帧行 | `content_sha256` | 复核到就写（部署侧取回 `cloud_url` 算 sha256） |
| 帧行 | `frame_dup: true` + `dup_group: <sha12>` | 同一指纹出现在 **≥2 个不同站位**（同站位重试同一张不算） |
| 轮行 | `suspect_duplicate: true` + `dup_groups: [{sha256_12, n, stations}]` | 本轮有跨站复制 |
| 轮行 | `content_check_error` | 复核**没查成**（取图失败等）——**不谎报"没有重复"** |
| journal | `{"event": "content_duplicate", "groups": …}` | 取证文件里也留一笔 |

实现落点：

- `patrol/src/patrol/daemon.py`：`PatrolDaemon(content_hash=…)` + `_check_content()`
  —— 在**写索引之前**跑，标记才跟着帧走；`content_hash=None`（默认）= 不查、不加字段，
  行为与从前完全一致。
- `deploy/src/deploy/content_verify.py`：`make_content_hash(fetch=…)` —— 取回 `cloud_url` 算 sha256；
  **取图失败上抛**（由 daemon 记 `content_check_error`），不把失败伪装成"没有重复"。
- `deploy/src/deploy/m1.py`：`--verify-content`（**默认关**）。代价 ≈34 MB/轮
  （48 × ≈700 KB），所以是显式开关。

**排除规则（跨域，必须落地）**：带 `frame_dup` 的帧与 `suspect_duplicate` 的轮
**不得进入长势与质量统计**（区间表、生长曲线、阈值标定样本）。归属：测量师（`measure/**`）
与环境官（analysis 聚合）各开一张票；我这边只负责把标记打准、把帧留住。

## 4. 证据（命令 + 结果）

```bash
cd D:\code\mushroom\patrol-workspace
.\.venv\Scripts\python.exe -m pytest -q ../patrol/tests/test_daemon_sync.py \
  ../deploy/tests/test_content_verify.py ../deploy/tests/test_m1.py
# → 43 passed
```

- `patrol/tests/test_daemon_sync.py`：三例 —— 同指纹跨站 ⇒ 帧 `frame_dup` + 轮
  `suspect_duplicate` 且**帧数不减少**；复核抛错 ⇒ 轮 `content_check_error` 且不假装无重复；
  不查 ⇒ 行里没有多余字段。
- `deploy/tests/test_content_verify.py`：sha256 取自真实取回字节；无 `cloud_url` ⇒ 不取图；
  取图失败上抛；同一份字节 ⇒ 同一指纹。

## 5. 需要别人做（跨域，已按归属分派）

1. ✅ **发版官（只读）已完成**：① 该轮 journal/round 原文已取回（见 §2.1：48 站全 `ok:true`、
   `n_results=48`、`n_failures=0`，**`elapsed_s` 与正常站无差别**）；② 全池**真 sha256** 重扫完成
   （见 §2.2：只有这一处同轮复制，跨轮 0、假阳性 0）。**只剩一条要他注意的**：那轮 `docker logs`
   已随容器重建丢失，run jsonl 是唯一原文（已在票 05 §5 记）。
2. **step code**：什么条件下会返回**同一帧**（连接池复用 / `MakeKeyFrame` / 等帧逻辑 /
   子码流回退），给票据。给他的最强线索：**耗时正常（9.08–9.13 s）+ `ok=true` + 各有自己的
   `object_name`**，却交付同一份字节 ⇒ 不是"取不到流"，而是"取到了却不属于这一站"。
3. **测量师 + 环境官**：按 §3 的标记排除；标定样本也要排除（那轮 35 帧全在同一指纹下）。
   测量师已落地（`FrameInput` 透字段 + 可疑帧默认不产行 + 样本侧 `sample_hygiene`/`KNOWN_BAD_ROUNDS`）。
4. **我**：标记已落；等 step code 的采图侧结论再定策略（当场重拍、重建连接、或把可疑轮判 `partial`）。
   ⚠️ 另有跨域阻塞：环境官的 `rounds.dup_groups` 是 TEXT 列而 ingest 原样绑定**列表** ⇒
   `sqlite3.ProgrammingError: type 'list' is not supported`（`analysis/src/analysis/api.py:139-146`、
   `db.py:507`）；修法见与该票配套的沟通（建议他侧对非标量做 `json.dumps`）。

## Comments

- 2026-10-13 巡检手：立项。取证来自发版官只读实测（票 05 §5）+ 我对 3147 帧 CSV 的只读重扫；
  代码与测试随本票一起提交。**未连生产、未改历史数据**。
- 本票**修正**了"整轮抓到同一张"这个读法：正确说法是"**从第 14 帧（S211）起**开始复制"，
  前 13 帧（S101–S112、S212）是正常帧。
- 2026-10-13 巡检手：**真因由 step code 挖出并已在服务侧实现**（`4196b66`，
  票 `imaging-quality/issues/06-duplicate-frame-marking.md`）：`_pool_capture_locked`
  丢了 `wait_play_data` 的返回值，而周期性的 `PLAY_INFO` 就能点亮它 ⇒ 返回 True
  **不证明新帧到达**；底层视频冻结时 `MediaSnapImageEx` 把解码器里那一帧**反复写出来**，
  文件就绪、上传成功、`success=True`，**客户端看不出来**（站位名各不相同、巡逻侧库内零网络调用）。
  离线复现与线上形状一致（前 14 帧正常、之后同字节、`duplicate_streak` 涨到 35、48 站全 success）。
  **两套指纹互补**：服务侧实时指纹（当场发现）+ 我这边事后复核（发现服务侧没报的）。

### §3.1 轮次语义落地（Chief 2026-10-13 裁：票 06 的 B 方案 + 单独计数）

| 项 | 实现 |
| --- | --- |
| 服务侧字段进元数据 | `orchestrator.service_integrity()`：`image_sha256` → **`content_sha256`**（与事后重算同义，**一个字段名**，不造第二套）；`same_as_previous` / `duplicate_streak` 原样带出；`same_as_previous` 为真时直接标 `frame_dup` + `dup_group` |
| 手动抓拍 | `manual_exec` 同样带这三个字段，`same_as_previous` 为真时**当场打警告**（仍入库） |
| `n_results` 语义 | = **成功取图** = 帧数 − 重复帧数 ⇒ `ok=1` 不再意味着"48 站都拍到了自己的图" |
| 单独计数 | 轮行加 `n_duplicate` 与 `n_frames_recorded`（账说全，不藏东西） |
| 不撞失败计数 | 重复帧**不**计入 `MAX_CONSECUTIVE_FAILURES`、**不中止**整轮（重复不是 `CaptureError`） |
| 帧照旧入库 | 48 帧全落索引（留档 + 标记），由下游按 `frame_dup`/`suspect_duplicate` 排除 |
| 判据 | 同一指纹出现在 **≥2 个不同站位**，或服务侧明说 `same_as_previous` |
| 事后复核 | `--verify-content` 保留（默认关）；两组指纹合并成同一份 `dup_groups` |

**验收回放**：`patrol/tests/test_daemon_sync.py::test_daemon_replays_the_1009_round_shape`
——48 站、前 13 不同、之后 35 同字节 ⇒ `n_results=13`、`n_duplicate=35`、`n_frames_recorded=48`、
`suspect_duplicate=true`、`n_failures=0`、**未中止**、48 帧全部入库（35 帧带 `frame_dup`）。

### §3.3 `play_data_ready` **不是**"流死"信号（发版官现场实测更正，2026-10-13）

发版官在 C1 镜像上生产后连抓两次**健康**帧：

```
{"image_sha256":"d532c628…","same_as_previous":false,"duplicate_streak":1,
 "play_data_ready":false,"capture_time_ms":5202}
{"image_sha256":"86199de7…","same_as_previous":false,"duplicate_streak":1,
 "play_data_ready":false,"capture_time_ms":5200}
```

⇒ **健康抓拍时 `play_data_ready` 也是 false**（`capture_time_ms≈5200` ⇒ `wait_play_data`
每次都在 5 s 上限超时，随后照样抓到好帧；`third_party/capture/.../capture.py:411` 取到、
`:462` 只报不用）。**在 `/pool_capture` 池化路径上它不是"流死"信号**，拿它做判据会每次
正常抓拍都误报。

**结论（照此实现，已核对）**：轮次语义只用三件套
`image_sha256` / `same_as_previous` / `duplicate_streak`；
**`play_data_ready` 不进轮次语义、不进看门狗**（我这边也没把它写进索引行，避免有人当信号用）。

**另供参考**：本次取证显示"周期性 `PLAY_INFO` 心跳缺失"比 SDK 自己的 `HeartBeat-Timeout`
**早 22 秒**（18:22:13.952 停数据 vs 18:22:36.106 记超时）——将来要做"流中断"检测，
那条信号比 `play_data_ready` 靠谱。证据与更正见发版官票 06 §F（提交 `74e4371`）。

### §3.2 需要 analysis 补的列（跨域，环境官）

已有的列（`frame_dup`/`dup_group`/`content_sha256`/`suspect_duplicate`/`dup_groups`/
`content_check_error`）够用；§3.1 新增的**三个审计字段**还没有列，现在发出去会被
`IMAGE_FIELDS`/`ROUND_FIELDS` 静默忽略（不会报错，但库里看不到）：

| 表 | 字段 | 含义 |
| --- | --- | --- |
| `images` | `same_as_previous` | 服务侧当场说"与上一张同字节" |
| `images` | `duplicate_streak` | 连续第几份相同（事故那轮涨到 35） |
| `rounds` | `n_duplicate` / `n_frames_recorded` | 单独计数：重复帧数 / 实际写盘帧数 |

另外**一个阻塞仍未解**：`rounds.dup_groups` 是 TEXT 列而 ingest 原样绑定**列表** ⇒
`sqlite3.ProgrammingError: type 'list' is not supported`（`analysis/src/analysis/api.py:139-146`、
`db.py:507`）；建议对该列做 `json.dumps`。

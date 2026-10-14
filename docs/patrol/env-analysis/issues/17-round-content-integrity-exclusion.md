# 17 — 轮内容完整性：复制帧/可疑轮不进聚合统计（票 13 的 analysis 侧落地）

**Spec:** `fmc4030-camera-scan/issues/13-round-content-integrity.md`；../spec.md §5（区间表）、§2.7（长势）

**归属：** 环境官（`analysis/**`）；帧/轮侧的标记由巡检手产生（`6a232e4`）。

**Status:** resolved（排除规则 + 列已落地；`/ranges`、`/growth` 的**计数暴露**待随各自改造一起上）

**What to build:** 让"复制帧"进不了任何聚合统计 —— 它们会让长势曲线看起来**正常**
（2026-10-09 18:20–18:27 那轮 48 帧里 35 帧字节相同，轮行却写 `ok=1 / 0 失败`），
这种错误比缺数据难发现得多。

## 1. 列（先落列，再由巡检手发字段）

| 表 | 列 | 由谁产生 |
| --- | --- | --- |
| `images` | `frame_dup`、`dup_group`、`content_sha256`、`same_as_previous`、`duplicate_streak`、`elapsed_s` | 巡检手 |
| `rounds` | `suspect_duplicate`、`dup_groups`、`content_check_error`、`n_duplicate`、`n_frames_recorded` | 巡检手 |

`ADDED_COLUMNS` 就地迁移、老行 NULL（NULL = "那时还没查"，**不是"已确认无重复"**）。
`IMAGE_FIELDS`/`ROUND_FIELDS` 已带上，`/ingest` 原样透传（提交见 Comments）。

**`dup_groups` 是对象数组 ⇒ 存 JSON 文本**（**更正 2026-10-14，以生产者为准确认**）：
`rounds.dup_groups` 是 TEXT 列，生产者 `patrol/src/patrol/daemon.py:344` 发的是

```
[{"sha256_12": "<12 位>", "n": <帧数>, "stations": ["S211", …]}, …]   无重复时 NULL 或 []
```

直接绑给 sqlite3 会抛 `ProgrammingError: type 'list' is not supported`
（`insert_rounds` 的 `_as_text` 序列化、`fetch_rounds` 的 `_as_list` **成对**解回）——
只写不读回来等于没这个字段。**元素是对象，不是字符串**；`suspect_duplicate` 与
`frame_dup` 生产者发的是**布尔 `True`**，库里落 0/1（INTEGER）。
回归：`test_dup_groups_round_trips_as_objects_not_strings`（夹具用真形状含 `n`/`stations` ——
早先用 `["a1b2c3d4"]` 会把"元素是字符串"的错误印象固化成契约）。

## 1.5 护栏：没有对应列的键**必须**被点名

`/ingest` 现在按 kind 检查键是否在 `*_FIELDS` 里，表外的键进响应
`warnings`（并打一行日志）："`image_index.brand_new_field 没有对应列，会被忽略`"。
**静默忽略是这类错误唯一的藏身处** —— 巡检手票 13 §3.2 那四个字段就是这样被吞过一次，
发送方以为已经落库。测试：`test_unknown_key_is_reported_not_swallowed`、
`test_clean_batch_has_no_warnings`。

顺带补了 `images.elapsed_s`：生产者一直在发（`elapsed_s`），表里原先没有 ⇒ 被静默丢。
按"写入方多给、存储方少要"的教训**加列而不是继续吞掉**；`yz`（坐标列表，由
`flatten_image` 摊平成 y/z）在检查里显式认作已知键。

## 2. 排除规则（`analysis/src/analysis/integrity.py`）

| 标记 | 处置 |
| --- | --- |
| `images.frame_dup`（生产者布尔 `True` ⇒ 库里 0/1） | 该帧不进统计 |
| `rounds.suspect_duplicate`（生产者布尔 `True`） | **整轮**的帧不进统计 |
| `rounds.content_check_error` 非空 | **"没查成"≠"已确认无重复"** ⇒ 同样排除，并记原因 |

**`duplicate_streak` 的语义（更正 2026-10-14，界面师真机实测 + Chief 裁定）：
正常帧就是 `1`**（"连续 1 张"＝它自己；真实正常帧 48/48 全是 1）。所以

- **帧级不可信只判 `frame_dup=1`**；
- `duplicate_streak` **只作展示/取证**；任何需要它的判据**必须 `> 1`**，
  写 `> 0` 会把每一帧都判成不可信（全站天天报警，真报警被淹掉）。

口径已钉进 `docs/agents/bot-roles.md`（`4ad40b3`）当全队唯一出处。
**analysis 侧没有误用**：`integrity.py` 的排除判定只读 `frame_dup`/`suspect_duplicate`/
`content_check_error`，不读 `duplicate_streak` —— 这次只是票面文字更正，统计结果未受影响。

（`dup_groups` 只作**取证/展示**用：排除判定看 `suspect_duplicate`/`content_check_error`
这两个标量，**不靠解析对象数组** —— 阵列形状变了也不该让排除规则失效。）

**为什么按"轮窗口"排**：测量行只有 `(ts, room_id, box_id)`，**没有 `round_id`**
（票 11 的线格式里没有），逐帧对不上。于是取
`[round.ts, 同库房下一条 round.ts)`；没有后继轮次时上界封顶
`round.ts + SUSPECT_MAX_SPAN_H`（1 小时）——**不让一个可疑轮把该库房之后的数据永久排除**。

**不静默丢**：`partition()` 返回 `Dropped` 明细（ts/zone/reason/**detail**/window），
调用方要把它记进响应或日志。

## 2.5 显式排除清单（legacy 轮：没有标记列，只能点名排）

`analysis/integrity.py` 里的 `LEGACY_EXCLUSIONS`：**只增不改**的清单，每条带
`round_id` **和/或** `(room_id, ts_from, ts_to)` 窗口 + 一行原因 + **批准人**。
**第一条 = 2026-10-09 那一轮**。

**两个键，优先级写死**（Chief 2026-10-14）：**`round_id` 优先命中**；`round_id` 为空、
或那一代的**帧行**没有 `round_id` 时，按**时间窗**命中。命中的行 `Dropped.detail`
带上"**哪条规则命中 + 谁批的 + 为什么**"。

**第一条的证据（原文，发版官只读导出 `.scratch/deploy-acceptance/round-accident-12038e7a.json`）**：

```json
{"ts": "2026-10-09T18:27:34", "round_id": "12038e7ac0334a8bbc1e178793ed5c09",
 "ok": 1, "n_results": 48, "n_failures": 0, "aborted": 0,
 "room_id": "611", "entry_date": "2026-09-30", "batch_no": "mogu-107",
 "suspect_duplicate": null, "dup_groups": null, "content_check_error": null,
 "n_duplicate": null, "n_frames_recorded": null, "source": "prod"}
```

⇒ **轮行的 `round_id` 不是 NULL**（`frame-sample-extract.py:38` 说的那一代大概指**帧行**）。
`frame-sample-extract.py:38` 那句**只对了一半**（发版官 2026-10-14 实测）：`round_id IS NULL` 的
**最大** `ts` = `2026-10-09T09:27:36`，非 NULL 的**最小** `ts` = `2026-10-09T12:27:43` ——
即 10-09 当天 00:27/03:27/06:27/09:27 四轮为 NULL、12:27 起有值。**两键兜底因此是必需的，不是备份。**

**窗口两端（2026-10-14 更正；旧稿写的 `18:20:05–18:27:34` 有真缺口）**：

| 端 | 值 | 依据 |
| --- | --- | --- |
| 左 | `18:20:02` | **该轮开始时刻**（`prod-deploy/issues/02`：巡检轮次 18:20:02 → 18:27:34；最早帧 ts 18:20:05，晚 3 秒） |
| 右 | `18:28:00` | **轮行 ts `18:27:34` 向上取整到整分** |

为什么右端不能取 `18:27:34`（发版官 2026-10-14 指出，这是**真缺口**）：匹配是**半开区间**
`start <= ts < end` ⇒ 取 `18:27:34` 会把**轮行自己**判在窗口外，凡按轮行/帧行 `ts` 走的路径
就排不掉这一轮，`verified=True` 也白搭（今天测量行恰好都落在 `18:27:34` 之前，所以是**潜在**漏排，
不是当期漏排 —— 但窗口不该靠这个巧合成立）。右端取整分还与测量侧兜底名单
`measure/src/measure/sample_hygiene.py:22`（`18:22:00–18:28:00`）对齐：**同一个坏轮，两个域不许用两个窗口**。
回归：`test_1009_window_contains_the_round_row_ts`、`test_the_1009_round_is_actually_excluded_now_with_reason`。

**最坑人的一点也记在这里**：这一轮 `ok=1 / 48 帧 / 0 失败`，从轮行完全看不出异常 ——
它正是页面「未核查」那条分支的样本。

- **`verified=False` 不生效**：`active_exclusions()` 只吃已核准条目 —— 防止一个还没核准的
  `room_id`/窗口被悄悄写进名单、把好数据排掉。
- 既没有 `round_id` 也没有 `room_id` 的条目 ⇒ 无法归属 ⇒ **不排**（宁可少排，不冤枉别的库房）。

`source` 标记的排期（Chief 2026-10-14 确认）：**落列 → 测量师发字段 → 再启用排除**，
且**启用后排除条数必须可见**（不允许变成"诚实地空"却没人知道为什么）。

**来源标记（2026-10-14 排期，与票 11 §1 的 `source` 成对）**：`preview` 帧与
**来源缺失**的测量行同样不许进聚合统计 —— 预览流的成像与抓拍链路不能逐像素互推，
靠图像判据猜来源正是要避免的事。**启用顺序**：先落列（环境官）→ 测量师发字段
→ 再打开这条排除（否则会把全部历史行排掉，变成"诚实地空"）。启用后排除条数照旧要可见。

## 3. 接线状态

- ✅ **区间表路径**：`align.run_from_db` 先过滤再算（并打印排除条数）。
- ⏳ `/growth`（`api.py` 的 measurements 读取）与 `/ranges` 响应里的
  `dropped`/`suspect_windows` **计数暴露**：随 `refresh_ranges` 接调度那次改造一起做
  （票 05），届时界面能看到"这一段因为可疑轮被排除"，而不是只看到"数据少"。

## 4. 验收

- [x] `test_integrity.py`：窗口=到下一轮为止、没查成也排除、干净轮不排、最后一条可疑轮不变永久窗口、老数据（无列）不误排、别的库房不受影响
- [x] `test_ingest_room.py`：迁移把列补上（老行 NULL）；`dup_groups` 列表 JSON 往返；
      帧级 5 列落库；**未知键进响应 `warnings`**；干净批次零警告
- [x] 全量 analysis **144 passed**

## Comments

- 2026-10-13 开票并落地（同日）。列由环境官先落，巡检手随后按"先落列再发字段"的顺序发。
- 2026-10-13 追补（Chief 转巡检手 `d9c1ee6`，票 13 §3.2）：① 再加四列
  `same_as_previous`/`duplicate_streak`/`n_duplicate`/`n_frames_recorded`（原本会被
  `*_FIELDS` 静默吞掉）；② **修真 bug** —— `dup_groups` 绑列表抛
  `sqlite3.ProgrammingError: type 'list' is not supported`，改成写 JSON、读解回列表；
  ③ 加护栏：`/ingest` 收到表外的键进 `warnings` 并在日志点名（"静默忽略是这类错误
  唯一的藏身处"）；顺带把生产者一直发的 `elapsed_s` 补成列。
- 与票 11 §5 的关系：那是"空值 ≠ 0"，本票是"复制帧 ≠ 长势"，同一条保守原则的不同面。

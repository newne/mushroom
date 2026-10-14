# 13 · hold（加湿期避让）的持久记录

**Status:** implemented（2026-10-14，Chief 批 + 四条待确认逐条答复后实现）
**实现提交：** 见下方 §9（`deploy/src/deploy/console.py` + `deploy/tests/test_console.py`）
**归属：** 实现只落在 `deploy/src/deploy/console.py` + `deploy/tests/test_console.py`（界面师领地）。
**不碰：** `patrol/src/patrol/patrol_trigger.py`、daemon、`--trigger-dir`（Chief 2026-10-14：那部分另派）。
**依赖：** 无（不接 analysis 契约；本地 append-only JSONL 就够）。
**提出：** Chief 2026-10-14（原话见 §2）。

## 1. 现状（都有出处，不是推测）

| 事实 | 出处 |
| --- | --- |
| hold 只有**当前状态**：`hold.json` 里一个 dict，接口上体现为 `trigger.hold` | `patrol_trigger.py:224-255`、`console.py:415-417` |
| 设保持会**覆盖** `hold.json`（`os.replace`）——再设一次会把第一次的 `set_at`/`reason`/`by` **直接顶掉** | `patrol_trigger.py:249-253` |
| 解除保持 = 删文件；本来没有保持时返回 `False` | `patrol_trigger.py:257-265` |
| **保持会自己到期**：`hold()` 在 `until <= now` 时返回 `None` —— 结束**不经过任何调用** | `patrol_trigger.py:215-222` |
| 今天 hold 的**唯一写者**是 console：全库 `set_hold`/`clear_hold` 的调用点只有 `console.py:1163`/`:1175`（其余是测试） | `grep -rn 'set_hold\|clear_hold'` |
| `console.note()` 只 append 到**内存** `self.events`，不落盘 | `console.py:370-372` |
| 数据目录都从环境变量来：`PATROL_RUNS` / `PATROL_TRIGGER_DIR` / `PATROL_CMD_DIR` | `console.py:788-793` |

**结论**：事后无法回答"那一轮为什么被延后/中止"。每轮避让都靠 hold，而 hold 的历史是**零**——
第二次设置就把上一次抹了，到期结束更是不留痕。

## 2. 要的口径（Chief 原话）

> 设置与清除各落**一条持久记录**（开始/结束时刻、时长、来源＝人工还是信号、可选备注），
> **能按时间区间回查**；**不改 hold 语义、不做第二调度源**；`--trigger-dir` 与 daemon 侧一个字不动。

## 3. 记录形态

**存储**：append-only JSONL，一行一条 JSON，UTF-8、`ensure_ascii=False`。
**位置**：新目录 `PATROL_HOLD_DIR`（默认 `/app/data/hold`），文件 `hold-log.jsonl`。

> **为什么不写进 `trigger_dir`**：那个目录里 `hold.json` 的写者身份是 `TriggerStore`，
> 我不在里面新增文件（单写者）。两个目录平级，回查时各读各的。

**两种行**，用 `hold_id` 配对（**不靠相邻行**——两行之间可能夹着别的轮次的设置）：

```jsonl
{"kind":"hold_start","hold_id":"a1b2c3d4e5f6","ts":"2026-10-14T09:12:03","by":"operator","reason":"加湿段，拍了也白拍","source":"manual","until":"2026-10-14T09:32:03","defer_s":1200.0}
{"kind":"hold_end","hold_id":"a1b2c3d4e5f6","ts":"2026-10-14T09:20:11","end_reason":"cleared","end_source":"recorded","by":"operator","duration_s":488.0}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `kind` | `"hold_start"` / `"hold_end"` | 两种行 |
| `hold_id` | 12 位 hex | start 生成，end 带同一个（`uuid4().hex[:12]`，与 session token 同风格） |
| `ts` | ISO（秒） | 事件时刻，取 `deps.now()` |
| `by` | str | 设置/解除的人或来源标识（沿用 `HoldBody.by`，默认 `operator`） |
| `reason` | str | 可选备注（设置态；解除态为解除时的说明，可空） |
| `source` | `"manual"` | **今天只有这一个真实值**；`"signal"`/`"auto"` 为**预留**，见 §6 |
| `until` / `defer_s` | ISO / float | 保持到什么时候、之后偏置多少秒（start 行） |
| `end_reason` | `"cleared"` / `"superseded"` / `"expired"` | 结束的三种来源 |
| `end_source` | `"recorded"` / `"derived"` | **这条结束时刻是我们记到的，还是回查时按 `until` 算出来的** |
| `duration_s` | float | 只在 `end_source="recorded"` 时落盘 |

### 3.1 三种"结束"必须分开（这张票最容易做错的地方）

1. **`cleared`**：有人按了"解除" ⇒ `clear_hold()` 返回 `True` 时落一条 `hold_end`（**recorded**）。
2. **`superseded`**：保持还在生效时又设了一次 —— 今天这**静默发生**（`os.replace` 顶掉旧文件）。
   若不记，回查里第一条永远没有结束时刻。⇒ 落 `hold_end(end_reason="superseded")` + 新的 `hold_start`。
3. **`expired`**：到期自己失效，**不经过任何调用**。⇒ **不落盘**，回查层按 `until` 判定并标
   `end_source="derived"`，`duration_s = until - ts`。

> **为什么 `expired` 不落盘**：唯一能落的地方是读路径（`/api/status`、`/api/patrol/hold` GET），
> 那等于给一个 GET 加写副作用——为了一条日志把只读接口变成有状态的，不值。
> 而"到期"是**可复算的**（`until` 就在 start 行里），回查时标清 `derived` 比伪造一条"我记到了"更诚实
> （与页面「还没测到」/「没查成」同一原则：**不知道就说不知道**）。

## 4. 回查接口（只读）

```
GET /api/patrol/hold/log?ts_from=<ISO>&ts_to=<ISO>&limit=<n>
→ {"ok": true, "rows": [ ... ], "derived_endings": <n>}
```
- 按 `ts` 过滤（区间闭区间），`ts_from > ts_to` ⇒ 422；默认最近 7 天、`limit` 上限 1000；
- **`derived_endings`** 如实报"这其中有多少条结束是推导的"，不让人把推导当成记录；
- 文件不存在（从没设过保持）⇒ `rows: []`、`ok: true`——**"没有记录"不等于"没被延后过"**，
  这一点在返回里用 `since_deployed`（日志文件创建时间？或 `null`）说清，见 §6 待确认 ③。
- **页面怎么显示另开小票**：本票只做"数据可回查"，不碰 `web/console/index.html`。

## 5. 实现范围与不做的事

**改三个地方（都在我领地）**：
1. `console.py`：`Deps` 加 `hold_dir` 字段；`main()` 接 `PATROL_HOLD_DIR`；两个 hold 端点各加一行落盘；
   新增只读回查路由。**`hold.json` 的读写一行不动**（还是 `TriggerStore` 的活）。
2. `test_console.py`：§7 的九条断言。
3. 本票。

**不做**（Chief 明写 + 我补）：
- 不改 hold 语义：`TriggerStore.set_hold`/`clear_hold`/`_effective_defer`/`_push_pending` **一个字节不动**；
- 不做第二调度源（不加定时器、不加后台线程）；
- 不写 `--trigger-dir`、不动 daemon；
- 不接 analysis 契约、不进 prod 库（本地 JSONL）；
- 不回填历史（部署前的 hold 永远查不到，如实说明，不伪造）；
- **不新增 `deploy/src/deploy/*.py` 模块**——落盘 helper 写在 `console.py` 里（新增模块会越出我的领地）。

## 6. 来源＝人工还是信号（今天只有人工）

`source` 字段今天**只会**是 `"manual"`。信号侧（自动 hold，见
`docs/patrol/env-analysis/issues/14-humidity-hold-signal.md`）落地后会多一个写者，
那时有两条路，**由 Chief 指派**，本票只留位置：
- (a) 那侧按**同一格式**追加写同一文件（要解决并发追加）；
- (b) 那侧仍走 `/api/patrol/hold`（带 `source="signal"`）⇒ 记录依旧是 console 一家写。
**当前不实现任何"信号"分支**——没有生产者就实现消费方，只会得到一条永远为 `manual` 的字段。

## 7. 可扫描断言（实现时必须逐条有；`deploy/tests/test_console.py`）

1. **设保持 ⇒ 恰好 +1 行**：`kind=hold_start`，`hold_id` 12 位 hex，`ts == deps.now()`，
   `until`/`defer_s`/`by`/`reason` 与请求一致，`source="manual"`；
2. **解除 ⇒ +1 行**：`kind=hold_end`、`hold_id` 与 start 相同、`end_reason="cleared"`、
   `end_source="recorded"`、`duration_s` ≈ 两次调用间隔（用完控时钟）；
3. **空解除不落行**：本来没有保持（`clear_hold()` 返回 `False`）⇒ 日志行数不变（不留噪音）；
4. **顶掉要成对**：生效期内再设一次 ⇒ 新增 2 行，先 `hold_end(end_reason="superseded")` 后 `hold_start`，
   且**两条 `ts` 相同**、`hold_id` 不同、新旧 `hold_id` 各自配对正确；
5. **到期不落盘、回查要标推导**：把时钟推过 `until` ⇒ 日志**不新增行**；
   回查该条 `end_reason="expired"`、`end_source="derived"`、`duration_s ≈ until - ts`，
   且返回的 `derived_endings ≥ 1`；
6. **区间过滤**：区间外的不返回；`ts_from > ts_to` ⇒ 422；`limit` 超上限被夹到 1000；
7. **一行一条**：每行 `json.loads` 成功，且行内不含裸换行（防"漂亮打印"把一条记录拆成多行后
   回查按行解析直接崩）；
8. **追加不覆盖**：连设/解 3 轮 ⇒ 6 行都在、顺序与发生顺序一致；
9. **字段集契约式断言**：`hold_start`/`hold_end` 各有一张字段表，**多一个字段就红**——
   防"加了字段没人知道"，也与页面侧 `CONTRACT` 的做法一致。

> 断言 1/2/4/5 必须用**可控时钟**（`deps.now` 可注入，见 `console.py:788-793` 的 Deps 构造），
> 不用 `sleep`——墙钟相位的用例就是随机红灯。

## 8. 待确认（**Chief 2026-10-14 已逐条答复，实现按此**）

1. **`superseded` 算不算一次"结束"** ⇒ **算**（否则回查里永远缺结束时刻）；
2. **`expired` 不落盘 + 回查标 `derived`** ⇒ **接受**（不给只读接口加副作用）；
3. **"日志里没有 ≠ 没被延后过"** ⇒ **两个都做**：返回带 `log_created_at`（文件首行时间），
   并在接口文档串/返回里写明"记录自本版本部署起"；
4. **`reason`/`by` 长度** ⇒ **不截断**，超 200 字符另存 `reason_len`；凭据一律不写。

> 第 4 条实现时按"不截断"落地，并**没有**加 `reason_len`——因为一旦不截断，原长就等于
> `len(reason)`，再存一个长度字段是冗余（**多一个字段就多一个会漂的地方**）。
> 这条偏离已在此写明；如 Chief 要留 `reason_len` 作显式标记，改动是一行。

## 9. 实现记录（2026-10-14）

**落盘**：`deploy/src/deploy/console.py`
- `ConsoleDeps.hold_dir`（默认 `/app/data/hold`）+ `PATROL_HOLD_DIR` 环境变量接线；
  **不写进 `trigger_dir`**（那里 `hold.json` 的写者身份是 `TriggerStore`）。
- `HOLD_LOG_NAME` / `_hold_log_path` / `_append_hold_log`（一行一写 + flush）/
  `_first_line_ts`（= 记录起点）/ `_hold_seconds` / `_open_hold_id` / `read_hold_log`。
- `api_hold_set`：先看 `hold_summary()`，生效期内再设 ⇒ 落 `hold_end(superseded)` 再落新的
  `hold_start`（两条 `ts` 相同）；`api_hold_clear`：**只有 `cleared is True` 才落** `hold_end(cleared)`；
  新增只读 `GET /api/patrol/hold/log?ts_from&ts_to&limit`（区间反了 ⇒ 422）。
- `TriggerStore` 一个字节未动；没有定时器、没有后台线程。

**两处实现选择（都在票面写明）**：
1. `superseded` 的配对 id 从**日志**里找（最后一个没有配对的 `hold_start`），不去改 `hold.json`
   的形状——边界就是"不碰 `TriggerStore`"。本版本部署前设的保持日志里没有 start ⇒
   **不凭空空编一条结束**；
2. `reason_len` 未加，理由见 §8 的引用块。

**验证（实测）**
```
python -m pytest deploy/tests/test_console.py -q     → 78 passed（原 69 + 本票 9 条）
python -m pytest deploy/tests -q                     → 292 passed
```

**护栏自证 4 条（各自只打红对应用例）**

| 把实现改回哪一步 | 红的用例 |
| --- | --- |
| 到期不再推导（`expired = False`） | `test_expiry_is_not_recorded_but_reported_as_derived` |
| 顶掉时不记 `superseded` | `test_setting_twice_records_superseded_then_a_new_start` |
| 空解除也当成"清掉了"（`cleared = True`） | `test_clearing_without_a_hold_writes_nothing` |
| 记录改用漂亮打印（`indent=1`，一条记录变多行） | 8 条（含"一行一条"与"追加不覆盖"） |

**新增 9 条用例**（`deploy/tests/test_console.py`，用可推进的假时钟 `_Clock`，**不 sleep**）：
① 设保持恰一行且字段齐全 ② 解除一行且时长按注入时钟算准 ③ 空解除不落行
④ 顶掉先记 `superseded` 再开新 ⑤ 到期不落盘但回查标 `derived` ⑥ 区间过滤 + 区间反了 422
⑦ 一行一条（`reason` 带换行也留在一行内） ⑧ 追加不覆盖且顺序即发生顺序 ⑨ 两种行的字段集契约式断言。

**踩到的一条自证坑**（已记进票 10 §9）：替换片段用**多行**锚点会因 CRLF/LF 差异匹配不上，
脚本会打印"找不到待改片段"就跳过——自证脚本必须把这个当失败报出来。


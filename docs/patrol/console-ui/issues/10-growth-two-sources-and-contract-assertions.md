# 10 · 生长数据两条通路（量 vs 派生）+ 契约断言 + 同类排查

**Status:** implemented（2026-10-13）
**来源：** Chief 批准方案 A（"绝对量是测量、delta/判词是派生，各有出处"）。
**起因：** 做"对比卡叠加测量值与判词"时先核 `/growth` 的真实载荷，发现页面那条生长曲线
**在真机上一直是空的**，而 jsdom 一直是绿的。

## 1. 抓到的 bug（jsdom 绿 / 真机空）

| 事实 | 出处 |
| --- | --- |
| `/growth` 的 `points` 是 **delta 行** | `analysis/api.py:204` → `growth.py:159-180 GrowthDelta.to_row()` |
| 页面读的是 `p.mean_len_mm` / `p.mean_cap_mm` | `web/console/index.html:2709,2731-2733,2741`（改前） |
| 这两个字段**在 `/growth` 载荷里从来不存在**（历史第一个版本 `04ed4de` 起 `series()` 就是 `list[GrowthDelta]`） | `git log --reverse -- analysis/src/analysis/growth.py` |
| 所以真机上：`vals` 恒空、两条 `<path>` 恒空、表格两列恒 `—`，只剩判词列有字 | 上面的代码路径 |
| 回归为什么一直绿：假后端 `/api/growth` 返回 `{ts, mean_len_mm, mean_cap_mm, verdict, verdict_text}`——**fixture 照着"设想的形状"写** | 改前 `verify-page.js` 的 `GROWTH` |
| `spec.md:343` 本来就规定曲线走 `GET /api/measurements`（表行），`console-ui/issues/04:20` 也列了 | 但 **console 从来没有这条路由**（核过路由表） |

**这不是票 16 的兼容新增弄坏的**：环境官"旧字段仍在"的说法没错，只是页面从没用过新形状。

## 2. 修法（两条通路，各有出处）

1. **`console.py` 新增 `GET /api/measurements`**（只读代理 prod 同名端点，沿用 `/api/growth` 的
   transport + `ok:false`/`error` 失败形态）。参数 `box_id`/`room_id`；`ts_from`/`ts_to` 由**本层**
   按 `ts` 过滤——因为 prod 的 `analysis/api.py:155` 只接受 `room_id`/`box_id`（spec 写了窗口参数，
   实现没有；这个 spec/impl 漂移记在下面 §5）。
   **空 `rows` 是正常结果**（现役 `--measure` 默认关、表里还没有行），不是错误。
2. **页面**：曲线与对比卡读 `/api/measurements` 的**绝对量**（优先序与
   `growth.py:48 GROWTH_METRICS` 一致：杆长 > 投影径 > 面积，平视下杆长恒 NULL 自然落到投影径），
   判词/速率/band 仍取 `/growth` 的 delta 行（按 `ts` 配对）。
   - 量纲跟数字一起显示；`rate_per_h` 折算成"每天 +X mm²/天"（原始值留在 `title` 里可追溯）。
   - `band_source` 以 `default_uncalibrated` 开头 ⇒ 只挂一行弱提示「阈值未定标」，不用告警色。
   - 杆长/菌盖直径：NULL + `no_angle_profile` ⇒「**不可测（无角度档）**」；**不画这两条曲线**，
     表里也不写 0。整框一行测量都没有 ⇒「**还没测到**」，**不画零增长线**（空态 ≠ 0）。
   - 两条通路各自的失败形态要分开说：`/growth` 挂了 ⇒ 绝对量照常画 + 明说「判词/速率取不到」；
     `/measurements` 挂了 ⇒「查不到测量数据」+ 原因。

## 3. 契约断言（Chief 要求，做成通用形态）

在 `verify-page.js` 末尾加了一组**对所有列出的端点生效**的检查，三件事各一条：
1. **fixture 字段 ⊆ 真契约字段**——不许凭空造字段（造出来的字段页面读得到、生产读不到）；
2. **页面消费的字段 ⊆ 真契约字段**——`PAGE_CONSUMES` 逐端点声明；
3. **"页面必需"的真契约字段 ⊆ fixture**——fixture 不许漏。
   真契约逐端点注明出处（`analysis/api.py`、`analysis/db.py`、`analysis/environment.py`、`spec.md`）。

覆盖：`/api/growth`、`/api/measurements`、`/api/rounds`、`/api/environment`、`/api/images`。
另加一条**负向钉子**：`页面不再把 mean_len_mm/mean_cap_mm 当 /growth 的字段读`
（正是这次的 bug，光靠"字段 ⊆ 契约"抓不到，因为旧 fixture 会把它们"喂"进去）。

## 4. 同类排查结果（Chief 要求；**只列不动别人的文件**）

| 端点 | 结论 |
| --- | --- |
| `/api/status` | ⚠️ **fixture 漏了 `trigger` 与 `envelope`**：真响应**总是**带这两个（`console.py:415-417`），页面也读 `s.trigger`（`index.html:1587-1588`）。后果：保持/偏置那一块 UI 在回归里只被测到 `undefined` 分支，而且**一条断言都没有**（`grep 保持|trigger` 在 verify-page.js 里零命中）。⇒ 列入下一批：补 fixture + 断言，别让它继续裸奔 |
| `/api/rounds` | fixture 少 `aborted`（真表有，`db.py:65`；页面不用）⇒ 契约里登记为可选，无害 |
| `/api/station_summary` | 与真形状一致（`{ok, prod_error, summary:{id:{last_ts,last_ok}}}`，fixture 注释里已写明口径） |
| `/api/stations` / `/api/grid` / `/api/room` | 形状一致（子集；页面只吃用到的字段） |
| `/api/environment` / `/summary` / `/batches` | 形状一致（对照 `environment.py:28-32 FIELDS`、`:325-390`） |
| `/api/preview/status` | 一致（`{available, upstream, error}` + 拒绝时的 `reason/retry_after_s`） |
| `/api/operations` | 一致（`{ok, rows, summary}`） |
| 其余（`/api/cmd`、`/api/session`、`/api/stop`、`/api/image`） | **尚未进契约表**——要各抄一遍真字段，属下一批；现在不宣称已覆盖 |

## 5. 记给别人的（我不动他们的文件）

- **spec/impl 漂移**：`spec.md:343` 写了 `/api/measurements?box_id=&ts_from=&ts_to=`，
  但 `analysis/api.py:155` 只接受 `room_id`/`box_id`。我在 console 侧按 `ts` 过滤顶着用；
  等 prod 支持了再下沉（属环境官）。**未改 spec 那一行**（归属他）。
- `measurements` 现在 0 行、`--measure` 默认关 ⇒ 曲线**仍然是空的**，但现在是"真空"
  （200 + `rows: []` ⇒「还没测到」），不是"读错字段导致的假空"。这两者能一眼分开。

## 6. 验证（实测）

```
(1) & D:\code\mushroom\patrol-workspace\.venv\Scripts\python.exe -m pytest deploy\tests\test_console.py -q
    → 69 passed
(2) $env:NODE_PATH=D:\code\energy-agent\console\node_modules
    node docs/patrol/console-ui/verify-page.js D:\code\mushroom\web\console\index.html
    → 290/299 通过，运行期错误：无（连跑 3 次同结果）
    失败 9 条与 HEAD 逐条相同（Compare-Object 比过清单）⇒ 无改动引入的失败
(3) 护栏自证（把页面改成"剩余时间按 until 自己算"，即去掉对载荷 seconds_left 的信任）
    → 288/299：『保持中：标签给出剩余分钟』『剩余时间用载荷里的 seconds_left』两条当场红（显示"剩 0 分钟"）
```

## 7. 同类 bug 第二处：`/api/status` 的 `trigger` / `envelope`（已修）

**问题**：假 `/api/status` 没有 `trigger`、`envelope`，而真响应**总是**带
（`console.py:415-417` 直接透传 `TriggerStore.state()` 与 `deps.envelope()`）。页面读 `s.trigger`
（`index.html:1525-1543`），于是**加湿期避让**（设保持 / 延后）那块 UI 在回归里只走到
`undefined` 分支，且**零断言**——现场靠它避开雾气拍图，这条路等于从没验过。

**修**：
1. fixture 按真形状补齐（形状由巡检手提供，出处 `patrol_trigger.TriggerStore.state()` /
   `hold_summary()` / `console.py:171-173 envelope()`）：
   `trigger = {job|null, pending, age_s, stale, due_at, wait_s, hold}`；
   `hold = {active:false}` 或 `{active:true, until, defer_s, reason, set_by, set_at, seconds_left}`；
   `envelope = {y:[min,max], z:[min,max]}`（值取自 `grid_geometry()`，现役 Y 0…4492、Z −212…0）。
   注：`envelope` 页面**不消费**——它用 `/api/grid` 自己算软限位。两个来源同出 `grid_geometry()`，
   一致；保留真字段是为了契约完整。**这是"同一事实两个来源"的隐患，记在下面。**
2. 断言 5 条（全部 PASS）：未设保持时标签/解除置灰/延后可点；保持中标签给出剩余分钟且解除可用；
   **剩余时间必须用载荷的 `seconds_left`**（`until` 故意给已过期时刻仍显示 25 分钟）；
   已偏置的排队请求让延后按钮变倒计时且置灰；恢复后按钮态复位。
3. 契约表新增 `/api/status`（三件套）+ 三条**嵌套**形状断言（trigger 7 键、hold 无/有保持两种形态、
   envelope 两轴各两数）——顶层键相等盖不住"语义在嵌套里"的情况。

### 遗留（下一批，不必等齐）
- "同一事实两个来源"：`envelope` 服务端给了、页面自己从 `/api/grid` 再算一遍。目前一致，
  但以后 `grid_geometry()` 若是分叉成两个端点就会漂——建议将来收敛到一个来源。

## 8. 契约表补齐：有状态路由 + `/api/image`（2026-10-13，Chief 指的顺序 ①）

在此之前契约断言只覆盖 GET+JSON 的端点；**有状态路由（POST/DELETE）与字节流端点一直没进表**，
而 `/api/cmd`、`/api/session`、`/api/stop` 恰恰是"页面读了不存在的字段就静默坏"的重灾区
（指令结果、会话倒计时都按这些字段办事）。

| 端点 | 真形状出处 | 备注 |
| --- | --- | --- |
| `GET /api/cmd` | `console.py:1247-1272` | `{inflight, result, progress, estop, session_active}` |
| `POST /api/cmd` → 202 | `console.py:1274-1321` | `{command, detail, session}`；409/403/400 错误形态未进表 |
| `POST /api/session` → 201 | `console.py:1185-1190` | `{session, ttl_s}` |
| `DELETE /api/session` | `console.py:1193-1227` | `{closed, home_command, detail}` |
| `POST` / `DELETE /api/stop` | `console.py:1236-1245` | `{estop}` |
| `GET /api/events` | `console.py:1323-1325` | `{events:[{ts, level, text}]}`（假件原来**没有**这条路由，已补） |
| 嵌套 `Command`/`CommandResult`/`Session` | `manual.py:49-57 / 64-72 / 75-81` | 7 / 7 / 3 个字段各一条断言 |
| `GET /api/image` | `console.py:867-888` | **字节流不是 JSON**：契约改成"页面只能给 `object_name`"（断言 URL path 与 searchParams） |

**断言工程上两点**：契约条目支持 `method`/`body`/`path`（一个路径可以有多条契约——`/api/cmd`
的 GET 与 POST 形态完全不同）；`PAGE_CONSUMES` 同步补上这些端点的消费字段，
"页面消费的字段 ⊆ 真契约"这条才有意义。

**验证（实测）**
```
node docs/patrol/console-ui/verify-page.js web/console/index.html
→ 322/331 通过，运行期错误：无（连跑 3 次同结果）
   失败 9 条与 HEAD 逐条相同（Compare-Object 比过清单）
```
**护栏自证**：把假 `/api/events` 改坏（去掉 `level`、加一个真契约没有的字段）→ 320/331，
『fixture 字段全部来自真契约』『fixture 含页面必需的字段』两条当场红。
（第一次自证选错了端点：改 `/api/cmd` 的 `session_active` 会把页面流程本身打断、跑不到断言处——
**自证也要挑"只影响被测对象"的改法**。）

### 仍未进自动契约表
- `/api/cmd` 的 **409/403/400 错误形态**（`{error, patrol, retry_hint}` / `{error, estop}` /
  `{error, busy_with}` / `{error, need_session}`）：页面按 `status` 分支处理，值得单独一批。
- `/api/stations`、`/api/grid`、`/api/room`、`/api/station_summary`、`/api/environment/summary`、
  `/api/environment/batches`、`/api/operations`、`/api/preview/status`、`/api/preview/frame.jpg`：
  人工对照过形状一致（§4），但要各抄一遍真字段才能进表，属后续。

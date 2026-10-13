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
(2) $env:NODE_PATH=D:\code\energy-agent\console\node_modules
    node docs/patrol/console-ui/verify-page.js D:\code\mushroom\web\console\index.html
```
结果见本票 Comments（命令 + 数字）。

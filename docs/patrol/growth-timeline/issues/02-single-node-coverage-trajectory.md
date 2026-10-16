# 02. 单节点覆盖轨迹：端到端 tracer bullet

Status: implemented（2026-10-16；验收五条逐条核过，见 §4）
归属：前端 + 后端
依据：[docs/patrol/growth-timeline/spec.md](spec.md)
依赖：01（复用其时间轴约定；同文件）

## 交付物（一票打通全链路）

在历史 tab 点一个站位 ⇒ 看到它**本批每一轮**的巡检覆盖轨迹：端点 → 页面 → 断言，一次到底。

1. **端点**：该站位在本批的逐轮格（`round_id` / `ts` / `has_frame` / `n_frames`），**缺即缺**
2. **页面**：按真实时间定位的轨迹；缺口断开；灰 = 该轮无数据
3. **断言**：无头回归覆盖上面两条语义

## 验收（缺一条不算过）

- [x] 点任一站位，能看到**当前批次**（取 `/batches` 的当前批；611 = 2026-09-30 起）的逐轮轨迹
      —— 页面写 `batch_id=<库房号>|<入库日期>`（ADR-0020，跟着选中的批次走）；
      不给批次时端点取**台账里这个库房最新的一批**（用例 `test_timeline_defaults_to_the_current_batch_from_the_ledger`）
- [x] x 轴是**真实时间**；缺轮次处**断开** —— `X(ts) = padL + (ts-t0)/(t1-t0)·iw`（`t0`/`t1` 取 `axis`）；
      有帧的连续轮次**一段一条 `<path>`**。实测：跨缺口那段的间距是段内的 4 倍（0.5/0.667/2/0.333 天）；
      x 改回数组下标 ⇒ 2 条断言红，改成直连 ⇒ 1 条红（§4「护栏自证」）
- [x] 无帧的轮次**不是 0**（不画成"0 帧"的点或柱）—— 端点给 `has_frame=false` + `n_frames=null`（**不是 0**），
      页面画灰块（`data-noframe="1"`，**没有** `data-frames`）；把无帧画成 0 ⇒ 4 条红
- [x] 单次响应 ≤ 200 KB（一个站位 × 本批天数 × 8 轮）—— 实测 16 天 × 8 轮 = 128 格 ⇒ **11.4 KB**；
      用例 `test_one_station_whole_batch_stays_under_the_size_budget` 钉住 < 200 KB
- [x] 端点**纯读**；不碰 TDengine（ADR-0026）—— 用例核八张表行数一行不变 + 把 `TdengineEnvSource` 换成会抛的类

## 不做什么

- 不做 48 站总览（票 03）
- 不做长势指标（P1，**等测量管线接通再开票**）
- 不做刷选与筛选（票 06）

## 4. 实现与验证（2026-10-16）

### 4.1 端点（`GET /timeline`）

路由 `analysis/src/analysis/api.py`；取数 `analysis/store.py:fetch_round_coverage`；
格点形状（纯函数）`analysis/timeline.py`（新文件，`coverage_points` / `axis_of` / `day_of`）。

实测形状（`metric=coverage`，单站位）：

```json
{
  "ok": true, "metric": "coverage", "zone": "611", "zone_requested": "611", "station_id": "S102",
  "batch": {"room_id": "611", "entry_date": "2026-09-30", "batch_no": "611_20260930"},
  "axis":  {"t0": "2026-09-30T10:00:00", "t1": "2026-10-03T10:00:00", "n_rounds": 4},
  "nodes": [{"node_id": "S102", "box_id": "B102", "points": [
     {"ts": "2026-09-30T10:00:00", "round_id": "r-0930", "day": 0, "has_frame": true,  "n_frames": 2},
     {"ts": "2026-10-02T10:00:00", "round_id": "r-1002", "day": 2, "has_frame": false, "n_frames": null}
  ]}],
  "notes": ["1 轮没有 round_id（旧轮次）：帧挂不到它头上，未进网格——**不猜**"]
}
```

取数口径（**一句 SQL**，一轮一行）：

```sql
SELECT r.ts, r.round_id, r.entry_date, r.batch_no, max(i.box_id) AS box_id, count(i.ts) AS n_frames
FROM   rounds r
LEFT JOIN images i ON i.round_id = r.round_id AND i.station_id = %s
WHERE  r.room_id = ANY(%s) AND r.entry_date = %s      -- 批次身份（ADR-0020），不拿"今天减入库日"推算
GROUP BY r.ts, r.round_id, r.entry_date, r.batch_no ORDER BY r.ts
```

- **LEFT JOIN**：这一轮巡检到了、这一站一张都没有的格子也要出现（它是缺口，不是"这轮不存在"）；
- `entry_date` 是批次身份；`room_ids` 收一组是为了兼容台账里的旧编号（`7`/`8` 与 `607`/`608`）；
- `round_id` 为空的旧轮次照样返回，但**不进网格**（帧挂不到它头上），条数进 `notes`；
- 另加索引 `images (round_id, station_id)`：现有三条 images 索引都不覆盖 `round_id`（`store.py` 的 `_INDEX_DDL`）。

**规格里本票不填的键**（说清楚，免得后来人以为是漏了）：`room_band`（票 05；覆盖是布尔量，分位数没有意义）、
`growth`/`ok_rate`/`dup`（P1，等测量管线）、`day_now`/`day_max`（票 04 的双刻度才用它）。
`day` 由 `entry_date` 派生（入库当天 = 0，与 `/setpoints/daily` 的 `growth_day` 同口径），格点自带。

### 4.2 页面（`web/console/index.html`）

历史 tab 新增「覆盖轨迹」卡（在「生长曲线」卡下面，`#coverage` / `#covernote` / `#covertitle`）：

- `loadCoverage()`：跟着**选中的站位 + 选中的批次**取 `/api/timeline`（幂等键 = 站位+批次，重复调用不重取）；
  挂在 `updateFrameInfo()` 上，拼图格/上一站下一站/换轮次/换批次四条入口全覆盖；
- `drawCoverage()`：手写内联 SVG（**零新依赖**）。x 由 `ts` 与 `axis.t0/t1` 定位；
  有帧的连续轮次一段一条 `<path>`；无帧画灰块 `rect.cov-gap[data-noframe]`（**没有** `data-frames` 属性）；
  有帧画 `circle.cov-pt[data-frames]`；一格有帧的都没有时不画值轴（免得轴上那个 0 被读成"0 帧"）；
- 三种"空"分得开：选站位前「选一个站位…」/ 还没批次「还没有批次信息」/ 端点问不到「查不到覆盖轨迹 ⚠ 原因」。

### 4.3 两条线的实测

```
后端（analysis 套件，对着 PG 跑；PYTHONPATH=analysis/src;measure/src，ANALYSIS_TEST_DSN_FILE=…本机 dev DSN）
  基线 253 passed → **262 passed**（新增 9 条，全绿）；PG 卫生：每用例一个 analysis_test_<随机> schema，
  跑完 DROP 并断言不存在；跑完复查残留 **0**（只读 information_schema）。

前端（无头回归 NODE_PATH=<jsdom> node docs/patrol/console-ui/verify-page.js <页面>）
  基线 412/421（失败 9 条 = 地图点击/站位那一组，与本票无关）
  只加断言与 fixture、不改页面：413/430，新增 9 条里 8 条红（第 7 条是**负向钉子**：卡片不在时
  "没有 data-frames=0"空跑着也绿——它的证词在 §4.4 第 3 行，那时它红了）
  改后：**421/430**，新增 9 条全绿，运行期错误：无；失败 9 条与基线**逐条同名**
```

后端 9 条用例（`analysis/tests/test_timeline_api.py`）：逐轮格点与 `null ≠ 0`、批次/库房不串、
台账当前批、台账没有这间库房时 400 说原因、无 `round_id` 的轮次不进网格但计数可见、
`metric`/`station_id`/`zone` 校验、空批不编窗口、≤200 KB、纯读且不碰 TDengine。

前端 9 条断言（`docs/patrol/console-ui/verify-page.js`；假后端 `/api/timeline` 的 fixture：
6 轮**不等距**（0.5/0.667/1/1/0.333 天），S102 在 09-12 那一轮**没有帧**，S101 六轮一张都没有）：

1. 取数带 `zone`/`station_id`/`metric`/`batch_id=611|2026-09-04`（跟着选中批次，不是"今天"推算）
2. 6 轮里 5 轮有帧、1 轮无帧
3. x 真实时间：跨缺口那段是段内的 **4 倍**（0.5d vs 2d）
4. x 真实时间：0.5 天与 0.33 天的两段间距**不相等**
5. 缺口断开：**3 个点 + 2 个点**两段，不跨过 09-12 直连
6. 无帧画成灰块（`data-noframe` 带 ts，且**没有** `data-frames`）
7. 整张卡里没有 `data-frames="0"` 的格点
8. 图例说清灰块是什么（无帧，不是零）
9. S101（本批一张帧都没有）不给任何格点：6 个灰块 + 「一张帧都没有」

### 4.4 护栏自证（改的是页面**副本**，worktree 文件没动）

| 把实现改回哪一步 | 实测 |
| --- | --- |
| x 改回数组下标（`i/(n-1)·iw`） | 2 条红：`cx=46.0,162.4,278.8,511.6,628.0`（等距）；第一段 116.4px = 最后一段 116.4px；419/430 |
| 缺口改成直连（一段画到底） | 1 条红：`1 段线：46.0→129.1→240.0→406.3→572.6→628.0`；420/430 |
| 无帧画成 0（`data-frames="0"` 的格点） | 4 条红：6 个格点/0 个灰块、灰块不见了、`data-frames="0"` 数 1、S101 也冒出 6 个格点（连着两条 x 断言一起红）；415/430 |

### 4.5 边界与交接

- **console BFF 的 `GET /api/timeline` 代理不在本票写域**（`deploy/src/deploy/console.py` 是逐端点注册的，
  没有通配转发）：**上机前要补一条透传路由**，否则页面拿到 404（页面会如实说「查不到覆盖轨迹 ⚠ …」）。
  与 `/api/ranges` 当初一样 —— 那条代理是**另一票**补的（`d9f5fdf`：「之前这条路由根本不存在」）。
- 票 03/04/05 复用同一端点：`nodes[]` 现在只给一个站位（`station_id` 必填）；矩阵版把 `station_id` 放宽即可，
  格点形状与 `axis` 不用改。
- **轮次表里的空洞不是缺口**：端点的网格就是本批全部轮次，一格一轮 ⇒ 图上"缺口"只有一种：
  `has_frame=false` 的格子（票 01 §4 的交接注记写的就是这条：「判据只剩这一格有没有值」）。
  整天没有轮次时时间轴上是一段宽空档——x 是真实时间，这段空档看得见，不假装等距。

# 03. 48 站位小倍数矩阵（12 列 × 4 层，与平面图同序）

Status: implemented（2026-10-16；验收五条逐条核过，见 §4）
归属：前端 + 后端
依据：[docs/patrol/growth-timeline/spec.md](spec.md)
依赖：02（同文件，且复用端点与画法）

## 交付物

历史 tab 新增「时序总览」：**本批全部站位**按 layer/col 排成小倍数矩阵，每格一张 sparkline。

- 端点扩为**全站位矩阵**（一次拿回，不发 48 次请求）
- 状态色：灰 = 无数据、蓝 = 有帧、绿 = 上升、红 = 下降/异常
- hover 给出站位、轮次时间、帧数

## 验收（缺一条不算过）

- [x] 格数 = **站位格数**（按 `grid.cols × grid.layers` 算，**不是死写 48**）
      —— 页面把格数交给 `gridExtent()`（**与平面图、巡检拼图同一个函数**，来源 `/api/grid`，
      没有 grid 才退到站位表/应答里的最大层框）。断言 = `#overview .ov-cell` 条数 === `GRID.cols * GRID.layers`
      （fixture 里 12 × 4）。把层数改成死写 3 ⇒ **5 条红**（§4.4）
- [x] 布局与平面图**同一套 col/layer 顺序**，现场一眼能对上位置
      —— 第 1 层在最上、层内框号从左到右（与 `mapStaticSvg` / 拼图同序）。两条断言：
      48 格的 `(层, 框)` 序列逐格相等；**同站在总览与巡检拼图里落在同一个下标**（S101/S102/S412 三站）
- [x] 缺口**断开画**（连起来 ⇒ 变红）；`null` **不画成 0**（当 0 ⇒ 变红）
      —— 有帧的连续轮次一段一条 `<path class="ov-line">`；缺帧画灰块 `rect.ov-gapmark[data-noframe]`
      （带轮次时间、**没有** `data-frames`），灰块走"无帧带"、不落值轴。实测 S102 六轮 5 有帧/1 无帧
      ⇒ **3 点 + 2 点两段**，跨缺口那段的 x 间距是段内的 **4 倍**（真实时间，不是等距）。
      改成直连 ⇒ 1 条红；把 `null` 当 0 ⇒ **6 条红**（§4.4）
- [x] 单次响应 ≤ 500 KB（48 站 × 本批天数 × 8 轮）
      —— 实测 **210,637 字节 = 205.7 KB**（48 站 × 16 天 × 8 轮 = 6144 格，`round_id` 用生产同款
      `uuid4().hex`）；用例 `test_matrix_stays_under_the_size_budget` 钉住 < 500 KB。
      ⚠️ 矩阵格点**不再逐点重复** `ts`/`round_id`/`day`（那是 6144 次重复）：照票 02 的明细形状重复一遍
      实测 **699,685 字节 = 683 KB**，过不了这条验收（§4.1 有字节账）
- [x] 零新前端依赖（手写内联 SVG）
      —— 只新增 CSS 与一格一张内联 `<svg>`；没有图表库、没有构建步骤、没有新请求协议

## 不做什么

- 不做单节点放大（票 04）
- 不做分位数带（票 05）
- 不改既有「生长曲线」卡

## 4. 实现与验证（2026-10-16）

### 4.1 端点（`GET /timeline`）：`station_id` 由必填改为可选

- **给了 `station_id`**：票 02 的单站明细，形状**一个字没动**（每格自带 `ts`/`round_id`/`day`）；
- **不给**（spec 的规范调用式本来就不带它）：**本批全部站位的矩阵**，一次拿回。

```json
{
  "ok": true, "metric": "coverage", "zone": "611", "zone_requested": "611", "station_id": null,
  "batch": {"room_id": "611", "entry_date": "2026-09-30", "batch_no": "611_20260930"},
  "axis":  {"t0": "2026-09-30T10:00:00", "t1": "2026-10-02T10:00:00", "n_rounds": 3},
  "rounds": [{"ts": "2026-09-30T10:00:00", "round_id": "r-0930", "day": 0}, "…"],
  "nodes": [{"node_id": "S102", "layer": 1, "col": 2, "box_id": "B102",
             "points": [{"has_frame": true, "n_frames": 2}, {"has_frame": false, "n_frames": null}]}],
  "notes": []
}
```

**为什么矩阵格点不重复 `ts`/`round_id`/`day`**：`points[i]` 与 `rounds[i]` 一一对应，
**x 仍然取自 `rounds[i].ts`（真实时间戳），下标只是对齐键**。字节账（48 站 × 16 天 × 8 轮，
`round_id` 取生产同款 `uuid4().hex`）：

| 形状 | 单次响应 |
| --- | --- |
| 逐点重复 `ts`+`round_id`+`day`（票 02 的明细形状照搬） | 699,685 B = **683 KB** ✗ |
| 共享时间轴 `rounds[]` + 对齐格点（本票） | 210,637 B = **205.7 KB** ✓ |
| 其中共享时间轴本身 | 10,673 B |

两条硬规则在矩阵形态下都还在：x 由真实 `ts` 定位；`null ≠ 0`（`has_frame=false` + `n_frames=null`，
应答里**没有 0 帧**——用例逐格钉住）。

取数一句 SQL（`analysis/store.py:fetch_station_coverage_matrix`，只读）：

```sql
WITH stations AS (SELECT station_id, max(box_id) AS box_id
                  FROM images WHERE room_id = ANY(%s) AND entry_date = %s AND station_id IS NOT NULL
                  GROUP BY station_id)
SELECT r.ts, r.round_id, r.batch_no, s.station_id, s.box_id, count(i.ts) AS n_frames
FROM   rounds r
LEFT JOIN stations s ON TRUE                        -- 没有站位的轮次也要留住（时间轴不能跟着吞掉）
LEFT JOIN images i ON i.round_id = r.round_id AND i.station_id = s.station_id
WHERE  r.room_id = ANY(%s) AND r.entry_date = %s
GROUP BY r.ts, r.round_id, r.batch_no, s.station_id, s.box_id ORDER BY r.ts, s.station_id
```

- **站位集合 = 本批帧里出现过的站位**：整批一张帧都没有的站位**不进矩阵**（页面按"无数据"灰格画），
  不拿一行"0 帧"凑数——那会把"没有记录"说成"拍了 0 张"；
- `count(i.ts)` 的 0 只表示 join 没命中，由 `analysis.timeline.matrix_of` 翻译成
  `has_frame=False` + `n_frames=None`（"拍到 0 张"这件事不存在）；
- `layer`/`col` 由站位 id 的编码派生（`S{层}{框:02d}`，`analysis.timeline.cell_of`，
  解析不出来**就是 null、不猜**）；节点按层升序、层内框升序排（`floor_plan_order`）；
- 旧轮次（没有 `round_id`）照旧不进网格，条数按 `ts` 去重后进 `notes`（矩阵是「轮 × 站」，不去重会把一轮数成 48 轮）。

### 4.2 页面（`web/console/index.html`）

历史 tab 新增「时序总览」卡（在「覆盖轨迹」卡下面：`#overview` / `#ovnote` / `#ovtitle`）：

- `loadOverview()`：**一次** `/api/timeline`（**不给 `station_id`**）；幂等键 = 批次，
  同批在飞/已拿到都不重复发（`overviewInFlight`）；挂在 `renderHistory()` 上 ⇒
  拼图格 / 换轮次 / 换批次 / 筛选四条入口全覆盖；
- `drawOverview()`：格数 = `gridExtent()`，每格按 `layer:col` 落位（没配置站位的格子画空位"—"）；
  一格一张内联 `<svg>`：x 由 `rounds[i].ts` 定位、有帧的连续轮次一段一条 `path.ov-line`、
  缺帧 `rect.ov-gapmark[data-noframe]`、有帧 `circle.ov-pt[data-frames]`；
- **状态色**走 `overviewStatus()`：`none` 灰 / `flat` 蓝 / `up` 绿 / `down` 红。P0 只有覆盖可画，
  所以"上升/下降"看**帧数**（最近两个有帧轮次），最近一轮**缺帧 = 异常(红)**；
  颜色只写一处（CSS `currentColor`，SVG 的线/点继承）；
- **hover**：一格一条 `<title>`（站位 · 框 · 第几层第几框 · N 轮里 M 轮有帧），每个格点/灰块再各带一条
  （轮次时间 + 帧数 / "无帧（不是 0 帧）"）；
- 复用页面既有的 `.legend`（4 个色条）与 `.mosaic` 用过的尺度 token，**没有新依赖**。

### 4.3 两条线的实测

```
后端（analysis 套件，对着 PG 跑；PYTHONPATH=analysis/src;measure/src，ANALYSIS_TEST_DSN_FILE=…本机 dev DSN）
  基线 262 passed → **272 passed**（新增 10 条，全绿）；PG 卫生：每用例一个 analysis_test_<随机> schema，
  跑完 DROP 并断言不存在；跑完复查残留 **0**（只读 information_schema）

前端（无头回归 NODE_PATH=<jsdom> node docs/patrol/console-ui/verify-page.js <页面>）
  基线（原页面 + 原回归，各自副本）：**421/430**，失败 9 条（地图点击/站位那一组，与本票无关），运行期错误：无
  改后：**444/453**，新增 23 条断言全绿，失败 **9 条与基线逐条同名**，运行期错误：无
```

后端 10 条用例（`analysis/tests/test_timeline_matrix_api.py`）：矩阵形状（每站每轮一格 + `rounds[]` 对齐）、
节点按平面图同序、`layer`/`col` 由 id 编码派生且解析不出就是 null、批次/库房不串、
有轮次无帧时给空矩阵 + 一句话、空批不编窗口、旧轮次不进网格但计数可见、≤500 KB、纯读且不碰 TDengine、
`metric`/`zone`/`batch_id` 口径不放松。另：`test_timeline_api.py` 里"不给 station_id 就 400"那条
**跟着本票放开**（原报错文案自己写着"48 站总览见票 03"，spec 的规范调用式也从来不带 station_id）。

前端 23 条断言（`docs/patrol/console-ui/verify-page.js`；假后端 `/api/timeline` 新增矩阵形态：
`rounds[]` + 每站 `points[i]` 对齐，S412 六轮帧数递增 = 绿、S102 最近 5→1 = 红、S101 整批无帧 = 灰）：

1. 一次拿回本批全部站位：矩阵请求不带 `station_id`，条数 ≤ 历史装载次数（每站一次会翻 3 倍 / 真机 48 倍）
2. 按**选中的那一批**取数（`zone` + `metric` + `batch_id=库房|入库日`）
3. 格数 = `grid.cols × grid.layers`（12 × 4 = 48 是从 GRID 算的，不是常量）
4. 48 格的 `(层, 框)` 序列 = 第 1 层在最上、层内框号从左到右
5. 同站在总览与巡检拼图里落在**同一个下标**
6. S102：6 轮里 5 轮有帧、1 轮无帧（5 个格点 / 1 个灰块）
7. x 用真实时间戳：跨缺口那段是段内的 4 倍
8. 缺口断开：3 点 + 2 点两段，不跨过缺帧那一轮连线
9. 缺帧画灰块（`data-noframe` + 轮次时间），**不是** 0 帧的点
10. 整张总览里没有 `data-frames="0"`
11-13. 状态色：S412 绿（上升）、S102 红（下降）、S101 灰（无数据，且一个格点都不画）
14. 没配置站位的格子是空位（45 个），不冒充"有数据"
15-17. hover：一格给站位/框/格位；格点给轮次时间 + 帧数；灰块说清"无帧（不是 0 帧）"
18. 换轮次不重取矩阵（前后条数相等）
19-23. 契约：`/api/timeline` 三件套（fixture 字段 ⊆ 真契约、页面消费字段 ⊆ 契约、必需字段 ⊆ fixture）
      + 矩阵嵌套形状（`rounds[]` 带 `ts`/`round_id`/`day`，`nodes[]` 带 `layer`/`col`/`box_id`，
      `points[i]` 与 `rounds[i]` 等长）+ 矩阵格点里没有 0 帧

### 4.4 护栏自证（改的是页面**副本**，worktree 文件没动）

| 把实现改回哪一步 | 实测 |
| --- | --- |
| 缺口改成直连（`segs = [framed]`，一段画到底） | **+1 条红**：`缺口断开：…3 个点 + 2 个点…`；443/453 |
| `null` 当 0（缺帧变成 `n_frames:0` 的格点） | **+6 条红**：格点数 6/0 灰块、跨缺口间距比失真、缺口不断、
| | 灰块不见了、`data-frames="0"` 冒出来、灰块 hover 文案变成 0 帧；438/453 |
| 格数/层数死写（`cols=12, layers=3`） | **+5 条红**：格数 36 ≠ 48、顺序、同站同格位、S412 状态色、空位数；439/453 |
| 状态判据去掉（`overviewStatus` 恒 `flat`） | **+2 条红**：S412 该绿、S102 该红；442/453 |
| 矩阵退回带 `station_id` 的单站请求 | **+16 条红**：矩阵请求 0 条、格数仍有 48 但一个站位都对不上数据、
| | 状态全灰、hover 三处全空；428/453 |

### 4.5 边界与交接

- **console BFF 透传 `/api/timeline` 仍是票 07 的事**（`deploy/**` 不在本票写域）：上机前不补那条路由，
  页面拿到 404，会如实说「查不到时序总览 ⚠ …」（与票 02 同一条交接）。
- **矩阵格点是"每轮两件事"**（`has_frame`/`n_frames`）。P1 的 `growth`/`ok_rate`/`dup` 与票 05 的
  `room_band[]` 接进来时，加在 `rounds[]` 或格点上都可以——但**别再逐点重复** `ts`/`round_id`（体积立刻超）。
- `points[i]` 与 `rounds[i]` 的**对齐是契约**（用例与前端回归都钉着）：将来要按窗口分页/筛选，两边必须一起切。
- **顺带修的一处老毛病**（不在本票验收里，但不修本套件就跑不完）：`create_app` 每建一个应用就开一份
  存储连接、谁也不关——夹具只能等 GC 回收，于是**每条用例攒一份**。实测：改前连接数在 130s 内从 20 涨到 89，
  272 条用例把开发库 `max_connections=100` 撑满（尾部 14 条用例全是 "sorry, too many clients already"；
  262 条的基线只差几个连接就到顶）。修法是"谁建谁关"：`app.state.store_conn = conn`（api.py，只多一个引用）
  + `conftest.py` 的 `app` 夹具收尾时 `close()`。改后同一轮连接数全程在 **20–45** 之间平着走，272 条全绿。

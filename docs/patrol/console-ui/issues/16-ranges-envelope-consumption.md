# 16 · `/api/ranges` 改成信封后的页面消费（破坏性形状变更）

**Status:** implemented + **部署态已验证**（2026-10-14；`8002/api/ranges` 由 404 变 200，见 §6）
**归属：** `web/console/index.html` + `docs/patrol/console-ui/verify-page.js`（界面师）
**来源：** `docs/patrol/env-analysis/issues/05-ranges-rework.md` §5（环境官，票 05）
**前提（已满足）：** 发版官 2026-10-14 13:14 重建后**生产已生效**——`8002/api/ranges` 200 + 九键信封
（重建前是 **404**：`console.py` 当时还没有这条代理路由，我随后补上 `d9f5fdf`）。页面按
"**两版都能读、信封优先**"实现这条**继续保留**：旧 analysis 镜像/直连路径仍可能给裸列表。

## 1. 形状变更

```
旧：GET /api/ranges → [ row, row, … ]                       （裸列表）
新：GET /api/ranges → {ok, rows, count, state, state_text,
                       detail, computed_at, metric, unit}    （信封；行形状不变）
```

六态具名（`analysis/align.py:197-210` 的 `STATE_TEXT`，页面**照抄后端话术**，不自己拼）：
`ok` / `no_measurements` / `all_excluded` / `no_environment` / `no_growth_metric` /
`insufficient_samples`，外加存储层的 `never_computed`。

## 2. 页面做了什么（三件事，按 Chief 的三条）

1. **契约表按信封登记**：`CONTRACT['/api/ranges']` 九个字段（`ok/rows/count/state/state_text/
   detail/computed_at/metric/unit`），`required` 只要六个必有键；`PAGE_CONSUMES['/api/ranges']`
   同步登记（**不是只登记 `rows`**）。
2. **空态按 `state`/`state_text` 显示，六态各自具名**：页面**原样显示后端给的 `state` + `state_text`**，
   **绝不合并成「暂无数据」**——`insufficient_samples`（样本不足）与 `never_computed`（没重算过）
   在页面上是**两句不同的话**（"未知要说未知"）。
3. **排除可见**：`detail.excluded_by_reason`（按原因计数）+ `detail.excluded_samples`（前 20 条原文，
   带 `ts` / `zone` / `reason` / `window` / `detail`）+ `excluded_samples_truncated` 标记 +
   `n_kept`/`n_excluded`——"排除了多少"和"排掉了哪一段、凭什么"都能答。
   例：`已排除 3 条：legacy_excluded 1、suspect_round 2` + `2026-10-09T18:20:05 · 611 ·
   legacy_excluded · 2026-10-09T18:20:05~18:27:34 · 批准：Chief 2026-10-14`。

**旧形状怎么读**（`rangesEnvelope()`）：裸列表 ⇒ 只当"拿到了 N 行"，**没有状态字段** ⇒
页面如实说「旧版接口（裸列表、没有状态字段）：返回 N 行 —— **不知道**算没算过，别读成"样本不足"」
——**不许**把"不知道"说成"样本不足"（那正是这次变更要防的误读）。
取数失败（`ok:false` 或 HTTP 错）⇒「区间表取不到：<原因>」，与"取到了但是空的"分开。

**记账一条过期注释**：`web/console/index.html:937` 的注释写「**旧版线上还是裸列表**」——13:14 重建后
**这句已不成立**（生产是信封；裸列表只存在于 `analysis:8000` 直连/旧镜像）。**本轮刻意不改它**：
页面刚与仓库 HEAD 逐字节同步（sha `4f464986…`、4119 行，见 §6），为一个注释让发版官再 scp 一次不划算。
**下一批页面改动一并改**（行为无影响，只是注释别把人带偏）。

**取数位置**：环境模式的 `loadEnv()`（与批次对比并列）；渲染在批次卡（`#batchnote`）底部。
**没加 `?metric=` 这类查询参数**（量纲在行上，环境官有测试钉住别加回去）。

## 3. 可扫描断言（6 条，全在 `verify-page.js`）

1. 空态**原样显示** `state` + `state_text`（不合并成"暂无数据"）；
2. 空态**不说**"暂无数据/没有数据"（负向；只看区间表那一段——批次说明里本来就有"没有数据"这句）；
3. 排除计数可见（`legacy_excluded 1` / `suspect_round 2`）；
4. 排除**前 20 条原文**带窗口与依据（含 `批准：Chief 2026-10-14`）；
5. `never_computed` 与"样本不足"是**两句不同的话**；
6. 旧版裸列表能读、且**不冒充**"样本不足"；取不到时如实说原因。

## 4. 验证（实测）

```
node docs/patrol/console-ui/verify-page.js web/console/index.html
→ 390/399 通过，运行期错误：无
   失败 9 条与 HEAD 逐条相同（Compare-Object 比过清单，2026-10-14 13:28 复核）
```

**护栏自证 2 条**：① 把旧版裸列表当成"样本不足" ⇒ 「旧版裸列表…不冒充」红；
② 空态改成显示"暂无数据" ⇒ 「原样显示 state_text」红。

## 5. 不做什么

- 不改 `analysis/**`（过滤与状态机是环境官的活）；**不为它造数据**；
- 不把"没取到"（`undefined`/失败）与"取到了但空"混成一句；
- 不新增查询参数（量纲在行上）；
- 观感/信息层级不动（等用户一句具体诉求）。

## 6. 部署态验收（2026-10-14 13:14 真样本，界面师）

**样本**：`.scratch/deploy-acceptance/ranges-raw.json`（发版官导出，只读；`calls` 数组）。
**命令**：

```
NODE_PATH=<jsdom> node docs/patrol/console-ui/verify-deploy-samples.js \
    web/console/index.html .scratch/deploy-acceptance
→ 28 PASS / 0 FAIL（`/api/ranges` 那章从 **SKIP 转成 PASS**），exit 0
```

**真形状（逐字）**：

| call | 路径 | http | body |
| --- | --- | --- | --- |
| 0 | `8002/api/ranges`（nginx → console:8001 → analysis:8000，**页面同源**） | 200 | 九键信封：`state="no_measurements"`、`state_text="样本不足：库房里还没有任何测量行（测量管线未接上）"`、`rows:[]`、`count:0`、`detail={n_measurements:0, n_environment:2608, n_kept:0, n_excluded:0, excluded_by_reason:{}, min_samples_per_bin:3}`、`metric/unit=null`、`computed_at="2026-10-14T13:14:50"` |
| 1 | `8000/ranges`（**旧 analysis 直连**，不经 nginx） | 200 | 裸列表 `[]`（**历史抓取 11:34:51**，13:14 重建后已取不到） |

**一条别误判的事实**（Chief 转述发版官）：旧形状**只存在于 `analysis:8000`**；旧 console 上的
`8002/api/ranges` **从来是 404**（连裸列表都不是）⇒ 代理层"上游返回裸列表只补 `rows`/`count`、
不补 state"那条分支**只能按上游直连来验**，**不得写成"8002 曾经有过旧形状"**。

**本轮为真形状加固的三条钉子**（都喂真样本，都能红）：

1. **信封九键齐全**——`metric`/`unit` 在空态是 `null`，**"值为 null"≠"字段不在"**（缺字段会让页面
   读到 `undefined` 而静默降级）；并且**样本自述的 `envelope_keys` 必须与真身键集一致**（样本自己
   标错不能替真形状背锅）。
2. **`rows` 是 list**——打印改成 `list(array)`，不再印 `object`（那会被扫日志的人误读成坏值）。
3. **路径归属**——页面看到的是 `console_8002`，**直连 `analysis_8000` 的形状不能替页面那条路径背书**。
   来源优先级：call 的 `via` 字段 > call 的 `request` 里的端口 > 文档级 `provenance`（**仅当该 call
   自己没带端口**），**每条都在标签里写出用了哪个来源**，不做静默推断。

**护栏自证 7 条**（离线改副本，不动仓库样本；A–D 为形状，E–G 为路径）：

| # | 破坏 | 结果 |
| --- | --- | --- |
| A | 信封里删掉 `metric`（自述也同步删） | 红 2（九键 + 自述一致） |
| B | 只把自述 `envelope_keys` 里的 `unit` 删掉 | 红 1（自述 vs 真身） |
| C | 页面 `if(false && e.state)` 不显示 state | 红 1（原样显示 state/state_text） |
| D | `body.rows` 改成字符串 | 红 1（rows 是 list） |
| E | 文档 `provenance` 去掉端口 | 红 1（页面同源信封样本存在） |
| F | 直连那条 call 自述 `via="console_8002"`（与 URL 矛盾） | 红 1（自述 vs URL 端口） |
| G | 同源那条 call 自述 `via="analysis_8000"` | 红 1（页面同源信封样本存在） |

**这批样本仍然没验到的**（**照旧不写 PASS**）：

- **排除可见**：真样本 `excluded_by_reason: {}`、`n_excluded: 0` ⇒ 该分支**仍只由 `verify-page.js`
  的 fixture 覆盖**（票 16 §3 第 3/4/5 条）；
- **`insufficient_samples` 与 `never_computed` 是两句不同的话**：真样本只出现 `no_measurements`
  一态 ⇒ "六态各自具名"里只有一态有真数据背书，其余仍是 fixture；
- **`dup_groups` 非空**那条与本票无关，同 §3 的既有说明。

**给发版官的一条待补（确切字段名）**：每条 call 加显式 **`via`**，取值只允许
**`"console_8002"`**（nginx → console → analysis，页面同源）或 **`"analysis_8000"`**（直连）。
理由：本轮 `call[0]` 的 `request` 只有 `"GET /api/ranges"`、**没有主机端口**，路径归属只能从文档级
`provenance` 继承；补了 `via` 之后"页面同源"这条就从继承推断变成**逐条自述 + 可交叉核**（脚本会拿它
和 URL 里的端口对，不一致直接红）。**不补也能过**（现在就是从 provenance 继承、标签里写明了来源）。

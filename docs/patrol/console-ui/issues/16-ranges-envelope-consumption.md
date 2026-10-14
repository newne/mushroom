# 16 · `/api/ranges` 改成信封后的页面消费（破坏性形状变更）

**Status:** implemented（2026-10-14，Chief 转环境官 `4ae422c` 的破坏性变更后实现）
**归属：** `web/console/index.html` + `docs/patrol/console-ui/verify-page.js`（界面师）
**来源：** `docs/patrol/env-analysis/issues/05-ranges-rework.md` §5（环境官，票 05）
**前提：** 生产**还没生效**（要等发版官那次 analysis 重建把 `RANGES_AT` 并进去）⇒
线上仍是**旧形状**，所以页面按"**两版都能读、信封优先**"实现。

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
→ 388/397 通过，运行期错误：无（连跑 3 次同结果）
   失败 9 条与 HEAD 逐条相同（Compare-Object 比过清单）
```

**护栏自证 2 条**：① 把旧版裸列表当成"样本不足" ⇒ 「旧版裸列表…不冒充」红；
② 空态改成显示"暂无数据" ⇒ 「原样显示 state_text」红。

## 5. 不做什么

- 不改 `analysis/**`（过滤与状态机是环境官的活）；**不为它造数据**；
- 不把"没取到"（`undefined`/失败）与"取到了但空"混成一句；
- 不新增查询参数（量纲在行上）；
- 观感/信息层级不动（等用户一句具体诉求）。

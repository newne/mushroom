# 19. 环境页接「仅估算」态与未验证徽标

Status: implemented（2026-10-15，界面师；无头回归 417 条：新增 9 条全绿、失败清单与基线逐条同名）
归属：前端
依据：env-analysis 票 26 已交付的契约（ADR-0025）；docs/patrol/env-baseline/spec.md
依赖：env-analysis 26（**已合并到 feat/env-baseline**）；**与 console-ui 17/18 同为 web/console/** ⇒ 必须排在它们之后（17/18 已合）**

## 0. 为什么需要这张票

票 26 已让接口出独立态与标注，但**页面还看不见**：现有 ranges 行表只在 state 等于 ok 时才画行。
⇒ 只有估算行时（state=estimate_only），用户看到的是**一张空表**——而契约里明明有行、也有"未验证"标注。

## 1. 交付物

1. 接 `state="estimate_only"` 分支：**照常画行表**（不是空表）
2. 整维徽标：按 `flags` 里的 `unverified` 显示「**估算（未验证）**」
3. 行级可见：`tier` / `unverified` / `n_verified` / `n_estimate`（同箱两类计数**分开显示**）
4. 空态不退化：没有行时仍说清"为什么没有行"（沿用既有空态语言）

## 2. 验收（缺一条不算过）

- [x] `state=estimate_only` 时**能看见行**，每行带「估算（未验证）」标记
- [x] 同一分箱里已验证与估算的计数**分别可见**，不混成一个数
- [x] 估算行**不出现**在建议层相关区域
- [x] 不新增任何前端依赖（沿用既有手写内联 SVG / 纯 DOM 手法）
- [x] 无头回归加断言：把 estimate_only 分支去掉会红

## 3. 不做什么

- 不改接口与契约（票 26 已交付）
- 不把估算值显示成验证值，也不隐藏它的"未验证"标记

## 4. 实现记录（2026-10-15，界面师）

**改哪**（只在 `web/console/**` 与本票的无头回归；接口与契约一个字没动）：

| 文件 | 内容 |
| --- | --- |
| `web/console/index.html` | `rangesEnvelope` 读信封 `flags`；新 `rangeTierLabel(row)`；`rangesHtml` 按 `flags` 挂整维徽标、画行表的条件从 `state==='ok'` 放宽到 **`ok ｜ estimate_only`**、行表加「档 / 同箱已验证 / 同箱估算」三列；`.tag.warn` 一个样式档 |
| `docs/patrol/console-ui/verify-page.js` | 假后端补 `flags`/行字段/`estimate_only` 原文（`align.py` 的 `STATE_TEXT` 与 `STATE_TEXT_ESTIMATE_EMPTY`）；新增 **9 条**断言（见 §5）；契约表 `fields`/`PAGE_CONSUMES` 登记 `flags`/`tier`/`unverified`/`n_verified`/`n_estimate` |

**四条交付物各自落在哪**：

1. **照常画行表**：`e.ok && e.rows.length && (e.state === 'ok' || e.state === 'estimate_only')`
   —— 只有估算行时不再是一张空表。带行的状态只有这两个（`analysis/align.py` 的状态机），
   注释里写明"将来多一个带行的状态要在**这里显式**加名"，别又静默退化成空表。
2. **整维徽标**：`flags.includes('unverified')` ⇒ 行表**之外**挂
   `<span class="tag warn">估算（未验证）</span>`。它**跟 flags 走、不跟状态词走**：
   估算档卡在别的空态上（`align.py:410-411` 的 `no_growth_metric` + `["unverified"]`）
   照样要挂；一行都没有时也照挂（标注说的是"这份表是什么档次"，不是"有没有行"）。
3. **行级可见**：`档` 列 = `rangeTierLabel` —— `verified` ⇒「已验证」、`estimate` ⇒
   「估算（未验证）」、**没标过档** ⇒「未标档（按未验证算）」（fail-closed：不替它猜成已验证）、
   未知档值原样显示；`n_verified`/`n_estimate` **各占一列**（同箱已验证 / 同箱估算），
   绝不加成一个数。
4. **空态不退化**：`state_text` 原文照旧显示；没有行时**不画空表头**
   （`estimate_only` + 0 行时后端自己已在 `state_text` 里补「样本不足…暂不输出区间」）。

**§3 不做什么**：没动接口/契约与 `analysis/**`；估算值一律带「未验证」，没有一处当验证值显示；
没新增任何前端依赖（只在既有的内联 style/script 里加，无新 script src、无 @import）。

## 5. 验证（实测）

```
NODE_PATH=D:\code\energy-agent\console\node_modules \
  node docs/patrol/console-ui/verify-page.js <worktree>/web/console/index.html
→ 408/417 通过，运行期错误：无
   失败 9 条与基线（本票前 HEAD 3526125：399/408）**逐条同名**：全是"地图点击/站位"那一组
```

**新增 9 条断言**（全部 `票 19` 前缀）：

1. `estimate_only` **照常画行表**（2 行都在，不是空表）；
2. 每一行都带「估算（未验证）」（`["估算（未验证）","估算（未验证）"]`）；
3. 整维徽标按 `flags` 挂在**行表之外**（一处）；
4. 同一分箱两类计数**分开**（`同箱已验证 7` / `同箱估算 1`，不合并成一个数）；
5. 估算行**只出现在区间表那一段**：行里的 `9.87` 与「估算（未验证）」在页面别处
   （剪掉 `#batchnote` 与 script/style 后的全文）一个字都没有 ⇒ 不进建议层相关区域；
6. `ok` + `flags` 无 `unverified` ⇒ 行画「已验证」、**不挂**徽标（徽标不是"有行就标"）；
7. `estimate_only` 一行都没有 ⇒ 不画空表头、原样说清"为什么没有行"；
8. 一行都没有时徽标**照样**挂；
9. 空态带 `flags=["unverified"]`（`no_growth_metric`）**照样**挂徽标（跟 flags 走，不跟状态词走）。

**护栏自证**：把 `(e.state === 'ok' || e.state === 'estimate_only')` 改回 `(e.state === 'ok')`
⇒ **417 里红 3 条**（① 行表在不在 ② 每行标记 ③ 两类计数分开；`→ 405/417`）。
第 1 条报「没有行表」——正是本票要修的那张空表。徽标那几条**不红**：它按 `flags` 挂，
与画不画行表解耦（这也顺带证明了两件事各自可单独失败）。

**一条留给发版验收的提醒**：票 26 之后真实信封多了一个 `flags` 键（九键 → 十键），
`verify-deploy-samples.js` 里的 `NINE` 常量与该脚本比对的样本 `envelope_keys` 还是九键口径；
仓库里没有 `.scratch/deploy-acceptance/ranges-raw.json`（未跟踪目录只在主树），本票**没有动它** ——
下次跑部署态验收前要么重导样本、要么把 `NINE` 补成十键，别让旧样本替新形状背锅。

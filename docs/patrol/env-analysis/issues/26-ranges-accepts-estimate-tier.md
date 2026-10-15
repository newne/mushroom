# 26. 区间表接纳「估算」档

Status: implemented（2026-10-15，后端；页面上的「估算（未验证）」标注**归前端票**）
归属：后端
依据：`docs/patrol/env-baseline/spec.md`；D1；修正 ADR-0024 的「只吃已验证」
依赖：05（区间表重构）、算法侧临时序列票

## 1. 交付物

1. 区间表新增独立状态（「**仅估算**」），整维标注「**未验证**」
2. 估算行**不得与已验证行混算**（同一分箱里两类必须可分）
3. **建议层永不消费**估算行
4. 状态与 `flags` 沿用既有信封风格

## 2. 验收（缺一条不算过）

- [x] 有估算行时状态为「仅估算」，**不是** ok 也不是空
- [x] 建议层的输入里**不含**估算行（有断言）
- [x] 同一个分箱里两类行的计数分别可见
- [ ] 页面明确显示「估算（未验证）」 —— **归前端票**（写域不在本票；后端契约已给到，见 §4）

## 3. 不做什么

- 不改 `measurements` 的既有列语义
- 不放宽已验证的门槛
- 不因为「有数了」就取消空态表达

## 4. 实现记录（2026-10-15，环境官）

**改哪**（全在 `analysis/**`，前端页面的标注不在本票写域）：

| 文件 | 内容 |
| --- | --- |
| `analysis/src/analysis/evidence.py` | **档词汇的唯一出处**：`tier_of(quality)`（`verified` **优先**、其次 `estimate`、其余两档都不收）+ `TIER_VERIFIED` / `TIER_ESTIMATE` |
| `analysis/src/analysis/align.py` | `hourly_rates(tier=)`、`best_ranges(tier=, peers=)`（每箱 `n_verified`/`n_estimate`）、`compute_ranges` **两档各跑一遍**、新状态 `STATE_ESTIMATE_ONLY`、`RangesRun.flags`、建议层入口 `advice_input` / `assert_verified_only` / `EstimateTierLeak` |
| `analysis/src/analysis/db.py` | `ranges` 加 `tier`/`n_verified`/`n_estimate`、`ranges_state` 加 `flags`（**就地补列**，老行 NULL）、`fetch_ranges` 读侧派生 `unverified`、`fetch_advice_ranges` |
| `analysis/src/analysis/api.py` | `/ranges` 信封加 `flags`（整维标注）；docstring 补 `estimate_only` 与"建议层不许读这个响应" |

**ADR-0025 的四条边界各落在哪**：

1. **独立态**：只有估算档 ⇒ `state="estimate_only"` —— 不是 `ok`、也不是任何空态。
   **一条种子估算行**就成立：速率的**两侧**都要在，所以那一条不会有区间行，但状态与标注照样成立
   （`flags=["unverified","insufficient_samples"]`，`state_text` 里"为什么没行"照说）。
2. **不混算**：两档各自跑完整流水线；箱边界与均值只来自**主档**（有 `verified` 行就是它），
   另一档只**数**进 `n_verified`/`n_estimate`。用例拿**独立算得出来的判据**钉：
   7 条已验证（1.0 mm/h）+ 1 条估算（11.0 mm/h）同箱 ⇒ 混算的话均值会是 2.25、`n` 会是 8，
   实际断言 1.0 与 7。
3. **不进建议层**：`db.fetch_advice_ranges` / `align.advice_input` 是唯一入口，末尾
   `assert_verified_only` 是保险丝（**故意把筛选改宽 ⇒ 立刻 `EstimateTierLeak`**，已自证跑过一次）。
   仅估算时建议层的输入是**空**，不是"估算行也算"。
4. **整维标注**：信封 `flags=["unverified"]` + 行上 `tier`/`unverified`；`unverified` 由读侧派生，
   **没标过档的行按未验证算**（fail-closed）—— 老行宁可短暂标成未验证，也不许被当验证值显示。

**§3 不做什么**：三条都守住了 —— 只加了 `tier`/`flags`/`n_*` 与一个状态词，
`measurements` 的列语义、`verified` 的门槛、既有六个空态与 `state_text` 一个字没动。

**留给前端票**（本票只保证契约与状态）：`web/console/index.html` 的 `rangesHtml` 现在只在
`state === 'ok'` 时画行表 —— 请前端接 `state === 'estimate_only'` + `flags` 显示
「**估算（未验证）**」，并把行上的 `tier`/`n_verified`/`n_estimate` 一起呈现
（"同一分箱里两类计数分别可见"要在页面上真的看得见）。

**验收证据**：`analysis/tests/test_ranges_estimate_tier.py` **7 条**
（一条种子行、两档同箱计数、建议层输入与保险丝、端到端信封 ×3、老库就地补列 ×1）；
全量 `pytest analysis/tests` = **233 passed**（本轮前 226）。
命令（主树解释器不可用时的跑法：`uv run --no-project --offline` + PYTHONPATH 指本 worktree）：

```
uv run --no-project --offline --with pytest --with httpx --with pandas --with fastapi \
  pytest analysis/tests -q
```

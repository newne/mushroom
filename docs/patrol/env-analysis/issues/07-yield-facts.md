# 07 — 产量的权威口径与样本量核对

**Spec:** ../spec.md §2.5

**What to build:** 定义"最终产量"的取数口径并核对今天的可用样本量，把"产量缺失"变成显式状态而不是 0。端到端行为：能回答"现在有几个批次的产量是录全的"。

**Blocked by:** 无

**Status:** ready-for-agent（口径已定，数据现状已核实）

- [ ] 口径定案：该批次全部 `mushroom_batch_yield` 行的 `fresh_weight` 求和，还是取 `harvest_time` 那条
- [x] 核对结果（2026-10-12）：`mushroom_batch_yield` 26 行，**`fresh_weight` 全部为 NULL**，`max(stat_date) = 2026-09-30`。即**一个批次都没录过产量**
- [ ] 因此 v1 的产量验证列一律标"样本不足"，实现里不许把它当 0 参与统计
- [ ] 产量缺失一律标"缺失"，不许当 0
- [ ] 与 `decision_prompt.jinja:47` 的"产量不参与决策"对齐：本期产量只作验证列
- [ ] `dedupe_batch_yield.py` 按 `(room_id, in_date)` 去重，与表唯一键 `(room_id, in_date, stat_date)` 不一致，核对哪边对

## Comments

- 2026-10-12 写票。每日 05:00 的初始化任务只写占位行（`fresh_weight=None`），产量数字靠人工经 API 录入（`PUT /mushroom_batch_yield/rooms/{room_id}/batches/{in_date}`），所以"有没有产量"只能查库。

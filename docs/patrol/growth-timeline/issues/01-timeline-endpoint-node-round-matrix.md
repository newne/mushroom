# 01. `/timeline` 端点：每节点 × 每轮矩阵（双刻度、null 语义）

Status: ready-for-agent
归属：后端
依据：docs/patrol/growth-timeline/spec.md
依赖：—

## 交付物

1. `GET /timeline?zone=611&batch_id=611|2026-09-30&metric=coverage`
2. **矩阵**：本批全部轮次 × 48 个站位（站位为主轴，附 `box_id` 供既有「生长曲线」卡对接）
3. **双刻度**：`axis`（t0/t1/刻度点）+ 每个点带 `day`（由 `batch.entry_date` 派生）
4. `room_band`：每轮的中位与四分位（p25/p50/p75/n）
5. `notes[]`：数据侧说明（沿用既有 notes 手法）

## 验收（缺一条不算过）

- [ ] 每个站位每个轮次**有且只有一格**；某站位某轮没有帧 ⇒ `has_frame: false`，**不补行、不插值**
- [ ] `day` 由入库日派生：入库当天 = 第 0 天（与既有「生长天数」口径一致）
- [ ] **无测量行时 `growth` 必须是 `null`，不是 0**
- [ ] 批次不在台账里 ⇒ **明确空态 + notes**，不是 500、不是空数组了事
- [ ] 单次响应 ≤ 500 KB（48 站 × 本批天数 × 8 轮）
- [ ] **纯读**：不写任何库；不碰 TDengine（ADR-0026）

## 不做什么

- 不做 `metric=growth`（那是票 05 / P1）
- 不做分页（本期数据量不需要）
- 不改既有 `/growth`、`/ranges` 等端点

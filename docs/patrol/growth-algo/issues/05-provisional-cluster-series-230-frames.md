# 05. 临时序列：230 帧跑出现役簇像素量

Status: ready-for-agent
归属：算法
依据：`docs/patrol/growth-algo/spec.md`；会话决定 D20
依赖：04（轮次标识）、env-analysis 26（区间表接纳估算档）

## 1. 交付物

1. 用**现役 L1–L3**（**不需要模板**）离线跑本地已评估的 **230 帧**，产出锚点级**簇像素量**序列
2. 写入 `measurements`，带 `estimate` ＋ 证据列（票 20 形状）＋ 轮次标识
3. **不开启生产 `--measure`**

## 2. 验收（缺一条不算过）

- [ ] `measurements` **非零行**
- [ ] 每行带 `quality=estimate` 与证据列；不存在无标记的估算行
- [ ] `/ranges` 的 `state` 从 `no_measurements` 变为「仅估算」
- [ ] 重跑结果**确定性一致**

## 3. 不做什么

- **不**声称它等于真指标（它不是 `mean_profile_height_px`）
- 不开生产开关、不写毫米列
- 不出结论（只有 3 个日期，第 4 层才有 7 个时点）

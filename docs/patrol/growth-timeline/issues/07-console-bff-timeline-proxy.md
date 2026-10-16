# 07. console BFF 透传 `/api/timeline`（否则上机 404）

Status: ready-for-agent
归属：前端（Console BFF）
依据：票 02 §4.5；与 `/api/ranges` 的代理先例（提交 d9f5fdf「之前这条路由根本不存在」）
依赖：02

## 为什么需要它

票 02 打通了「端点 → 页面」，但**浏览器不直连 analysis**（ADR-0003：浏览器只与 console 一个 origin 通信）。
`deploy/src/deploy/console.py` 是**逐端点注册**、没有通配转发 ⇒ `GET /api/timeline` 在真实部署上会 **404**，
页面会**如实**显示"查不到覆盖轨迹 ⚠ …"（不会假装有数据，这点是对的，但对现场就是不可用）。

**没有这条代理，票 02 在机上就不可演示**——所以它不是"顺手补一条"，而是这一票的收尾。

## 交付物

1. `GET /api/timeline` → analysis 的 `/timeline`，**原样透传 query 与信封**（含 400 的错误体）
2. `deploy/tests/test_console.py` 照 `/api/ranges` 那条补一条代理用例

## 验收（缺一条不算过）

- [ ] 带 query 的 `/api/timeline?zone=&station_id=&metric=&batch_id=` 原样转发，**query 不被吞**
- [ ] **状态码与信封原样透传**：analysis 回 **400** ⇒ 页面也拿到 400 与同一份错误体（**不许改写成 200**）
- [ ] analysis 不可达时给**明确错误**，不挂起、不返回空信封冒充成功
- [ ] 代理用例进 `deploy/tests`，且**去掉这条路由会红**
- [ ] 不改 analysis 侧；不动既有代理

## 不做什么

- 不做缓存 / 不做重试策略（沿用既有代理的朴素做法）
- 不改 `/timeline` 的契约

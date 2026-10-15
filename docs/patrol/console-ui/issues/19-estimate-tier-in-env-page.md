# 19. 环境页接「仅估算」态与未验证徽标

Status: ready-for-agent
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

- [ ] `state=estimate_only` 时**能看见行**，每行带「估算（未验证）」标记
- [ ] 同一分箱里已验证与估算的计数**分别可见**，不混成一个数
- [ ] 估算行**不出现**在建议层相关区域
- [ ] 不新增任何前端依赖（沿用既有手写内联 SVG / 纯 DOM 手法）
- [ ] 无头回归加断言：把 estimate_only 分支去掉会红

## 3. 不做什么

- 不改接口与契约（票 26 已交付）
- 不把估算值显示成验证值，也不隐藏它的"未验证"标记

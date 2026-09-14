# 05 · 前端页面落地：实时 + 历史两模式

Status: ready-for-human

## 背景

交互与信息架构已在 `.scratch/console-ui/prototype.html`（单文件、mock 数据）中定稿并逐条
验证过（45 项断言）。本票把原型接到真实 `/api`：替换 mock 适配层，其余按原型。

## 目标

- 以原型为规格实现页面：顶栏（模式开关 + 状态药丸 + 连接指示）、左栏站位（按 `box_id`
  分组）、中栏主视区、右栏操作区、底栏事件日志。
- 实时模式：`/api/status` 1s 轮询；坐标软限位在**浏览器侧**拦截；`goto`/`home` 二次确认；
  急停常驻且不随滚动隐藏；运动期间控制类按钮禁用。
- 历史模式：站位 → 时间轴（倒序，按日期分组，失败帧标记）+ 大图缩放（1×/2×/4×，拖拽平移）
  + `box_id` 生长曲线（`mean_len_mm`/`mean_cap_mm`）；筛选：日期、角度档、轮次。
- 依赖 vendor 进仓库（`console/static/vendor/`），**不走 CDN**；图表可换 ECharts/Chart.js，
  原型里的手绘 SVG 可保留为无依赖兜底。
- 深链保留：`?mode=history&station=S05`。

## 验收

- [ ] 原型里的 45 项断言在接入真实 API 后仍全部通过（把 `verify.js` 的 mock 差异点改成契约断言）。
- [ ] 1280×800 平板触控可用：所有可点元素 ≥44px，无横向滚动。
- [ ] 断网/守护重启时页面给出可理解的降级提示，不出现"点了没反应"。
- [ ] 页面上任何位置都不出现 MinIO 凭据或直连 MinIO 的 URL。

## 依赖

- 01、02、03、04；原型与验收脚本 `../prototype.html`、`../verify.js`；设计 `../spec.md`

## 备注

`verify.js` 需要 jsdom；现场无网络时把 jsdom 也 vendor 进开发环境，或用真实浏览器手工回归。

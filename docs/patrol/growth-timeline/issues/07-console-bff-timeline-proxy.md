# 07. console BFF 透传 `/api/timeline`（否则上机 404）

Status: implemented（2026-10-16；验收逐条核过，提交 0dbe4fc）
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

- [x] 带 query 的 `/api/timeline?zone=&station_id=&metric=&batch_id=` 原样转发，**query 不被吞**
- [x] **状态码与信封原样透传**：analysis 回 **400** ⇒ 页面也拿到 400 与同一份错误体（**不许改写成 200**）
- [x] analysis 不可达时给**明确错误**，不挂起、不返回空信封冒充成功
- [x] 代理用例进 `deploy/tests`，且**去掉这条路由会红**
- [x] 不改 analysis 侧；不动既有代理

## 不做什么

- 不做缓存 / 不做重试策略（沿用既有代理的朴素做法）
- 不改 `/timeline` 的契约

## 4. 实现与验证（Lead 补记）

提交 `0dbe4fc`，分支 `feat/tl-07`。写域实际落在 4 个文件（票面列了 2 个）：

| 文件 | 内容 |
| --- | --- |
| `deploy/src/deploy/console.py` | 新路由 `GET /api/timeline` → analysis `/timeline`：query 用 `request.url.query` 原样贴；`JSONResponse(payload, status_code=status)` 原样透传状态码与信封；上游非 JSON ⇒ 状态码照抄 + 如实说"看不懂"；链路失败 ⇒ **502 + 原因**，不返回空 `nodes` 信封 |
| `deploy/src/deploy/transport.py` | **超出票面写域的第三个文件**（Lead 复核后接受）：加 `fetch()` → `(状态码, JSON|None)`，发请求收进 `_request()`，既有 `__call__` 语义一字未改。理由：`__call__` 把非 2xx 折成异常 ⇒ 代理层拿不到状态码，"照抄状态码"这条验收在旧接口上无解。属**加深模块**，非扩权 |
| `deploy/tests/test_console.py` | +5（照 `/api/ranges` 代理先例）：query 原样（含空值、`%7C` 不被打散、上游 URL 逐字断言）、400 与同一份错误体原样、不通 ⇒ 502 且 `nodes`/`axis` 都不在、上游非 JSON、无 transport |
| `deploy/tests/test_transport.py` | +3：`fetch` 不折 400；2xx 给体、非 JSON 给 `None`；链路故障仍抛 `TransportError` |

**实测**：`deploy/tests` 基线 337 → **345 passed**（Lead 在集成分支复跑同数）。真链路 E2E（真 `HttpxTransport` + 真 `http.server` + 真 app）：上游 400 ⇒ 回 400 与同一份体；上游收到的 path 里 `%7C` 原样；上游关掉 ⇒ 502 且无 `nodes` 键。
**护栏自证**：去掉路由 ⇒ 5 红；400 折成 200 ⇒ 1 红；吞 query ⇒ 1 红；假空信封 ⇒ 1 红；`fetch` 折非 2xx ⇒ 1 红。

# ADR-0001：巡检结果同步采用 at-least-once，prod 端以幂等写兜底

日期：2026-08-31
状态：已接受
背景：架构评审候选 #1（统一链路 seam）。`patrol.sync.SyncClient` 把 outbox 记录推送到
prod 的 `POST /ingest`；传输层可能重试（`patrol.links.RetryingTransport`），断线补传也会
重放历史批次。

## 决定

- 同步语义为 **at-least-once**：同一记录可能被推送多次。
- prod 端 `analysis.db` 的 `measurements` / `rounds` / `ranges` 表均以业务键为主键
  （如 `PRIMARY KEY (ts, box_id)`），写入用 `INSERT OR REPLACE`——重复推送覆盖同键行，
  不产生重复数据。
- 代价：同键但不同内容的行会"最后一次写入胜出"。当前测量行同键必同源（同一站位同一
  小时的聚合），可接受。

## 后果

- 部署侧可为 /ingest 传输自由套 `RetryingTransport`，无需请求去重。
- 若未来出现"同键需保留多版本"的需求（如同一小时多次补拍），须重新审视本 ADR
  （改用追加式事件表）。

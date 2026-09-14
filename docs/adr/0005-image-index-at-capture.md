# ADR-0005：图像索引在采图时落库，不事后扫描 MinIO

日期：2026-09-10
状态：已接受
背景：历史模式要"点击站位看该点位的历史图像"，但今天**不存在图像与站位的索引**：
`analysis` 的 `measurements` / `rounds` 表只存聚合数值，不含图像链接；图像仅以 MinIO 对象名
`{date}/{box_id}_{station_id}_{angle_profile}_{HHMMSS}.jpg` 存在。更关键的是，
`StationCapture.run` 返回的 `object_name` / `cloud_url` 在 `PatrolDaemon.run_cycle` 中被
**直接丢弃**，从未持久化。

## 决定

- 采图成功时**立即**写一条图像索引记录（站位、角度档、时间、对象名、质量/成败），而不是
  由前端或后端事后 list MinIO 前缀 + 正则解析 key 还原。
- 索引记录随 ADR-0001 的 outbox → `POST /ingest` 链路同步到 prod，保持 at-least-once 与
  幂等写（以 `(ts, object_name)` 之类的业务键为主键）。
- 图像索引与 `measurements`（毫米级聚合）是两类数据：**前者一帧一行，后者一站位一时段一行**，
  不合并成一张表。

## 决定理由

- 时间戳只存在于索引里才可检索：对象名只有 `HHMMSS`，不跨天就只能靠 list 全桶再解析，
  随着图像累积会越来越慢，而且无法按站位/日期建查询。
- 丢弃 `object_name` 已经发生过一次（当前 daemon 就在丢）；索引化把"这一帧存在过"变成
  可追溯事实，也让"图被删了但测量值还在"这类不一致可见。

## 后果

- **本 ADR 生效前采集的图像不会被索引**，只能靠对象名兜底解析；历史模式须明确区分
  "有索引"与"仅对象名可推"两种来源。
- MinIO 侧若有生命周期/清理策略，索引行可能指向已删除对象；查询端点须容忍 404 并标注。
- patrol 需要一次小改：把 `StationCapture.run` 的返回结果接到索引写入（今天在
  `orchestrator` / `daemon` 之间被丢弃）。

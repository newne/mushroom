# 03 · 图像索引落库：采图时写一行，随 outbox 同步 prod

Status: ready-for-agent

## 背景

`StationCapture.run` 已经产出 `object_name` / `cloud_url`，但 `PatrolDaemon.run_cycle`
把它们**直接丢弃**：今天没有任何"这一帧存在过"的记录，`measurements` 只有毫米级聚合。
历史模式因此无法按站位/时间检索图像（ADR-0005）。

## 目标

- patrol 本地 SQLite 建 `images` 表（`spec.md` §6 的 DDL）：一帧一行，
  主键 `(ts, object_name)`，含 `box_id/station_id/angle_profile/round_ts/ok/error`。
- 采图成功与**失败**都落一行（失败带 `error`），手动抓拍 `round_ts = NULL`。
- 新增 outbox 记录类型 `kind:"image"`，沿用 ADR-0001 的 at-least-once 推送。
- prod `analysis.db` 建同构表，`POST /ingest` 支持 `kind:"image"` 行，`INSERT OR REPLACE`。

## 验收

- [ ] 一轮巡检后本地 `images` 行数 = 站位×角度档数（含失败行）。
- [ ] `object_name` 与 MinIO 实际对象名逐字一致（含截图服务补的 `.jpg` 由 `GET /api/image` 处理）。
- [ ] 断网采图若干轮后恢复网络，prod 表最终与本地一致（重放不产生重复行）。
- [ ] 索引写入失败**不得**中断采图流程（只记告警）。

## 依赖

- ADR-0005、ADR-0001；设计 `../spec.md` §6

## 备注

- `object_name` 仍由 `patrol.stations.image_object_name` 生成，索引只记录、不改命名。
- 本票生效前采集的历史图像不会有索引行，只能靠对象名兜底解析（ADR-0005 后果）。

# 04 · 历史查询 API：合并双源 + 图像转发与缩略图缓存

Status: ready-for-agent

## 背景

历史模式要"点站位看该点位历史图像"。图像在 MinIO，浏览器既不该拿到凭据也不该直连
（ADR-0003）；刚采完的图还存在本地 outbox 未同步，只读 prod 会"查不到"（ADR-0006）。

## 目标

在 console 侧实现（形状见 `spec.md` §5.3）：

- `GET /api/stations`：站位表 + `last_capture_ts` + `image_count`。
- `GET /api/images?station_id=&angle=&date_from=&date_to=&round_ts=&limit=`
  → **合并**"本地未同步索引"与"prod 已同步索引"，每行带 `source: local|prod`。
- `GET /api/image?object=<object_name>[&w=320]`：从 MinIO 取流并转发；`w` 存在时生成缩略图。
- 缩略图按 `object_name` 落盘缓存（图像不可变，无需失效逻辑），LRU 上限
  `spec.md` 未决 #3 的默认值 1 万张。
- `GET /api/measurements`、`GET /api/rounds`：读 prod，供生长曲线与轮次刻度。

## 验收

- [ ] 刚在实时模式抓拍的那张，切到历史模式**立即**可见，且标 `本地待同步`。
- [ ] outbox 同步完成后同一帧变为 `prod`，不出现重复条目。
- [ ] MinIO 对象被删/不可达时，该行仍返回并标注不可用，页面不崩、不白屏。
- [ ] 100 帧的站位首屏加载中，页面只请求缩略图，主图按需加载。

## 依赖

- ADR-0003、ADR-0006、ADR-0005、03（索引表）
- 设计：`../spec.md` §5.3、§8

## 备注

`analysis/api.py` 已有 `/measurements`、`/rounds`、`/ranges`，可直接复用；本票只新增
`/images` 一类查询，不要把 MinIO 逻辑塞进 prod。

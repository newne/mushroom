# ADR-0020：批次的权威身份是（库房号, 入库日期）

日期：2026-10-12
状态：建议（随 `docs/patrol/env-analysis/spec.md` 一起评审）
相关：ADR-0011（装配与部署边界）、`prod-deploy/gap-list.md` §11.2/§11.7

## 背景

环境参数分析要回答的第一个问题是"当前批次是哪个"，而这个仓库里同一个批次有三种写法：

| 写法 | 出处 |
| --- | --- |
| `mogu-<mo_gu_batch.id>` | 巡检侧 `batch_no`；`deploy-fetch-room` 从生产库 `mo_gu_batch` 取 |
| `<room_id>_<%Y%m%d>` | 算法侧，`device_setpoint_changes.batch_id` 的注释就写着"批次ID (room_id+in_date)" |
| `<room_id>|<in_date>` | Streamlit 看板的 `batch_key` |

更麻烦的是"当前批次"由两套代码各算各的：巡检侧靠 `mo_gu_batch` 的入库日期（`deploy-fetch-room`），算法侧靠 `resolve_setpoint_batch_info`（先问 IoT，再用 `ImageTextQuality.in_date` 兜底）。批次切换当天，两者可以给出不同答案。

## 决定

1. 权威身份 = **（库房号, 入库日期）**。库房号取 `mo_gu_batch.code_num`，入库日期取 `in_time` 的日期部分。
2. 三种字符串写法都保留，但一律是**派生显示名**，不作输入。需要字符串时用 `<room_id>_<in_date>`，并在写它的地方注明派生自哪一对。
3. 「当前批次」= 该库房最近一条入库日期不晚于今天的批次。这是巡检侧现有语义，`deploy-fetch-room` 已实现，不新造。
4. 新增的跨表聚合（环境窗口、产量、区间表）一律按这一对 join。

## 后果

- 环境页的批次选择器只有一个真值来源，切批次时环境窗口与产量列不会各说各话。
- 算法侧 `batch_id` 与看板 `batch_key` 不动。改它们的收益小于风险，但从今天起它们只用于展示与既有存储。
- 巡检侧不受影响：`batch_no` 本来就是 `mo_gu_batch` 的派生。
- 旧代码里的 `query_batch=True` 是"分批查询"，与本 ADR 的"批次"无关。这两件事在本仓库里重名，写文档时必须区分。

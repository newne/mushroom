# 04 — 生长天数分档与测量对齐

**Spec:** ../spec.md §2.3、§2.4

**What to build:** 把巡检测量（`measurements`）与环境序列按小时对齐，并按生长天数分档打标。端到端行为：给定批次，得到"每档的生长速率 + 环境均值"一张表。

**Blocked by:** 02

**Status:** ready-for-agent

- [ ] 分档主键 `D1-D7 / D8-D14 / D15-D21 / D22-D28 / D29+`，与 `control_ops_dashboard/data.py:211` 同源，不另立一套
- [ ] 天数两套口径并列（我们 = 入库当天 0，生产系统 = 入库当天 01），话术照抄 `patrol.room.RoomState.verdict()`
- [ ] `mean_len_mm` 作档内连续协变量，不参与分组
- [ ] 图像 4 阶段作叠加标签，缺就留空，不猜
- [ ] 单测：合成 48h 数据验证对齐与分档边界（D7/D8、D28/D29）

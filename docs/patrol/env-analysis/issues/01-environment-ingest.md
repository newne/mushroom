# 01 — 环境数据入库：TDengine → `environment` 表

**Spec:** ../spec.md §2.1、§7

**What to build:** 把 TDengine `data1` 四个库房的温湿度与 CO₂ 时序拉进 `analysis.environment`，增量游标 + 幂等 upsert，断档可见。端到端行为：跑一次 `--once`，能从"库房 611 + 批次窗口"查到数。

**Blocked by:** 无（2026-10-12 已上机确认 TDengine 直读可用）

**Status:** resolved（2026-10-12 上机）

- [ ] TDengine REST（容器 `tools_taos`，`:6041`，库 `data1`）的连接与查询封装；表名大小写敏感，要反引号
- [ ] 设备到库房的映射：`SUP_<deviceCode>` 子表 ↔ `device_register_ledger.device_code` ↔ 库房号
- [ ] 点位表复用 `src/configs/monitoring_points_config.json` 与 `static_config.json` 里已登记的系统标识符（如 `TD1_Q1MDTM01::Tatmosp`），不新造一套名字
- [ ] 写入 `analysis.environment`，`zone` = 库房号，不用自由文本（`"default"` 只允许出现在 CSV 演练里）
- [ ] 增量游标 + 主键 `(ts, zone)` 幂等：重复跑不产生重复行
- [ ] `light_lux` 一律写 NULL，不写 0
- [ ] 断档不插值
- [ ] 单测：合成源验证增量、去重、字段缺失；真机跑一次 `--once` 核对行数

## Comments

- 2026-10-12 上机：`analysis/src/analysis/env/tdengine.py` 落地（TDengine REST + 反引号标识符 + UTC→本地 + 补光状态按分钟前向填充）。**落库口径按 ADR-0023 改为小时聚合**：四个库房各 289 行、`n` 合计 17,279 = 该批次采样点数。入库服务 `mushroom-env-ingest.service` 每 5 分钟一轮，游标退到整点重算当前小时。文件 SHA256 与本地逐一核对过。

- 2026-10-12 写票。
- 2026-10-12 上机核实（已作废上面那条担心）：TDengine 是活的，四个库房环境表最后一行都是 `2026-10-12T08:06:54Z`，611 库 192,284 行；REST 可用（`curl -u root:taosdata -d "<sql>" http://127.0.0.1:6041/rest/sql`）。`analysis.environment` 现在是 0 行，缺的正是这一票。设备号 ↔ 库房：`TD1_Q1..Q4` = 611/612/607/608。
- 2026-10-12 注意：库里已有 `environment` 表结构与 `upsert_environment`，本票只补取数与调度，不改表结构。

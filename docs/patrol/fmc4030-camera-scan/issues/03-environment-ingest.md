# 03 — 环控系统读取入库（environment 表）

**Spec:** ../spec.md §2.3、§6

**What to build:** 与现场确认现有环控系统的读取方式（HTTP API / Modbus / 数据库直读），实现定时拉取温湿度、CO₂、光照并写入 environment 表；prod 可直连则拉取方在 prod，否则由库房主机代读、随测量结果上报。端到端行为：查表能看到滚动更新的库房环境数据。

**Blocked by:** 00 — 仓库治理：git 化、目录重组、workspace 骨架（代码落位在 `analysis/env_ingest.py`，先有包骨架）

**Status:** ready-for-agent

- [ ] 现场确认读取接口形式并记录（方式、采样频点、字段与单位）
- [ ] environment 表落地（ts/zone/temp_c/rh_pct/co2_ppm/light_lux，按 spec §6 结构）
- [ ] 定时拉取连续运行 ≥24h 无缺档（网络抖动自动重试）
- [ ] 拉取方部署位置（prod 直连 vs 主机代读）定案并实现

## Comments

- 2026-08-30 代码完成（commit `2f92cbb`）：`analysis/db.py`（environment/measurements/rounds schema）、`analysis/env/adapters.py`（CSV 适配器可用；HTTP 源只含解析逻辑，请求函数 `fetch_fn` 待现场接口确认后注入）、`analysis/env/ingest.py`（增量游标拉取 + CLI，`--once`/`--loop`），8 项单测（去重/增量/解析）。
- **待现场**：确认环控系统接口形式（API/Modbus/直读）→ 实现 fetch_fn 或换 adapter；≥24h 连续拉取；部署位置定案。

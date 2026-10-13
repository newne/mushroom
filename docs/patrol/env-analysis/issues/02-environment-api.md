# 02 — `GET /environment` 与聚合口径

**Spec:** ../spec.md §4

**What to build:** analysis 侧新增只读接口，把 `environment` 表按时间窗与 zone 取出来，支持 `raw / hour / day` 三档粒度，另给一份 summary。端到端行为：console 能拿到当前批次窗口的四维序列与统计量。

**Blocked by:** 01

**Status:** resolved（2026-10-12 上机）

- [ ] `GET /environment?zone=&start=&end=&granularity=raw|hour|day`
- [ ] `GET /environment/summary?zone=&start=&end=` → 每参数 min/median/max/q25/q75/count + 断档点列表
- [ ] 整列无源时返回 `null`，响应里标 `sources_missing`
- [ ] 空窗口返回空数组，不返回伪造的 0
- [ ] 路由加在 `analysis/src/analysis/api.py` 的 `create_app` 里（闭包路由，没有注册表）
- [x] 单测补进 `analysis/tests/test_env_api.py`（含 raw 现取与窗口上限）

## Comments

- 2026-10-12 上机：`GET /environment`（hour/day 读小时聚合；raw 现取 TDengine、窗口上限 6 小时）与 `/environment/summary` 随 analysis 服务发布；console 侧 `/api/environment*` 反代已通（页面 origin 8002 实测可取数）。

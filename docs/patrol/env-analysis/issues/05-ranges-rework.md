# 05 — 区间表改造：两张表、参数集、分层、产量验证列、调度

**Spec:** ../spec.md §5

**What to build:** `ranges` 出两张表：**实际参数区间**（主）与**设定区间**（次），按同一分层聚合，都带产量验证列，并把 `refresh_ranges` 接进每日批处理。端到端行为：跑一次批处理，`GET /ranges` 同时返回两张表。

**Blocked by:** 03、04、07、09、**11**（硬阻塞）、measurements 管线

> 现场核实（2026-10-12）：`analysis.measurements` 是 **0 行**（`PatrolDaemon(measure_fn=None)`，检出模型未到位），`ranges` 也是 0 行。区间分析的生长速率输入不存在，本票在测量管线接上之前只能做出空表。

> **硬阻塞补记（2026-10-13，Chief 批）：** 本票现在依赖测量侧的 `measurements.room_id`（票 11）。
> `analysis/src/analysis/align.py:40-41` 按 `hour` 单键聚合环境，而 `measurements` 原先没有任何
> 库房维度 ⇒ 四个库房会被混进同一个分箱，本票做出来的是**错表，不是空表**。票 11 关掉之前不开工。

> **口径变更（2026-10-13，票 11 §9）：** 平视单档下 `mean_len_mm`（杆长）**明确缺测**，
> 所以"生长速率"**不能再用杆长差分**（`align.hourly_rates` 现在吃 `mean_len_mm`，
> 平视下会全空）。改用**菌簇范围**的小时增量：`mean_area_mm2`（mm²/h）或
> `mean_projected_d_mm`（mm/h），见票 11 §1 新增的四列。

> **量纲铁律（Chief 2026-10-13 加，硬要求）：** "面积/小时"和"毫米/小时"**不能共用一个字段名**。
> `ranges` 加三列，缺一不可：
> - `metric TEXT` —— 速率来自哪个测量列（`mean_area_mm2` / `mean_projected_d_mm` / …），
>   **由它唯一决定量纲**；
> - `unit TEXT` —— 显式单位（`mm2/h` / `mm/h`）；
> - `rate_per_h REAL` —— 速率数值。
>
> 旧的 `rate_mm_per_h` **保留兼容**，但**只在 `unit='mm/h'` 时等于 `rate_per_h`，否则必须为 NULL**
> （不许把 mm²/h 塞进一个叫 `_mm_` 的字段）。
> **区间表与建议层引用速率时必须显示量纲**（写"每天 +320 mm²"或"每天 +1.2 mm"，
> 不许只写"每天长 320"）——否则会被读成毫米。`CONTEXT.md` 的"长势"词条已按同一口径改，
> 本票与它对齐。

**Status:** in-progress —— **量纲三列 + 空态标注 + 每日重算接线已落地**（§5，2026-10-14）；
两张表（`kind`）、D 分档、产量列仍待做。

- [ ] 实际参数区间（主）：按 D 分档（可切图像 4 阶段）× 长势分层，统计 `temp_c / rh_pct / co2_ppm / lamp_on_min` 的 median / q25 / q75 / n，并列该层生长速率
- [ ] 设定区间（次）：同一分层下设定值的分布 + 该层变更汇总（频次、幅度、主要点位，数据来自 09 号票）
- [ ] 表结构上分得开：`ranges` 加一个 `kind`（`measured` / `setpoint`）列，或另起一张表，二选一由实现定
- [ ] 两张表都加 `yield_median`、`yield_n`，按（库房号, 入库日期）聚合（ADR-0020）
- [ ] 样本不足的箱继续按 `MIN_SAMPLES_PER_BIN` 过滤，不输出区间
- [ ] `refresh_ranges` 接线：接进既有每日任务链
- [ ] `GET /ranges` 支持按 kind 取；旧字段保留兼容
- [ ] 单测：现有 6 项 + 分层口径 + 产量列
- [ ] **速率的两侧都必须是 `verified` 且相邻整点**（Chief 2026-10-13 前向要求）：任一侧缺测、
      或被 `low_confidence`/`no_light` 等过滤 ⇒ 该段**不产速率**，标"样本不足"；
      **绝不允许跨小时用单侧差分凑一个数**（`groupby.diff()` 会把"2 小时的增长"算成"每小时"）。
      已实现：`align.hourly_rates` 只在相邻整点间差分，测试 `test_rate_needs_two_verified_neighbours`
- [ ] **`low_confidence` 等非 `verified` 行绝不被区间表或建议层当 `verified` 用**
      （整簇不可分时那面积/投影径是**估出来的**）：`align.hourly_rates` 的
      `verified_only=True` 是唯一入口，任何绕过它的读取路径都要在评审时挡掉
- [ ] 引用速率处**同时**给 `metric` 与 `unit`（量纲显示），`rate_mm_per_h` 仅在 `unit='mm/h'` 时非空

## 5. 空态标注 + 每日重算接线（2026-10-14 落地）

**问题**：`ranges` 长期 0 行，而"0 行"至少对应**六件不同的事**：从没重算过（接线问题）、
没有测量行、全被排除、没有环境数据、**平视下没有可用长势量**、样本不足。
以前 `GET /ranges` 只返回 `[]`，页面只能说"暂无数据" —— 把接线问题和数据问题混成同一个沉默的空，
和"未知字段被静默忽略"是同一类错误。

**实现**（`analysis/**`，全在环境官领地）：

1. **状态机**（`align.py`）：`compute_ranges(db_path)` 返回 `RangesRun(rows, state, state_text, detail)`，
   六个具名状态 `ok` / `no_measurements` / `all_excluded` / `no_environment` /
   `no_growth_metric` / `insufficient_samples`（+ 存储层的 `never_computed`）。
   每一步的空都带**计数**（`n_measurements` / `n_kept` / `n_excluded` / `n_environment` /
   `n_rate_rows` / `n_aligned_hours` / `min_samples_per_bin`）。
2. **排除可见**（票 17 的硬要求）：`detail.excluded_by_reason` 按原因计数，
   `detail.excluded_samples` 给前 20 条原文（`ts`/`zone`/`window`/人话 `detail`）。
   10-09 那轮命中时**现场证据**：`{"legacy_excluded": 1}`，窗口
   `2026-10-09T18:20:02~2026-10-09T18:28:00`，明细里带"批准：Chief 2026-10-14"
   （窗口两端 2026-10-14 更正过，见票 17：右端必须含轮行 `ts`）。
3. **状态落库**（`db.py` 新表 `ranges_state`，单行、只记最近一次重算）：
   `refresh_ranges` 在**同一处**写区间行与状态，避免"有 12 行却写着样本不足"。
   读到库是空的时候也返回 `never_computed` 一条，调用方形状恒定。
4. **每日重算接线**（票 12 的同一套机制）：`analysis/jobs.py` 新增 `RANGES_AT`（设了才跑，
   `daily_at`），与 env/audit/batch 同容器、同 lifespan —— 本票的"`refresh_ranges` 全仓库只有
   测试调用"到此结束。**生产要生效还需 compose 加一行 `RANGES_AT`**（`docker/` 不属我领地，
   已请 Chief/发版官裁定；compose 是创建时注入，改了就重建容器）。
5. **`GET /ranges` 响应形状变更（破坏性，需界面师同步）**：裸列表 → 信封
   `{ok, rows, count, state, state_text, detail, computed_at, metric, unit}`，与 `/operations`
   风格一致。裸列表**表达不了空态**，这是换形状的唯一理由；`rows` 里的行形状没变
   （仍带 `metric`/`unit`/`rate_per_h`）。
   **不加** `?metric=` 之类没人要的查询参数（行上带量纲就够了）—— 有用例钉住"别再加回去"。
   ⚠️ `docs/patrol/env-analysis/spec.md:194`（`ranges` / `GET /ranges` | 0 行 / `[]`）**还没改**，
   该文件不在我领地，已报 Chief 指派更新。
6. **未做**（本票剩余）：两张表（`measured`/`setpoint` 的 `kind`）、D 分档、产量列
   （`yield_median`/`yield_n`）—— 本期只交付"量纲三列 + 空态 + 调度"，其余按原计划。

**验收证据**：`analysis/tests/test_ranges_state.py`（9 条：六态各一条 + 排除可见 +
状态随数据翻转）、`test_api.py` 三条、`test_jobs.py` 装配断言；
全量 `pytest analysis/tests` **176 passed**（本轮前 165）。
可复验脚本（把六态信封原样打出来）：`ranges_states_demo.py`（在环境官工作目录，不在仓库）。

## Comments

- 2026-10-12 写票。`refresh_ranges` 今天没有任何调度、CLI 或部署接线（全仓库只有测试调用），所以界面上的参考带在接线完成前一定是空的。这一票不做，06 的参考带就是假功能。
- 2026-10-12 修订：主表是**实际参数区间**，不是设定区间（ADR-0022）。设定区间是次表，用来回答"这个背景下通常怎么调"，不能拿去下发——实际参数不可直接下发，那是个陷阱，代码注释里写明。

# 16. legacy 单杆字段退出活跃口径 + 区域指标降为辅助（消费侧清查）

Status: implemented（产出侧，2026-10-20）；消费侧（`analysis/growth.py`、`web/console`）待跨团队改
归属：算法（产出侧）+ 后端（`analysis/**`）+ 前端（`web/console/**`）
写域：`measure/**`、`analysis/**`、`web/console/**`、`analysis/tests/**` ＋本票
依据：`docs/patrol/growth-algo/spec.md` §4.7（legacy）、§4.2（区域指标为辅助，且须去袋）、
§9.4（像素值不写毫米列）、票 01 §5.1（V1 作废）
依赖：与 04（record 字段）、05（临时序列写库）协调写域；**本票不改 DDL**

## 1. 交付物

1. **字段分级登记表**（唯一落点，代码里可导入）：`active` / `auxiliary` / `legacy`。
   `legacy = {mean_len_mm, p10_len, p90_len, StipeEstimate}`（§4.7 原文四项）。
2. 产出侧：`measure/record.py` 的四个 legacy 列**仍保留列位**（历史兼容），但新行恒 `NULL`，
   且 `estimated_fields` 不再包含它们；`quality` 不以它们为可用性前提。
3. 消费侧①（后端）：`analysis/src/analysis/growth.py` 的 `GROWTH_METRICS` 移除
   `mean_len_mm` 的**首选优先级**（当前它是第一位），改为"只在历史行回读时可见，不参与新比较"。
4. 消费侧②（前端）：`web/console/index.html` 的 `DELIVER_KEYS` 不再把
   `mean_cluster_h_mm`/`mean_cluster_w_mm` 当"一期交付量"；区域指标标成**辅助（未去袋时不可当簇高）**，
   页面在无基线时显示明确的「暂无基线高度」状态，而不是拿区域高度顶替。
5. 两条新断言（§9.4 需要回写）：
   `no_legacy_field_in_active_output()`、`no_region_height_as_cluster_height()`。

## 2. 分级表（写死）

| 字段 | 级别 | 处置 | 理由 |
| --- | --- | --- | --- |
| `mean_len_mm` | **legacy** | 新行 NULL；不进曲线、不进页面、不进 `estimated_fields` | 单档平视不可测；spec §4.7 |
| `p10_len` / `p90_len` | **legacy** | 同上 | 同上 |
| `StipeEstimate` | **legacy** | 类型保留、退出活跃字段；不再参与聚合 | 同上 |
| `mean_cap_mm` | auxiliary | 仅帽级支线；无合格帽 ⇒ NULL | §4.7 Level 2 |
| `mean_cluster_w_mm` / `mean_cluster_h_mm` / `mean_area_mm2` / `mean_projected_d_mm` | **auxiliary（降级）** | 只作形态摘要；**未去袋不得当簇高** | §4.2 硬前提 |
| `H_profile` / `H_p50` / `H_p90` / `rel_*` | active（离线证据） | 主指标；DDL 未批前只在 JSONL | 票 13/15 |

**"legacy" 与 "auxiliary" 不是一回事**：前者是**退出**（代码与历史行保留），
后者是**仍产出但不作交付口径**。页面/文档不许把两者合成"不可用"。

## 3. 验收（缺一条不算过）

- [ ] `measure` 新产出的行：四个 legacy 列全 `None`，且 `estimated_fields` 中不出现它们
      （逐列断言，不是抽查）
- [ ] 历史行回读：带 `mean_len_mm` 的旧行仍能解析、不抛错（向后兼容用例）
- [ ] `analysis`：`GROWTH_METRICS` 不含 `mean_len_mm` 作为首选；用旧行构造 `compare()` 时，
      结论是 `not_comparable` 或落到真正的共同量，**不得**因为 `mean_len_mm` 有值就选它
- [ ] `web/console`：`DELIVER_KEYS` 变更后，页面文案不再出现「一期交付量 = 菌簇长度」；
      无基线时显示「暂无基线高度」，不显示 0（可 grep 断言 + 页面快照）
- [ ] 新增断言 `no_region_height_as_cluster_height()`：`cluster_h_px`（或 `*_h_mm`）
      不得出现在"簇高/高度曲线"的字段解析路径里
- [ ] `measure/tests`、`analysis/tests` 全绿；不改 DDL、不迁移历史数据
- [ ] 写域协调记录：04/05 的字段清单若同时改动，合并顺序写进 Comments（避免两侧字段清单漂移）

## 4. 不做什么

- **不删代码、不删列、不迁移历史行**（§4.7 明写"代码与历史记录保留"）
- 不把 `H_*` 写进 `measurements`（DDL 另行评审；本票只做退出与消费侧）
- 不改 `analysis` 的区间表/建议层判定逻辑（那是"verified vs usable"的另一条线，G5）
- 不动 `patrol/`、`deploy/`、`docker/`

## 5. 复跑

```bash
uv run --no-project --offline --with pytest --with numpy --with pillow \
  pytest measure/tests/test_record.py measure/tests/test_contract.py -q
uv run --no-project --offline --with pytest --with numpy --with pillow --with fastapi \
  pytest analysis/tests/test_growth.py analysis/tests/test_measurements_contract.py -q
```

## Comments

- 2026-10-20 开票：v6 把四个单杆字段标 legacy，但**消费侧仍在用**——
  `analysis/growth.py::GROWTH_METRICS` 的第一位就是 `mean_len_mm`，
  `web/console` 把 `mean_cluster_h_mm` 当一期交付量。只要这两处不改，"退出活跃字段"就只是文档。

### 实现证据（2026-10-20，产出侧）

- 落点：`measure/src/measure/contract.py`（`LEGACY_FIELDS` / `AUXILIARY_FIELDS` / `ACTIVE_FIELDS` /
  `FIELD_TIERS` / `field_tier()` / `without_legacy_fields()`）；
  `measure/src/measure/record.py::from_box_stats` 新行恒 NULL + `estimated_fields` 过滤。
- 单测：`measure/tests/test_legacy_fields.py` **6 passed**；与 `test_record.py`、`test_contract.py`
  合计 **24 passed**（`PYTHONPATH=measure/src;analysis/src`）。
- 已钉住：四个 legacy 字段分级、`estimated_fields` 剔 legacy、新行三列 NULL、辅助列仍照发、
  历史行直接构造仍保留旧值（读兼容）。
- review 修复：`FIELD_TIERS` 的键统一为 snake_case（`h_profile`），spec 的 `H_profile`
  经别名归一；像素辅助量与 mm 辅助量分开（`auxiliary_px`）；补两条可执行断言
  `assert_no_legacy_field_in_active_output()` 与 `assert_not_region_height_as_cluster_height()`。
- **消费侧未改**（`analysis/src/analysis/growth.py::GROWTH_METRICS`、
  `web/console/index.html::DELIVER_KEYS`）：这两处分别归环境官与前端，
  且 `analysis/tests` 有 20+ 条用例把 `mean_len_mm` 当长势量写死；改动会连带它们一起改。
  需要跨团队单独一次变更，**不要**在本产出侧票里顺手改掉。

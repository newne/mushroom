# 11 — measurements 表结构与写入契约（生产者 measure ↔ 消费 analysis）

**Spec:** ../spec.md §2.2、§2.7、§5；ADR-0020（批次身份）、ADR-0021（实际参数口径）

**归属：** 生产者（发送方）= 测量师，落 `measure/**`；存储与迁移 = 环境官，落 `analysis/**`。
字段名的**唯一钉点**是 `measure/src/measure/record.py:18` 的 `RECORD_FIELDS`（架构评审 #3）；
analysis 侧只消费，不复制字段表（现在 `analysis/src/analysis/db.py:327` 的 `MEASUREMENT_FIELDS`
是第二份副本，由环境官收敛）。

**Blocked by:** 无

**Status:** ready-for-agent

**What to build:** 把 `measurements` 的线上格式补齐到"能被区间分析正确消费"的程度 ——
加库房/站位归属、定 `quality` 枚举、钉幂等主键、写明空值语义。端到端行为：
测量师发一条 outbox 行 → `POST /ingest` → `analysis.measurements` 一行 →
`align.hourly_rates` 能按库房算出生长速率而不跨库房混算。

## 1. 字段表（线格式）

| 字段 | 现状 | 要求 | 说明 |
| --- | --- | --- | --- |
| `ts` | 有 | 必填 | 采图/测量时刻，本地 ISO8601，与 `rounds.ts`/`images.ts` 同口径 |
| `room_id` | **缺** | **必填（新增）** | 库房号 611/612/607/608。`environment.zone` 是同一个东西，**不造第三个名字** |
| `station_id` | **缺** | **建议新增** | 站位号（`S105`，CONTEXT.md:12 —— 一个框的一个拍照位）。`images` 主键是 `(ts, station_id, angle_profile)`，将来一定要 join |
| `box_id` | 有 | 必填 | **菇框号**（`B105` = 第 1 层第 5 框，CONTEXT.md:11）。**契约里写"框号"，不许写成站位号** |
| `angle_profile` | **缺（若同站位有多角度档）** | 同站位多角度档时必填 | 否则同 (ts, 站位) 的多档会互相覆盖；当前单档为 `level0`（CONTEXT.md:20） |
| `n` | 有 | 必填 | 本行统计的有效检出数；`n=0` 与"没写行"含义不同 |
| `mean_len_mm` / `mean_cap_mm` | 有 | 无检出时 **NULL** | 见 §5；平视单档下这两列**明确缺测**，见 §9 |
| `p10_len` / `p90_len` | 有 | 可空 | 分位数，样本太小时允许 NULL |
| `quality` | 有（自由文本） | 必填，枚举见 §3 | |

**`room_id` 由生产者填**：他手上有 round / room 上下文。
**不许 analysis 侧从 `box_id` 猜库房** —— 今天没有权威的 `box_id → room_id` 映射源，
现造一个就是把猜出来的归属写进区间表。

**关联口径（Chief 2026-10-13 定）：** 库里一律用 **`(room_id, box_id)` + `ts`** 关联环境、
轮次与测量，**不要依赖"每框 1 站位 1 角度档"的 1:1 假设** —— 那只是当前现场形态
（CONTEXT.md:12/20 都只写"当前"）。`box_id`（框）与 `station_id`（站位）不可以互换书写。

**命名与单位铁律：** `RECORD_FIELDS`（measure 侧）与 `MEASUREMENT_FIELDS`（analysis 侧）
必须**一字不差**，禁第四套命名；长度一律 **mm**；缺失一律 **NULL**；
**失败帧不产行**（不是产一行 0）。

## 2. 主键与幂等

- 存储主键至少 `(ts, room_id, box_id)`；同站位存在多角度档时**必须**含 `angle_profile`。
  理由：两个库房如果出现同一个 `box_id`（框号是库房内编号），`(ts, box_id)` 会让它们互相覆盖。
- 重发同一键 = 覆盖（`INSERT OR REPLACE`），不翻倍、不新增历史版本。
- 生产者不预聚合：一轮一站位一框一行是正常粒度，按小时的合并由 analysis 侧做。

## 3. `quality` 枚举（登记在契约里，不是自由文本）

| 值 | 含义 | 进区间表？ |
| --- | --- | --- |
| `verified` | 检出可信，数值可用于分析 | **是** |
| `no_detection` | 本行无有效检出，长度列全 NULL | 否 |
| `baseline` | 基线/定标帧，不是生长测量 | 否 |
| `cap_level_approx` | 菌盖高度为近似值 | 否 |
| `stem_absent(level0)` | 菌柄缺失（level 0），长度无意义 | 否 |
| `low_confidence` | 置信度不足 | 否 |

判定规则：**区间表与决策层只吃 `quality == "verified"`**，其余行落在库里但不参与统计
（不是删掉 —— 缺失要可审计，`spec.md` §6）。允许多标记用 `+` 连接（如
`cap_level_approx+low_confidence`）；只要不含 `verified` 就不进统计。

## 4. 时间与聚合口径

- `ts` 是**测量时刻**，不是整点；小时化由 `analysis.align.hourly_rates` 的 `floor('h')` 做。
- 生产者不得把小时聚合结果当原始行发（会与 `floor('h')` 叠成双重聚合）。
- 一小时内同一 `(room_id, box_id)` 多行是正常的，analysis 侧取均值。

## 5. 空值语义（铁律）

- 无检出 / 无值：长度列写 **NULL**，`quality='no_detection'`，或整轮不写行。
- **绝不写 0**：`hourly_rates` 会在 0 与真值之间差分，0 会被算成一个陡降的假速率，
  然后污染区间表。这是本票最重要的一条。

## 6. 落地分工（单写者）

| 谁 | 改哪 | 内容 |
| --- | --- | --- |
| 测量师 | `measure/src/measure/record.py`、`measure/pipeline.py` | `RECORD_FIELDS` 加 `room_id`（+`station_id`/`angle_profile`）、`quality` 取值、`to_row()` |
| 环境官 | `analysis/src/analysis/db.py` | `measurements` 建表语句 + `ADDED_COLUMNS` 就地迁移（老库加列，历史行留 NULL，不编默认值） |

加列**先落 analysis 侧的迁移**（老库 `CREATE TABLE IF NOT EXISTS` 不补列，
见 `db.py:131-153` 的注释），测量师随后按新字段发；顺序反了会静默丢字段。

## 7. 验收证据

- [ ] 测量师：一条单测断言 `to_row()` 含 `room_id` 且无检出时 `mean_len_mm is None`、`quality='no_detection'`
- [ ] 测量师：一条真 outbox 行 JSON 样例（脱敏，贴进本票 Comments）
- [ ] 测量师：**验收标准 = `GET /measurements` 出现非零行**（不是 `ranges` 有行 —— 区间表还卡在票 05）
- [ ] 测量师：失败帧不产行（失败不是 0 行测量值）
- [ ] 环境官：analysis 侧端到端 —— `POST /ingest` 两库房各一行 → `/measurements` 两行 → `hourly_rates` 分组不串库房
- [ ] 环境官：老库迁移测试（无 `room_id` 列的旧 `measurements` 表能就地加列，旧行 `room_id` 为 NULL）

## 8. 本票不做

- 不做检出算法本身（票在 `fmc4030-camera-scan`）。
- 不做 analysis 侧字段副本的收敛之外的改动（`align` 修正见票 05）。
- 不新增 `box_id → room_id` 映射表：没有权威源就不造。

## 9. 菌盖直径 / 菌杆长度：明确缺测（2026-10-13 补）

角度档口径（用户 2026-10-13 裁定）：**A —— 单档平视（level0），顶拍与 45° 侧斜都不做。**

- `mean_cap_mm`（菌盖直径）与 `mean_len_mm`（菌杆长度）两列在当前形态下**明确缺测**：
  写 **NULL + `quality='no_detection'`**，原因登记为「**无对应角度档**」。
- **禁止写 0**；**禁止用平视近似**（不许拿平视投影宽度充盖径、拿画面横向像素长度充杆长）。
- 区间表与建议层引用这两列时**必须显式标注"不可用（无对应角度档）"**，
  不得当 0、不得当普通缺测样本静默丢弃 —— 下游要能看出"这一列为什么空"。
- **换角即换口径（Chief 2026-10-13 裁定）：** 现役物理现状 = 平视单档 `level0`（CONTEXT.md:20），
  所以本节成立。**将来若换角生效，本节要重写**（45° 下菌盖直径可得、杆长带投影补偿），
  且**换角生效日 = 数据口径分界**：生效日之前与之后的量**不可直接比较**，
  区间表与趋势图不得跨角拼接。
- 依据：`docs/patrol/fmc4030-camera-scan/issues/09-angle-profile-45-default.md` §0.2、§5
  （"level0 给形态、不给定量"、"不许算法侧拿平视图硬算充数"）。
  ⚠️ 该票 §0 的"默认 45° 单档"是**已作废**的早先口径（Chief 2026-10-13 明确：
  用户改口径后没有"默认 45°"这回事），巡检手正在改那份文字；**以本节为准**。

## Comments

- 2026-10-13 写票（Chief 批口径 + 四条加固）。起因：`analysis.measurements` 0 行，
  `ranges` 0 行；但比"空"更危险的是"错" —— `analysis/src/analysis/align.py:40-41`
  按 `hour` 单键聚合环境，四个库房会混进同一个分箱，而 `measurements` 今天没有任何
  库房维度（`analysis/src/analysis/db.py:31-41`）。本票是票 05 的硬前置。
- 2026-10-13 补 §9（Chief 指示）：平视单档下菌盖直径/菌杆长度明确缺测，写 NULL 不写 0，
  区间表与建议层必须标注"不可用（无对应角度档）"。同条记下与巡检手票 09"默认 45° 单档"的
  口径冲突，已 @Chief 求证。
- 2026-10-13 进展（只读核对）：测量师已在 `measure/src/measure/record.py` 落 `room_id`（必填，
  `__post_init__` 校验）+ `station_id`/`angle_profile`，并注明"不许 analysis 从 box_id 反推"。
  **未齐**：`quality` 仍是自由文本（`measure/quality.py` 出 `blurry` 之类标志，逗号拼接），
  票 §3 的枚举（`verified`/`no_detection`/…）与"只吃 verified"的过滤还没落地；
  `record.py` 引用的 `measure.contract` 模块在仓库里还不存在。票 11 的存储侧（analysis 加列
  迁移）由环境官做，尚未开始。

- 2026-10-13 **测量师侧完成（生产者）**，四件：

  1. **quality 枚举已落**：新模块 `measure/src/measure/contract.py`：
     `QUALITY_FLAGS = (verified, no_detection, baseline, cap_level_approx,
     stem_absent(level0), low_confidence) + no_angle_profile`，多标记用 `+` 连接（§3）；
     `is_usable()` 给下游门控用。**多登记了一个值 `no_angle_profile`** —— §9 要求"缺测原因
     登记为无对应角度档"，这个原因在单档平视下每行都有，放 `quality` 里比埋进 `notes`
     更不容易被下游忽略。请裁定：并入 §3 枚举，还是改用 notes 列。
  2. **幂等/字段**：`RECORD_FIELDS = (ts, room_id, box_id, station_id, angle_profile,
     n, mean_len_mm, mean_cap_mm, p10_len, p90_len, quality)` —— 与你侧
     `MEASUREMENT_FIELDS` 逐字逐序一致（11 列）；`notes` **不在**线格式里（不在键内、
     不偷渡）；`room_id` 必填由 `__post_init__` 兜。
  3. **缺测语义（§5/§9）**：`mean_len_mm`、`mean_cap_mm`、`p10_len`、`p90_len` 四列
     **恒 NULL**（单档平视不可测，禁止平视近似）；`n` = 可信实例数（带 `low_confidence`
     时为 0，fail-closed 给下游 `growth.MIN_N`）；**失败帧不产行**（取图失败/帧质量不合格/
     无检出，默认不产行，`emit_empty=True` 才产 `n=0` 行）。
  4. **像素级菌簇范围已能算但没列**：垂直跨度/水平跨度/面积/投影径都在
     `measure.pipeline.aggregate_box()` 的 `mean_cluster_w_mm / mean_cluster_h_mm /
     cluster_w_min_mm / cluster_w_max_mm / mean_area_mm2 / mean_projected_d_mm`。
     要不要进 `measurements`（列名/单位）由你定，定了我加发字段。

  **验收证据（§7）**：

  - 单测：`measure/tests/` 全绿 **75 passed**（命令见票 05；用 `patrol-workspace\.venv`）。
    其中 `test_record.py::test_ticket_11_acceptance_row_has_room_id_and_null_lengths`
    断言 `to_row()` 含 `room_id`、缺测时 `mean_len_mm/mean_cap_mm is None`、
    `quality="no_detection"`、`notes` 不在行里；`test_production.py` 断言失败帧不产行、
    两库房同 box 不撞键、检出里的 cap 值不许进 `mean_cap_mm`。
  - 真机帧 S306（`data/S306-candidate-Y2058.833-Z117.5-20261008.jpg`，1343×754）走完整
    链路产出的 outbox 行（脱敏，`mm_per_px=0.5`）：

    ```json
    {"ts": "2026-10-13T15:00:00", "room_id": "611", "box_id": "B105",
     "station_id": "S105", "angle_profile": "level0", "n": 0,
     "mean_len_mm": null, "mean_cap_mm": null, "p10_len": null, "p90_len": null,
     "quality": "baseline+low_confidence+no_angle_profile", "kind": "measurement"}
    ```

    该帧的像素级菌簇范围（未落库）：`mean_cluster_w_mm=564.5`、
    `mean_cluster_h_mm=376.5`、`area_mm2=133124`、`projected_d_mm=411.7`。
    `n=0` 是因为该帧亮区占 52% 画面、走 `whole_frame_fallback` ⇒ 朵数不可信。

  **现场发现（请纳入质量门讨论）**：同一张真机帧 `lapvar=70.8`，低于
  `measure/quality.py` 的默认模糊阈值 **100** ⇒ **默认门下该帧被判 blurry、不产行**。
  即"现役阈值会拒真机帧"。我不擅自改默认值：阈值标定需要几帧真实巡检帧的
  `laplacian_variance` 分布，或把门改成"过暗/无对比度"专用判据。

  **依赖顺序**：请先落 `measurements` 加列迁移（`room_id`/`station_id`/`angle_profile`，
  `notes` 若采纳一并加），我再把这三列"必发"；现在就已发，早于迁移的会被静默丢。

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
| `angle_profile` | **缺（若同站位有多角度档）** | **实践上必填：单档也发 `level0`** | 视角/角度档的**唯一权威字段**（见 §3 末）；否则同 (ts, 站位) 的多档会互相覆盖 |
| `n` | 有 | 必填 | 本行统计的有效检出数；`n=0` 与"没写行"含义不同 |
| `mean_len_mm` / `mean_cap_mm` | 有 | 无检出时 **NULL** | 见 §5；平视单档下这两列**明确缺测**，见 §9 |
| `mean_cluster_w_mm` / `mean_cluster_h_mm` | **缺** | **新增（2026-10-13）** | 菌簇水平/垂直跨度均值，**mm**。平视下唯一可用的长势量（见 §9） |
| `mean_area_mm2` / `mean_projected_d_mm` | **缺** | **新增（2026-10-13）** | 菌簇面积均值（**mm²**）与投影径均值（**mm**）。**投影径不是菌盖直径**，不许互换 |
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
| `no_light` | 补光灯没亮，**曝光窗口不成立**（帧本身不可用） | 否 |
| `no_angle_profile` | **盖径/杆长不可用：没有对应角度档**（平视单档下每行都有） | **是 —— 但只作"那两列不可用"的标记，见下** |

**多标记用 `+` 连接**（不是逗号）：如 `verified+no_angle_profile`、
`baseline+low_confidence+no_angle_profile`。

**`no_angle_profile` 与 `verified` 正交（2026-10-13 裁定）**：它标记的是
"`mean_cap_mm`/`mean_len_mm` 这两列不可用"，**不否定整行**。
`verified+no_angle_profile` 是合法组合，该行**照样进统计**（用菌簇范围的形态量）。
若把这个 flag 当成"整行不可用"，平视单档下会一行不剩 —— 那就从"标注缺测"变成了"删数据"。

判定规则：**区间表与决策层只吃 `quality` 里含 `verified` 的行**，其余行落在库里但不参与统计
（不是删掉 —— 缺失要可审计，`spec.md` §6）。

**视角事实的权威字段与描述性 flag（2026-10-13，按已落地实现收敛）：**
`angle_profile`（`level0`）是**唯一权威字段** —— 关联、分档、跨角比较一律以它为准。
`level_view` 已作为**描述性 flag** 进枚举（每行都带，见 Comments），可以留，但**不参与可用性判定**：
它不许进 `NON_USABLE_FLAGS`，也不许被拿来当"第二份视角事实"去做 join 或分组。
一句话：**能决定行为的只有 `angle_profile`；`level_view` 只是它的一句旁白。**

**`verified` 的发放基准 = 人工看图（2026-10-13 定）：** 自动门（lapvar 之类）只作**辅助告警**，
**不是唯一拒收依据**。定标用抽样 + 人工标注（L1 跨轮次 / L2 跨天 / L3 跨生长阶段 / L4 跨站位，
另加加湿期、灯未亮、预览流三类坏帧各 ≥10 帧对照，见测量侧票 13 的设计）。
判据一条：**人工判可用、而自动门判低纹理的帧，必须能被发放 `verified`** ——
平视 + 矮菇本身就低纹理，否则门会把可用帧整批吃掉，区间表还是"诚实地空"。

## 4. 时间与聚合口径

- `ts` 是**测量时刻**，不是整点；小时化由 `analysis.align.hourly_rates` 的 `floor('h')` 做。
- 生产者不得把小时聚合结果当原始行发（会与 `floor('h')` 叠成双重聚合）。
- 一小时内同一 `(room_id, box_id)` 多行是正常的，analysis 侧取均值。

## 5. 空值语义（铁律）

- 无检出 / 无值：长度列写 **NULL**，`quality='no_detection'`，或整轮不写行。
- **绝不写 0**：`hourly_rates` 会在 0 与真值之间差分，0 会被算成一个陡降的假速率，
  然后污染区间表。这是本票最重要的一条。
- **生产装配建议开 `emit_empty=True`（2026-10-13 裁）**：被门拒的帧**留一行**
  （`n=0` + 原因标记，菌簇四列仍为 NULL）。理由：不开的话被拒帧在 `measurements` 里
  **完全没有痕迹**，人工根本不知道哪一帧需要复核；开了也不污染统计 —— `quality` 不含
  `verified`，区间表与建议层直接跳过。**NULL 而不是"有值但不可用"是刻意的**：
  下游漏看 `quality` 也拿不到数字可算；人工判可用的正路是"看图 + 重发该帧的测量行
  （带 `verified` 与四列值）"，**不许在库里把 NULL 改成值**。

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

**现役物理现状 = 单档平视 `level0`**（CONTEXT.md:20、ADR-0024、
`fmc4030-camera-scan/issues/09-angle-profile-and-framing.md` 第三版），
所以顶拍与 45° 侧斜对应的测量能力**今天不存在** —— 缺测口径照本节不变。
**是否换角不预设**：由现场作业与用户安排决定；**换角生效日 = 数据口径分界**。

- `mean_cap_mm`（菌盖直径）与 `mean_len_mm`（菌杆长度）两列在当前形态下**明确缺测**：
  写 **NULL + `quality='no_detection'`**，原因登记为「**无对应角度档**」。
- **禁止写 0**；**禁止用平视近似**（不许拿平视投影宽度充盖径、拿画面横向像素长度充杆长）。
- 区间表与建议层引用这两列时**必须显式标注"不可用（无对应角度档）"**，
  不得当 0、不得当普通缺测样本静默丢弃 —— 下游要能看出"这一列为什么空"。
- **换角即换口径（Chief 2026-10-13 裁定）：** 现役物理现状 = 平视单档 `level0`（CONTEXT.md:20），
  所以本节成立。**将来若换角生效，本节要重写**（45° 下菌盖直径可得、杆长带投影补偿），
  且**换角生效日 = 数据口径分界**：生效日之前与之后的量**不可直接比较**，
  区间表与趋势图不得跨角拼接。
- 依据：`docs/patrol/fmc4030-camera-scan/issues/09-angle-profile-and-framing.md` §0.2、§5
  （"level0 给形态、不给定量"、"不许算法侧拿平视图硬算充数"；该票已按"名字本身不该是结论"
  改名，早先的 `09-angle-profile-45-default.md` 已不存在）与 **ADR-0024**（测量标定权威）。
  三处（该票 / ADR-0024 / 本票）同口径，不再互标冲突。
- **对区间表的直接后果（2026-10-13 补）**：`mean_len_mm` 缺测 ⇒ `align.hourly_rates` 拿不到
  生长速率 ⇒ 区间表**不能**再用杆长差分。平视下可用的长势量是**菌簇范围**
  （`mean_area_mm2` / `mean_projected_d_mm` / 跨度，见 §1），速率改用它们的小时增量算。
  这条已写进票 05。

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

- 2026-10-13 环境官裁三条 + 测量师落地（提交 `02d61f9`）：
  1. **`no_angle_profile` 并入 §3 枚举**（不新开 notes 列），且**与 `verified` 正交**：
     只标记盖径/杆长两列不可用，不否定整行。已用单测钉死：
     `test_no_angle_profile_is_orthogonal_to_verified` 断言它**不在**
     `NON_USABLE_FLAGS`、且 `verified+no_angle_profile(+level_view)` 判定为可用——
     否则平视单档下每行都带它，`verified` 会一行不剩（从"标注缺测"变成"删数据"）。
  2. **菌簇四列已进 `RECORD_FIELDS`**（`mean_cluster_w_mm` / `mean_cluster_h_mm` /
     `mean_area_mm2` / `mean_projected_d_mm`，照 DDL 列序放在 `p10_len` 之前）；
     `cluster_w_min_mm`/`cluster_w_max_mm` 按裁定**不发**。两侧现在各 15 列，
     单测 `test_record_fields_are_the_analysis_contract` 断言**名字集合一字不差**
     （顺序不逐位比：你侧 `MEASUREMENT_FIELDS` 是 INSERT 列清单，顺序无关）。
     真机帧 S306 的三行样例四列都有值：`w=564.5 h=376.5 area=133124 projected_d=411.702`
     （mm / mm²），可直接用于你提的链路验证（生长速率改用面积或投影径的小时增量）。
  3. **模糊门的定位已对齐**：门只决定 `verified` 的授予，**不决定入库**；被拒帧留在库里
     带 `low_confidence`（默认不产行是为了不给下游塞空壳行，`emit_empty=True` 即可每帧留痕）。
     你提的风险我记账了：若门把绝大多数真机帧都拒了，`verified` 没有样本、区间表仍是空
     （只是"诚实的空"）——所以**阈值定标与票 05 一起排**，标定完成前宁可偏保守。
- 2026-10-13 Chief 裁定 **`no_light`（整帧近黑/灯未亮）新增判据、处置 = 不可用**（不是降权）：
  已落 `measure/quality.py`（用 **p99 亮度**判，不用均值——真机帧强晕影下均值会误判），
  枚举已加 `no_light`，阈值同属票 13 的标定范围。`level_view`（平视口径）也已加进枚举，
  每行都带。

- 2026-10-13 测量师补一条**实测结论对契约的影响**（标定 218 张线上帧后得出，标定细节见票 13）：
  **`preview` 类帧不能靠图像判据拦住**。实测 `preview`（1280×718）的 `lapvar` p10/p50/p90 =
  78.08 / 80.62 / 83.40，`p99` p50=230 —— 与正常帧（`lapvar` p50=71.3、`p99` p50=225.5）
  **没有可分性**：预览帧甚至比原图更"锐"（缩图效应）。所以：
  1. 质量三判据（清晰度/近黑/过曝）**都拦不住**预览帧，不要把"拦 preview"寄望于阈值；
  2. 要拦它只能靠**来源**：`images` 侧已有来源区分（历史帧 vs 预览流），若将来预览帧可能
     混进测量输入，测量侧需要拿到来源标记（如 `source=preview_frame`）才判得出来；
  3. 在拿到来源标记之前，测量侧的做法是**不猜**：不因"像预览"就拒帧（会误杀正常帧），
     只按质量三判据判可用性。
  这条请评估是否要进 `images` / 测量输入的字段面（我不先加字段，等契约裁定）。

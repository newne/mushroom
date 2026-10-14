# 21. 20 帧人工标注的表结构与流程（预标注 + 人只复核）

- **归属:** 表结构与预标注工具 = 测量师（`measure/**`）；复核执行 = 用户排期的现场/人工；**版本 = 2026-10-14**
- **Status:** ready-for-human（工单已生成，等排期）
- **关联:** 票 15 §2（20 帧标注是 precision/recall 的唯一来源）、票 15 §13（218 帧评估）、Chief 2026-10-14 裁定 ⑤（只投 20 帧、不训练）

## 1. 流程决定：**预标注 + 人只复核**（不是人从零标）

理由（Chief 倾向的这条我采纳，并给出"为什么它不降低证据强度"）：

- **省人时**：现役 L1/L3 已能出候选框，从零点几千个框不现实；预标注后人的动作只有
  **确认/删改/补漏**，而**删改与新增都被逐框记录**（`review_status`、`source`）；
- **precision 与 recall 都还算得出**：假阳 = `rejected` 的行、漏检 = `human_added` 的行；
  只有"既没预标注、人也没补"的实例才会漏统计——这是任何标注流程都有的残余，不是本设计特例；
- **与将来训练对齐**：同一个 CSV 能直接导出 COCO（`category` + `bbox_*`；将来有帽阶段样本时
  `category` 增加 `cap`/`stipe`，**表结构不变**），不需要"一次性丢弃的标注"。

## 1.5 人只做四件事（Chief 2026-10-14 复核，写死）

1. **确认** `review_status=confirmed`：框对、分类对、归属（`bag_id`）对；
2. **改框** `review_status=edited`：改 `bbox_*` / `base_y` / `tip_y` / `bag_id`（改了必须写 `reviewer_note` 一句）；
3. **删** `review_status=rejected`：**行必须保留**——它就是假阳率的分母里"错的那部分"，
   删掉行 = 把假阳率洗白（并写一句 `reviewer_note` 说明为什么是假阳）；
4. **补漏**：在 CSV 里**新增一行**，`source=human_added`、`review_status=confirmed`；
   若是**被袋肩门丢掉的真菇**（overlay 里的红框），必须写 **`reviewer_note=gate_false_negative`**
   ——这条**直接量化袋肩门的漏检**。

**两条不许动**：
- **`ignore=1` 的行必须保留**（严重遮挡/不可判）——删掉它，漏检率的**分母就错了**；
- **`truncated=1 ⇒ height_valid=0`，但该实例仍算"检出到了"、参与 recall**
  （被切掉的是杆高，不是"有没有菇"）。

**红线（再写一遍）**：**禁止在 `ds=2` 工作坐标里标**，坐标一律**原图 px**；
`w_px`/`h_px` **一律 2880×1616**（线上真机口径，全池 3147 帧一致）。

**一条容易被漏、但价值最高的样本**：**"看起来明显没有菇"的帧也必须逐框复核**——
比如工单里的 **F01（51 个候选全被袋肩门判掉、门后 0）**，它是"**全是袋面纹理**"的极端样本，
正是**袋肩门假阴/假阳判据**的来源；跳过它，就等于放弃了对袋肩门本身的检验。

## 2. 表结构（权威）

### 2.1 `frames.csv`（每帧一行）

| 列 | 单位/取值 | 谁填 | 说明 |
| --- | --- | --- | --- |
| `frame_id` | 文本 | 工具 | **唯一且可读**：`F07_S110_20261013T152136` |
| `file` | 相对路径 | 工具 | 相对 `.scratch/frame-quality-samples/` |
| `station_id` / `layer` / `col` / `ts` / `date` / `angle_profile` | 文本 | 工具 | 帧元数据 |
| `w_px` / `h_px` | px | 工具 | **一律 2880×1616**（线上真机口径） |
| `stratum` | 文本 | 工具 | `YYYYMMDD-day/night`（抽样覆盖用） |
| `pre_annotator` / `pre_version` | 文本 | 工具 | 例 `measure L1+L3` / `69b1434@2026-10-14` |
| `reviewer` / `review_date` | 文本/日期 | **人** | 复核人、日期 |
| `n_bag_pre` / `n_fruit_pre` / `n_fruit_gate_kept` / `n_fruit_gate_dropped` | 计数 | 工具 | 预标注量（含袋肩门前后的对比） |
| `notes` | 文本 | 工具/人 | 工具写诊断（`n_rows`/`weak_retry`/`kept_frac`） |

### 2.2 `instances.csv`（**每实例一行**，坐标一律**原图 px**）

| 列 | 单位/取值 | 说明 |
| --- | --- | --- |
| `frame_id` | 文本 | 关联 `frames.csv` |
| `instance_id` | 文本 | `F07_S110_…-001` 递增，**帧内唯一** |
| `category` | `bag` / `fruitbody` | 将来扩 `cap`/`stipe`（表结构不变） |
| `source` | `pre` / `human_added` | 预标注 vs 人补的（补的 ⇒ 直接量化漏检） |
| `review_status` | `pending`/`confirmed`/`edited`/`rejected` | 人的结论；`rejected` **保留行**（假阳证据） |
| `bbox_x0/y0/x1/y1` | **原图 px** | 左上/右下 |
| `bag_id` | 文本 | 仅菇体：它长在哪袋（必须指向本帧 `category=bag` 的行） |
| `cluster_id` | 文本 | 仅菇体：同袋同簇分组（**供"菌簇长度/直径"复算**） |
| `base_y` / `tip_y` | 原图 px | 仅菇体：底部/尖端；任一不可见 ⇒ 留空 + `height_valid=0` |
| `height_valid` | `0`/`1` | **杆高一致性的样本门**：两端都可见才 1 |
| `truncated` | `0`/`1` | 触画面边缘（被切） |
| `occluded_frac` | `0`/`1`/`2`/`3` | 无 / ≤1/3 / ≤2/3 / >2/3 |
| `ignore` | `0`/`1` | **1 = 不参与 precision/recall**（严重遮挡/不可判）——但**行必须保留** |
| `gate` | `gated` | 本行来自袋肩门保留集；（被门丢掉的候选**不逐行进 CSV**，只画在 overlay 红框里） |
| `reviewer_note` | 文本 | 改框/删框/新增时**必填**一句（`merged`/`gate_false_negative`/`partial_bag`…） |

**红线**：坐标**禁止**用 `ds=2` 工作坐标（否则 mm 换算与训练都错）；
被袋肩门丢掉的候选**必须画进 overlay**（不画就算不出漏检），但不逐条进 CSV（几千行人工拒绝无意义）。

## 3. 边界情况判据（照此标，否则 precision/recall 没法解释）

| 情况 | 怎么标 |
| --- | --- |
| 触画面边缘 | `truncated=1` + **`height_valid=0`**；仍算"检出到了"，参与 recall |
| 遮挡 ≤1/3 | `occluded_frac=1`，正常参与 |
| 遮挡 1/3–2/3 | `occluded_frac=2`，正常参与；杆高一致性只看 `height_valid=1` |
| 遮挡 >2/3 或分不清是菇还是袋面 | `occluded_frac=3` + **`ignore=1`**，**保留行**并写 note |
| 多菇黏连分不开 | **标成一个实例** + `reviewer_note=merged`（不按想象拆） |
| 真菇被袋肩门丢掉（红框） | **新增一行** `source=human_added`、`note=gate_false_negative` |
| 菌包被菇覆盖 | 仍按**可见部分**框 `category=bag`；`occluded_frac` 记录 |
| 菌包只露一小角 | 框它 + `note=partial_bag`；袋数 precision/recall 按"可见 ≥1/3"单独复核 |

## 4. 抽样（已执行，可复跑）

工具：`measure/tools/make_annotation_sheet.py`（系统性抽样：`label_auto=ok`，按 ts 排序等间隔取 20，
**排除 10-09 18:20–18:28 那一轮**（与 `analysis/integrity.py` 的 `LEGACY_EXCLUSIONS` 同窗口））。

```
python measure/tools/make_annotation_sheet.py --out .scratch/annotation-20 --n 20
```

实测组成（20 帧 / **923 实例行**，预标注版本 `69b1434@2026-10-14`）：

- **日期**：10-11 4 帧 / 10-12 1 帧 / 10-13 15 帧；**昼 17 / 夜 3**；
- **19 个不同站位**（S101 与 S411、S404 各重复一次）；
- **袋数 5–36**（含只认出 5 袋的难帧）、**门后菇体 0–65**（**含一帧门后 0**：F01 51 个候选全被判掉）。

产物：`.scratch/annotation-20/{frames.csv, instances.csv, overlays/*.jpg, README.md}`。

## 4.5 离线复核页（把"923 行手改"变成"点选"）

> **用法一句话**：**双击打开 → 选 `.scratch/annotation-20/` 目录 → 逐帧「确认/改框/删/加」→ 点导出**；
> 导出文件**另存**（别覆盖工单原件）。

**文件**：`measure/tools/review_annotation.html`（**单个 HTML**，无服务、无依赖、**双击即开**，离线可用）。

**⚠️ 验证状态（如实写）**：**视觉渲染未经真人/浏览器验证**——本机没有可驱动浏览器的工具，
只做了**无头证据**（`node --check` 语法；纯函数往返：923→924 行、表头逐字相同、CRLF 保留、
`gate_false_negative` 自动填；Python 侧读回 19 列一致并算出 P/R 骨架）。
⇒ **"视觉渲染"这一条待用户打开（或在我的浏览器面板里打开、我用 `read_browser` 读那一页）后补成 PASS。**

**怎么用（三步）**：

1. 双击打开该 HTML（Chrome/Edge 均可）；
2. 顶部 **"工单目录"** 选 `.scratch/annotation-20/` 整个目录（`webkitdirectory`；
   或分别选 `frames.csv` / `instances.csv` / `dropped.csv` + `overlays/*.jpg`）；
3. 逐帧复核 → 点 **导出 CSV**（得到 `instances.csv` + `frames.csv`，**列名与顺序与输入完全一致**，
   行尾与 BOM 也随输入保留）。

**页面上能做什么**（对应 §1.5 的"四件事"）：
- 点框选中 → **拖动改框**（自动 `edited`）、**右下角拖动缩放**；
- **确认** / **删除**（写 `rejected`，**行保留不真删**）；
- **＋ 新增框**：在画面上拖一个矩形 ⇒ `human_added`；**若压在被门丢掉的候选上，自动写
  `gate_false_negative`**；
- `occluded_frac` / `truncated` / `height_valid` / `ignore` / `category` **一律下拉**（不许自由文本）；
- 可开关"**显示被门丢掉的候选**"（红框来自工单新增的 `dropped.csv`，见 §2.3）；
- `reviewer` 与 `review_date` 在**导出时**自动回填到 `frames.csv`（只回填真有复核动作的帧）。

**硬约束（Chief 2026-10-14）逐条落地**：① 坐标一律**原图 px / 2880×1616**（页面直接以原图尺寸画布，
缩的只是 CSS 显示）✔；② 导出**列名/顺序/取值域与输入一致**（`serializeCSV` 用输入的表头顺序，
并按输入嗅探 CRLF/BOM 原样保留）✔；③ 删掉的框**写 `rejected` 保留行** ✔；④ **不联网、不起服务、无依赖** ✔。

**验证（无浏览器环境下做过什么）**：
- `node --check` 通过（页面脚本语法）；
- **纯函数往返**（Node 载入页面脚本）：读真工单 923 行 → 确认 1、改框 1、删除 1、新增 1
  （压在被门丢掉的候选上）→ 导出 ⇒ **表头与输入逐字相同**、**换行保持 CRLF**、行数 923→924、
  新行 `reviewer_note=gate_false_negative`；
- **下游可读性**：导出文件被普通 `csv` 读回，**列名与顺序一致（19 列）**、
  `review_status`/`source` 计数正确，并可算出 P/R 骨架
  （precision = (确认+改框)/(确认+改框+拒)；recall = (确认+改框)/(确认+改框+人补)）。

### 2.3 `dropped.csv`（被袋肩门丢掉的候选，**只给复核页画红框**）

| 列 | 说明 |
| --- | --- |
| `frame_id` / `candidate_id` | 帧与候选号（`…-D001`） |
| `bbox_x0/y0/x1/y1` | **原图 px** |
| `reason` | `inside_bag_body`（钉在袋体内部=袋面纹理）/ `unassigned_no_same_row_bag` / `other` |
| `bag_id` | 归属袋（能归属时） |

**它不进 `instances.csv`**：2819 个候选逐条进 CSV 就等于让人做几千次"拒绝"，
而复核页把它们画成红框、只在"确实是菇"时才点新增（新增行会自动带上 `gate_false_negative`）——
**既省人时，又保住了门漏检的计数**。

## 5. 复核完成后产出什么（写回票 15）
1. **L1**：菌包 precision/recall、袋宽 px 的一致性（预标注 vs 人工 ⇒ **分割误差**，不是像素测量误差）；
2. **L3**：菇体实例 precision/recall（`rejected` 率 = 假阳、`human_added` = 漏检）；
3. **袋肩门**：假阴/假阳率（红框侧 + `gate_false_negative`）；
4. **杆高一致性**：`height_valid=1` 的实例，预标注高度 vs 人工确认高度之差（px ⇒ 乘局部 mm/px）；
5. 结论写进 **票 15 §2 与 §13**；**未完成前，精度一律"未知"**，不许拿"看起来对"替代。

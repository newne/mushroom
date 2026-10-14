# `xlabel_coco_join`：XLABEL JSON → COCO(segment) + 侧车 `objects.json`

**离线工具，不进任何镜像**（本目录不在 measure/patrol/analysis/deploy/web 之中——"不进镜像"是结构性的）。
归属：测量师；授权：Chief 2026-10-14（范围只限本转换器；不碰 `measure/**`、不改 DDL、不动生产、不写训练代码）。

## 为什么必须有它（实测，2026-10-14 运维）

`xlabel2coco --mode segment` rc=0，但 annotation 只剩 `area/bbox/category_id/id/ignore/image_id/iscrowd/segmentation`：

| 语义 | COCO 侧 | 承载方 |
| --- | --- | --- |
| `attributes`（含 `review_status`/`baseline_valid`…） | **丢** | 侧车 `objects.json` |
| `group_id`（一袋一簇分组） | **丢** | 侧车 |
| 帧级 `flags`（`cap_state`/`quality_tags`） | **丢** | 侧车 `frames[].frame_meta` |
| `bag_top` 折线 | **不进 COCO**（非掩码形态） | 侧车 + 丢失清单点名 |
| `difficult` / `description` / `score` | **丢** | 侧车 |

⇒ **主档案只能是 XLABEL JSON，COCO 是派生物**（票 01 §4.5.2 红线）。

## 用法

```bash
python convert_xlabel.py --images IMG_DIR [--labels JSON_DIR] --classes labels.txt --out OUT_DIR \
                         [--room 611] [--expect-size 2880x1616] [--strict]
```

产物（`OUT_DIR/`）：`annotations.coco.json`、`objects.json`（侧车）、`coco_loss_report.json`（丢失清单）。
退出码：`0` join 全过 ｜ `1` **join 断言失败（不许当成功）** ｜ `2` `--strict` 且丢失清单非空。

## 契约（票 01 §4.5.2 定死）

1. **join 键 `(filename, shape_index)` 1:1**：侧车对象数与源 `shapes[]` 总数相等、键唯一、每条 COCO annotation 能回指侧车；对不上 ⇒ `JoinError`，**不静默**。
2. **`rejected` 不许静默消失**：掩码形态落 `iscrowd=1`；非掩码形态（折线）在丢失清单里**点名 + 计数**。
3. **侧车完整承载**：`attributes` / `group_id` / `shape_type` 与源逐条相等；帧级 `frame_meta` 与 COCO `images[]` 逐帧相等。
4. **`image_id`**（Chief 裁定）：派生确定性非空键 `"{YYYYMMDD}T{HHMMSS}.{station}.{profile}"`，叫不出身份退化 `file.{stem}`；
   **同一记录同时带原始字段四元组** `raw_key={room_id, ts, station_id, angle_profile, source_file}`（COCO `images[].raw_key` + 侧车）。
5. **丢失清单可见**：`carried_by_sidecar` 给出各类计数；`items` 逐条点名（`non_mask_shape_type:*`、`size_mismatch`）；`rejected` 逐条给 `preserved_as`。
   判定不是"没报错就算对"，而是清单必须能读出丢了什么、谁承载。

## 测试（无 pytest 也能跑）

```bash
python tests/test_convert_xlabel.py    # 8 passed / 8 total
pytest tests/test_convert_xlabel.py    # 同一批用例（本机 venv 曾被并发重建，故保留独立 runner）
```

> 用例文件按仓库 `.gitignore` 的 `test_*.py` 规则（那条是给算法侧临时脚本设的）本会被静默忽略，
> 所以本文件是**显式 `git add -f` 入库**的——与 `.gitignore` 里"真测试必须入库"的既有意图一致；
> 未改 `.gitignore`（不是我的文件）。

**已跑证据（2026-10-14）**
- 正跑（真样例 1 帧 6 shape）：COCO 5 ann + 侧车 6；`bag_top` 折线进清单 `(2, 'bag_top', 'non_mask_shape_type:linestrip')`；`rc=0`。
- **反向自证**：抽掉 1:1 检查 ⇒ `test_missing_sidecar_entry_is_detected`、`test_rejected_linestrip_must_be_named_in_loss_report` **红**（6/8）；
  `rejected` 改 `iscrowd=0` ⇒ `test_rejected_polygon_must_be_iscrowd` **红**（7/8）；还原后 sha256 与变异前一致。

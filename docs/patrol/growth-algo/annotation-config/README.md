# 标注配置（X-AnyLabeling）— 给运维/标注人的三件文件 + 一个字段样例

- **归属：** 测量师（P0 定义）；**客户端部署与推送到 prod 服务器**归运维（票 `02`）
- **状态：** 定义已出；**未在任何客户端实测**（本轮只出定义，不写码、不接生产）

## 1. 文件

| 文件 | 用途 | 客户端里怎么加载 |
| --- | --- | --- |
| `labels.txt` | 类别清单（**行序 = 类别 ID**：`bag`=0、`cluster`=1、`cap`=2、`bag_top`=3、`void`=4） | `Upload -> Upload Label Classes File`（或 `.xanylabelingrc` 的 `labels:`） |
| `attributes.json` | 对象属性（Plan §5.3 的 `occluded/truncated/association_valid/measurement_valid/rejection_reason/review_status/baseline_valid`） | `Upload -> Upload Attributes File` |
| `image_flags.txt` | 帧级 flags（`cap_state` 三值 + `quality_tags`） | `Upload -> Upload Image Flags File` |
| `example-frame.xlabel.json` | **字段落点样例**（手写，**不是真机帧标注**），给标注人对照每个字段在哪 | 不用加载；用编辑器打开看 |

## 2. 硬规则（改任何一条都要走 P0 变更，别在客户端里手改）

1. **类别只追加，不改序、不改名**：COCO 类别名按此顺序固化，改名 = 已导出数据全部要重导。
2. **掩码一律多边形**：袋/簇/帽/不可判定区用 `polygon`（含 Brush Polygon `Ctrl+N`、Magic Wand `Shift+W` 产出的也是 polygon）。
   **袋的可见框不单独标**——由转换脚本从掩码外接得到（避免"框与掩码两套不一致"）。
3. **袋顶折线用 `linestrip`（`Shift+S`），不用 `line`**：`line` 只有两点，表达不了弧形/分段袋顶。
4. **同袋分组用客户端原生 `group_id`（`G` 键）**，不另设 `bag_group_id` 属性：
   同一袋的 `bag` + `cluster` + `bag_top` +（可辨认的）`cap` 共用一个 `group_id`；
   **归属不清就不分组**（`group_id=null`）+ `association_valid=false`。
5. 属性里的 `"true"/"false"` 是**字符串值**，由转换脚本映射为布尔；**不要写成布尔**（属性面板只吃词表里的值）。
6. **帧级 flags 恰好一个 `cap_*` 为真**（帧不可用时三个都假并写 `q_*`）；`q_*` 可多选、可全假。
7. 帧级 `checked`（`Ctrl+Alt+K`）表示**该帧复核完成**，与对象级 `review_status` 是两件事。
8. **`difficult` 是"问题行"的客户端检索代理**（`difficult::1` 是客户端**唯一**能搜到"有问题对象"的入口）：
   `difficult=1 ⇔ review_status=rejected 或 association_valid=false 或 measurement_valid=false`。
   权威字段仍是属性；`difficult` 只求能搜。
9. 坐标一律**原图 px、2880×1616**；图与 JSON **同名同目录**（客户端默认），**禁止用改文件名表达属性**。
10. 无值一律留空/`none`，**不写 0 冒充测量值**。

## 3. 出处（上游文档，2026-10-14 抓取）

- 仓库 `CVHub520/X-AnyLabeling` main 分支，抓取 commit `a7d2765adc977cf1cbb82ff0ce897b04bcf31912`；
- 形态表：`docs/en/user_guide.md` §2.1（`rectangle/rotation/polygon/quadrilateral/point/line/linestrip/circle/cuboid`、Brush Polygon、Magic Wand）；
- JSON 结构：同文件 §1.4（`shapes[].label/points/group_id/difficult/shape_type/flags/attributes`、顶层 `flags/tags/checked/description`）；
- 预定义标签：§7.3；属性文件格式：`examples/classification/shape-level/README.md`（含 `__widget_types__`）；
- 帧级 flags：`examples/classification/image-level/README.md`（`--flags` / `Upload Image Flags File`）；
- 客户端内可搜项只有 `difficult/gid/shape/label/type/score/description/checked`（§2.5）——**自定义属性搜不到**，找负样本要靠导出后脚本筛。

## 4. 尚未实测（P1 第一件，别当已验证）

1. 客户端**实际版本**是否与上述 main 分支文档一致（票 02 §8 已要求截图核版本）；
2. **帧级 flags 手工设置路径**：文档只写了模型预测后到 `Ctrl+3` 里改；**纯人工怎么设要实测**；不行就退到 `View -> Image Tags`（`Ctrl+Shift+T`）写 `cap_state:clear` 这类固定串；
3. **导出 COCO 是否携带属性/`group_id`**（票 02 §6.2 四条判据）——**丢就出丢失清单，不许静默丢**；丢了就以 XLABEL JSON 为主档案。

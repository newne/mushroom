# 17. §9.5 评测标注批次：100–150 帧 + 静态重复对 + 锁定测试集

Status: ready-for-human
归属：算法（抽样与规范）+ 标注（人）+ 运维（取图/抓拍）
写域：`docs/patrol/growth-algo/annotation-config/**`、`docs/patrol/growth-algo/tools/**`、
`.scratch/**`、标注目录（帧**只读**取用，不改原图）
依据：`docs/patrol/growth-algo/spec.md` §9.5（数据与误差项）、§9.2、§9.3、§5.5、G7/G8；
票 03（样本池与取图方式）、`annotation-config/README.md`（类别只追加）
依赖：09（持续归档与取图链路）；票 13/14 的产出（用于"先跑通评测器"，但**标注不等它们**）
**时间盒**：静态重复对必须在 **2026-10-25 停采前**抓完；过期不可再生

## 1. 交付物

1. **抽样清单**（100–150 帧，可复算）：脚本 + TSV（`station/date/HHMMSS/pool_id/stratum`），
   分层 = 层 × 光照 × 阶段 × 事件；从归档/console **只读**取图。
2. **评测标注规范落地**：在现有 `annotation-config/` 上补两类东西（不改已有类别顺序）：
   - `labels.txt` **追加** `cluster_top`（`linestrip`，簇顶部边界）⇒ 新类别 ID = 5；
   - `attributes.json` 给所有类别补 `source`（`template`/`heuristic`/`model`/`human_added`，必填）；
     `cluster_top` 复用 `review_status` 与 `rejection_note`。
   现有 `bag_top`（ID 3）与 `bag`/`cluster` 掩码**已经够用**，不重画。
3. **人工真值集**：`bag` 掩码、`bag_top` 折线、`cluster` 掩码、`cluster_top` 折线；
   袋宽取 `bag` 掩码**最宽行**（可复算，不新增量测线类别）；帽期另标 30–50 帧 `cap` 掩码。
   **不标菌杆**（§9.5 明写）。
4. **静态重复对 10–20 对**：同站位、分钟级（console 实时模式 + 手动抢占，ADR-0004）；
   抓拍清单 + 采集步骤 + 质量门结果；每对记 `round_id` 与时间差。
5. **锁定测试集**：按 `round_id` 分组（同一轮全部站位同侧，G8），
   与开发集按 `round_id` **零交集**，**不含任何模型预标注**；冻结清单 + 文件哈希 + 冻结时间。
6. **标注 QA**：≥10% 帧双人独立标注；分歧率与仲裁规则写回本票（只看"一袋一簇归属"与"簇顶边界"两项）。

## 2. 口径（写死）

| 项 | 口径 |
| --- | --- |
| 帧数 | 100–150；分层计数与目标表一致，**不许"凑够就行"** |
| 来源 | 从**归档帧**抽，不从生产实时流抽（保证可复跑）；同一帧只出现在一个集合 |
| `bag_top` | 贴**袋口**，不贴菌簇；遮挡处断开，不猜（`baseline_valid=false`） |
| `cluster_top` | 贴簇的**可见外轮廓顶部**，不是菌帽轮廓；被遮挡列断开 |
| 袋宽 | 由 `bag` 掩码最宽行导出，落 `ref_kind=bag_width` |
| 静态对 | 同站位、时间差 ≤60 min、两帧均过质量门；**不是"相邻轮次弱替代"** |
| 锁定集 | `round_id` 分组；无模型预标注；冻结后不改 |

**抽样与 P1 池的关系（spec §9.5 的口径冲突，本票按此执行）**

§9.5 说"从 P1 单帧池另抽"，但 P1 池只有 68 帧（票 03），**不够 100–150**。
本票按"**沿用 P1 的抽样规则**（单帧、分层、不取连续序列）、从归档帧重新抽一批"执行，
并在清单里标 `pool_id=eval-v6`；不与 `sample-pool-p1` 复用同一批帧的锁定位。

## 3. 验收（缺一条不算过）

- [ ] 抽样脚本同输入同输出（固定种子）；TSV 行数 = 实际落盘帧数
- [ ] 每条标注 `source` 必填；缺一条就整批退回（导出后断言）
- [ ] `bag_top` 的每个 `group_id` 能对应到一个 `bag` 实例；`cluster_top` 能对应到一个 `cluster` 实例
      （join 断言，防止"折线画在空气里"）
- [ ] 锁定测试集与开发集 `round_id` 交集为空；且锁定集内 `source` 只有 `human_added`
- [ ] 静态重复对 ≥10 对，且**全部**通过质量门（不合格的对要补抓，不许拿弱替代凑数）
- [ ] 10% 双人标注的分歧率有数、有仲裁记录
- [ ] 试标 3 帧走完「预标注 → 复核 → 回写 → 回读」四条（票 02 §6.2 同口径）
- [ ] 清单、配置、冻结记录都进 `docs/patrol/growth-algo/`；原始帧不入库

## 4. 不做什么

- **不训练模型、不跑评测**（票 18）
- **不改 `measure/**` 代码**（本票只产数据与配置）
- 不标菌杆、不标 mm、不做绝对尺寸
- 不用锁定测试集做任何调参或阈值选择（§9.3）

## 5. 复跑

```bash
# 抽样（示例；脚本落地后补真实参数）
uv run --no-project --offline --with numpy \
  python docs/patrol/growth-algo/tools/sample_eval_batch.py \
  --from 2026-10-10 --to 2026-10-24 --target 120 --seed 20261020 \
  --out docs/patrol/growth-algo/annotation-config/pull-list-eval-v6.tsv
```

## Comments

- 2026-10-20 开票：v6 新增 §9.5，但评测数据一件都还没有；同时 `annotation-config` 里
  **没有 `cluster_top` 类别、也没有 `source` 属性**，而 §5.5/§9.5 都要求它们。
  静态重复对是唯一能定 `delta` 的 `tol` 的噪声底，**必须在停采前抓**。

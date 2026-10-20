# 18. §9.5 对照框架：五组方法 / 误差项 / 门槛冻结 / 四组消融 + `delta` QA

Status: ready-for-agent
归属：算法
写域：`measure/tools/**`、`measure/tests/**`、`docs/patrol/growth-algo/**`（门槛与报告）＋本票
依据：`docs/patrol/growth-algo/spec.md` §9.5（数据/误差项/门槛/消融）、§9.1、§9.3、
§4.7（`delta` 只作 QA）、§4.5（初值冻结纪律）
依赖：17（标注与锁定测试集）、12、13、14；
H3 组依赖 P2-1 的模型产物（**该工作包尚无票**）⇒ **H1/H2/H5 组先跑，H3/H3+H5 后置**

## 1. 交付物

1. 对照框架 `measure/tools/eval_profile_metrics.py`：输入帧清单 + 标注 + 方法开关，
   输出**逐帧逐袋** CSV + 汇总 JSON；一个方法一个 `--method`，不把结果合并成单一总分。
2. 误差项实现（§9.5 逐项）：
   `t(x)` 对 `cluster_top`、`b(x)` 对 `bag_top`、`H_profile`/`H_p50`/`H_p90` 对人工 `mean(b−t)`、
   宽度/面积相对误差（**只在不粘连的袋上**）、归属正确率、同图重复（两次推理/阈值抖动）、
   静态重复对假增长、延迟 p50/p95（**4 核口径写清**）。
3. 门槛冻结产物：`thresholds-v1.json` + 冻结记录（时间、commit、文件哈希、开发集范围），
   按 §9.3 **先冻结、后看锁定测试集**。
4. 四组消融（每组都要"改坏了它会红"）：
   - `H_profile` vs `cluster_h_px`（**必须分别报 `mask_source=cluster_raw` 与 `cluster_minus_bag`**，
     spec 未写明，本票补上）；
   - H1 vs H3 的 `t(x)`；
   - B1 模板折线 vs B2 图像自带袋沿；
   - 归属成功 vs 降级 Level 0 的比例，以及降级后锚点级曲线是否仍可比。
5. `delta` QA：`|(H_B − H_A) − delta_AB| ≤ tol`。
   `tol` 由**静态重复对**的噪声底定（初值 2–3 px，登记进 §4.5 表）；
   超差标 `baseline_or_mask_inconsistent`，**保留证据、不用于校准、不进主曲线、不进精度分子**。

## 2. 口径（写死）

| 项 | 口径 |
| --- | --- |
| 汇总粒度 | 逐误差项 × 分层（层/光照/阶段/事件）各报 MAE、RMSE、有效率；**禁止只报一个总分** |
| 有效率 | 有效袋数 / 应有袋数（模板槽位）；与误差同时报，单独报误差会掩盖"只测了简单的帧" |
| 归属正确率 | 只在前排、只在一袋一簇可判定时计入；`association_ambiguous` 不算错，单独计数 |
| 宽度相对误差 | 分母取人工袋宽；**排除粘连袋**（`split_ok=false` / `merged_multiple_bags`） |
| 延迟 | 单帧端到端，4 核；报 p50/p95，与 §6.1 的 3.75 s 同口径 |
| `delta` 比较 | **必须同 `mask_source`、同 pipeline 版本**；跨分割来源的比较只能作诊断，不判超差 |
| 锁定测试集 | 只在冻结后跑；每次查看记 commit 与时间；**不回头改阈值** |

**门槛（初值，照 §9.5；冻结后不回改）**

| 指标 | 初值 |
| --- | --- |
| `H_profile` MAE | ≤ 5 px |
| `b(x)` MAE | ≤ 4 px |
| `t(x)` MAE | ≤ 4 px |
| 宽度相对误差 | ≤ 10%（不粘连袋） |
| 归属正确率 | ≥ 95%（前排） |
| 静态重复假增长 | ≤ 1 px |
| 延迟 p95 | ≤ 3.75 s/帧 |
| 分层回归 | 任一层 `H_profile` 误差不差于基线 10% |

> **"基线"是谁必须在冻结前写死**：§9.5 的"分层回归"只说"不差于基线 10%"，
> 未说是 H2 还是 `cluster_h_px`。本票要求在 `thresholds-v1.json` 里显式写
> `regression_baseline=<method>`，否则这条门槛不可执行。

## 3. 验收（缺一条不算过）

- [ ] 同一条命令复跑两次 ⇒ 逐位一致的 CSV（种子与排序固定）
- [ ] **五组方法各自单独可跑**；任一组缺失不得静默用别组顶替（缺 H3 就报 `unavailable`）
- [ ] 每条门槛都有一个"改坏了会红"的自检，至少四条：
      `b(x)` 整体平移 5 px ⇒ `b` MAE 超门；`t(x)` 腐蚀 4 px ⇒ `t` MAE 超门；
      掩码与袋合并 ⇒ 宽度相对误差超门；两帧互换 ⇒ 静态重复假增长超门
- [ ] 汇总 JSON 中 `H_profile` 的误差**不含** `cluster_h_px` 的数字（字段级断言）
- [ ] 消融 1 同时给出 `cluster_raw` / `cluster_minus_bag` 两行，相关系数与差值都在报告里；
      **不得只给结论**（"先别下结论"是 spec 原文）
- [ ] `delta` 一致性检查：不满足"同 `mask_source`/同版本"的对**直接跳过并计数**，
      不参与 `tol` 标定、不参与门槛
- [ ] 冻结记录可核：`thresholds-v1.json` 的哈希与 commit 在报告里；
      锁定测试集的结果出现在冻结**之后**的提交里
- [ ] 不改 DDL、不写毫米列、不训练模型、不读生产（只读归档帧）

## 4. 不做什么

- **不训模型、不调模型参数**（P2-1）
- **不用锁定测试集做模型选择或阈值选择**（§9.3）
- 不把 `delta` 升级成主曲线（§4.7 已裁定；本票只做 QA）
- 不出"整体长势"结论：报告只回答"这一帧这一点量得准不准"
- 不改 `analysis/**`、`web/**`（消费侧在票 16）

## 5. 复跑

```bash
# 单组对照（示例）
uv run --no-project --offline --with numpy --with pillow --with opencv-python-headless \
  python measure/tools/eval_profile_metrics.py \
  --frames docs/patrol/growth-algo/annotation-config/pull-list-eval-v6.tsv \
  --labels <label-dir> --method h1 --out .scratch/eval-v6/h1

# 消融 1（两种 mask_source 各一行）
python measure/tools/eval_profile_metrics.py ... --method h3 --mask-source cluster_raw
python measure/tools/eval_profile_metrics.py ... --method h3 --mask-source cluster_minus_bag
```

## Comments

- 2026-10-20 开票：v6 的 §9.5 只有"要报什么"，没有"谁来跑、门槛怎么冻结、消融怎么判"。
  本票是它的执行层；`delta` 的 `tol` 与"分层回归基线"两处 spec 没写死，已在 §2 标注为必须显式登记。

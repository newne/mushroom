# 08 — 修断链：把图像生长描述接进决策提示词

**Spec:** ../spec.md §2.4

**What to build:** `decision_analyzer` 已经把 `llama_description` 读进 `current_data`，但 `template_renderer` 的 `growth_stage` 槽填的是 `semantic_description`（由设备设定点拼出来的文本）。把照片的生长描述接进去。

**Blocked by:** 无（算法侧独立）

**Status:** needs-info（要修的那条链路的输入数据已停半年，先确认拍照识别现在指哪条）

- [ ] `template_renderer.py:364` 的 `growth_stage` 改为优先用 `llama_description`，缺时回退 `semantic_description`
- [ ] 或在提示词里两者分列、不互相顶替（推荐分列，避免"设备文本冒充生长阶段"再来一次）
- [ ] 用一次真实推理对比改动前后的输出差异，留存样本
- [ ] 不动 `is_growth_phase`、D 分档等其它口径

## Comments

- 2026-10-12 写票。这是"结合拍照识别的生长情况"这句话的字面缺口：照片只通过 CLIP 相似案例（历史案例携带的设备参数）间接影响建议，生长文字本身没进提示词。
- 2026-10-12 上机核实：`mushroom_embedding` 只有 890 行、最后一条 `2026-03-27` —— 老的 CLIP/LLaMA 链路**已停半年**。同时巡检侧的 `analysis.measurements` 是 0 行（`measure_fn` 未接）。也就是说**今天两条拍照识别链路都不产生长数值**。这一票在数据链路恢复前改了看不出效果，先确认现场现在认哪条链路，再决定要不要动 `template_renderer`。

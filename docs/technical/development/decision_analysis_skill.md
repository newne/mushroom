# 鹿茸菇环境参数调节 Skill 设计说明

> 这里的 `skill` 指：沉淀“行业经验 + 现场运营经验”的可复用调控能力单元，用于指导温度/湿度/CO2及设备参数调节。

## 1. Skill 的目标

- 让模型输出更接近资深种植员调控习惯，而不仅依赖相似图像。
- 把“经验”结构化沉淀，支持快速复用、迭代、淘汰。
- 在现场数据波动时，提供可解释的优先建议和安全边界。

## 2. Skill 最小结构（建议）

每条 Skill 建议包含以下字段：

- `skill_id`: 技能唯一标识。
- `name`: 技能名称（例：出菇中期控湿防畸形）。
- `source`: 经验来源（`industry` / `onsite` / `expert`）。
- `priority`: 优先级（`high` / `medium` / `low`）。
- `applicable_conditions`: 适用条件。
  - `growth_day_range`
  - `season`
  - `env`（温湿度/CO2区间）
  - `room_tags`（可选）
- `actions`: 具体调节动作（设备、点位、目标区间、建议步长）。
- `risk_guardrails`: 安全边界（上限/下限、禁止组合、回滚条件）。
- `success_criteria`: 成功判据（环境稳定时长、品质指标、能耗指标）。
- `evidence`: 证据元数据（样本批次、命中次数、最近验证时间、操作者）。
- `status`: `active` / `candidate` / `deprecated`。

## 3. Skill 使用策略（决策时）

1. 先按 `growth_day + env` 匹配候选 Skill。  
2. 按 `priority + evidence.verified_count + recency` 排序。  
3. 产出建议时优先遵循 Skill 的目标区间和安全边界。  
4. 若与模型自由生成冲突，以 Skill 边界为准（避免越界调节）。  
5. 执行后记录效果，反哺 `evidence`，形成闭环。

## 4. 经验采集模板（现场复盘）

每次调节后建议记录：

- 时间、库房、批次、阶段（`in_day_num`）
- 调节前环境（温度/湿度/CO2）
- 调节动作（设备+点位+改变量）
- 30/60/120 分钟效果
- 质量与风险观察（畸形率、病害、能耗）
- 结论：可复用 / 待观察 / 不建议

## 5. 版本化建议

- Skill 文件采用版本字段：`skill_library_version`。
- 新增经验先入 `candidate`，连续多批次有效后转 `active`。
- 定期清理 `deprecated`，保留历史追溯。

## 6. 与当前系统的对接建议（不改提示词模板）

- 通过后处理约束：对模型建议值做 Skill 区间校正（clamp/step）。
- 将命中 Skill 写入 metadata，便于看板展示“建议来源”。
- 当相似图像缺失时，仍可依赖 Skill 产出稳定建议。

---

如果你同意，我下一步可以把 `src/configs/cultivation_skill_library.json` 正式接入 `DecisionAnalyzer` 的后处理流程（不改 `decision_prompt.jinja`）。

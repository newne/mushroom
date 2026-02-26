# 决策分析 Dify/MaxKB 重构建议（safe_batch_decision_analysis）

## 1. 结论（先看这个）

- **主编排平台建议：Dify**
- **知识库补充建议：MaxKB（可选）**
- **当前阶段不建议：用 MaxKB 直接替代 safe_batch_decision_analysis 全流程**

原因：当前流程是“定时调度 + 多步骤编排 + 结构化输出校验 + 数据库存储”，属于工作流系统能力，Dify更匹配；MaxKB更擅长知识检索问答。

---

## 2. 现状链路与改造边界

现状关键节点：

1. 调度入口：`src/scheduling/tasks/job_registry.py` 中 `safe_batch_decision_analysis`
2. 任务编排：`src/decision_analysis/tasks.py`
3. 业务分析主链路：`src/decision_analysis/decision_analyzer.py`
4. 结果拼装：`src/scripts/analysis/run_enhanced_decision_analysis.py`
5. 规则校验/约束：`src/decision_analysis/output_handler.py`
6. 落库：`store_decision_analysis_results`（静态配置/动态结果）

**建议改造边界**（最稳妥）：
- 保留本地 Python：调度、数据获取、结果校验、落库
- 迁移到 Dify：Prompt 编排、模型路由、多模型策略、可观测性

---

## 3. Dify vs MaxKB 能力匹配

### Dify 适配项（强匹配）

- 工作流编排（多节点、条件分支、重试）
- Prompt 版本管理（可灰度）
- 模型切换与参数管理（温度、max_tokens、fallback）
- API 调用集成，便于替换 `llm_client`

### MaxKB 适配项（中匹配）

- 设备SOP、异常处理手册、历史案例作为知识库检索
- 用于增强上下文，不适合承担主流程状态机

### 不建议

- 用 MaxKB 直接承接“按库房批处理 + 严格结构化输出 + 强约束落库”主链路

---

## 4. 推荐目标架构（Hybrid）

```text
APScheduler(job_registry)
    -> safe_batch_decision_analysis
        -> safe_decision_analysis_for_room(room_id)
            -> 本地数据采集/特征抽取（保留）
            -> 调用 Dify Workflow API（替代 llm_client）
            -> 本地 output_handler 二次校验（保留）
            -> store_decision_analysis_results 落库（保留）
```

关键原则：
- **Dify 负责“生成”**
- **本地代码负责“约束与落库”**

---

## 5. 分阶段实施（建议 4 阶段）

### Phase 0：准备（1~2天）

- 梳理当前输入/输出契约：
  - 输入：room_id、analysis_datetime、current_data、env_stats、device_changes、similar_cases
  - 输出：设备参数建议结构（必须兼容 `output_handler`）
- 新增 feature flag（建议）：`DECISION_ENGINE=local|dify`

### Phase 1：最小接入（2~4天）

- 新增 `decision_analysis/dify_client.py`（仅做 API 调用）
- 在 `safe_decision_analysis_for_room` 内保留原流程，仅将 LLM 调用替换为 Dify
- 强制保留本地 `validate_and_format`

### Phase 2：灰度双跑（3~7天）

- 同一批次同时跑 local 与 dify（不同时落库，或分表）
- 对比指标：
  - JSON 合法率
  - 参数越界率
  - 建议一致率
  - 平均耗时
  - 失败/重试率

### Phase 3：切主与保底（1~2天）

- 默认切到 Dify
- 保留 local fallback：Dify 超时/异常时回退本地 llm_client

---

## 6. 风险点与控制

1. **结构化输出漂移**
   - 控制：本地二次校验必保留
2. **时延抖动**
   - 控制：API 超时 + 重试 + 回退
3. **提示词升级导致行为波动**
   - 控制：Prompt 版本号落库
4. **平台依赖风险**
   - 控制：`DECISION_ENGINE` 一键切回 local

---

## 7. 具体落地建议（本项目）

- 保持 `job_registry.py` 调度方式不变
- 在 `decision_analysis/tasks.py` 增加引擎选择：
  - `local`：现有 `DecisionAnalyzer + llm_client`
  - `dify`：`DecisionAnalyzer` 前三步保留，LLM 调用改 Dify
- 本地 `output_handler.validate_and_format` 不删除
- `store_decision_analysis_results` 不变，保障下游兼容

---

## 8. 是否要引入 MaxKB

建议作为二期增强：
- 用 MaxKB 管理设备知识、告警处置经验
- Dify workflow 中增加知识检索节点，拼接到 prompt context
- 不把 MaxKB 用作主流程引擎

---

## 9. 推荐路径（最终）

- **短期（最快）：Dify 接管 LLM 生成，本地保留校验+落库**
- **中期（稳定）：Dify + MaxKB（检索增强）**
- **长期（治理）：完善评测看板，统一 Prompt/模型版本管理**

# 决策提示词 MLflow 注册与结构化占位使用说明

## 目标

- 对 `safe_batch_decision_analysis` 使用的提示词进行版本化管理。
- 通过 MLflow Prompt Registry 注册新版本（新增版本，不覆盖历史版本）。
- 使用模板占位 `{knowledge_base_section}` 注入知识库内容，避免运行时字符串拼接。

## 配置示例

在 `src/configs/settings.toml` 中增加或确认以下配置：

```toml
[default]
decision_prompt_registry_name="decision_analysis_structured"
decision_prompt_register_on_start=true
decision_prompt_cache_ttl_seconds=1800

[default.data_source_url]
prompt_decision_analysis="prompts:/decision_analysis_structured/1"
prompt_decision_analysis_bak="http://{host}/prompt/api/v1/prompts/decision-analysis/active"
```

说明：

- `decision_prompt_registry_name`：MLflow Prompt 名称。
- `decision_prompt_register_on_start`：是否在运行时将当前模板注册为新版本。
- `decision_prompt_cache_ttl_seconds`：进程内缓存秒数，减少重复加载。
- `prompt_decision_analysis`：优先从 MLflow URI 读取。
- `prompt_decision_analysis_bak`：MLflow 不可用时，作为 API 回退地址。

## 模板结构化占位

在 `src/configs/decision_prompt.jinja` 中加入：

```text
<KB_CONTENT_START>
{knowledge_base_section}
<KB_CONTENT_END>
```

运行时由 `TemplateRenderer` 注入内容：

- 有知识库内容：注入真实文本。
- 无知识库内容：注入默认说明文本。

## 运行流程

1. `run_enhanced_decision_analysis.py` 调用 `resolve_decision_prompt_template(...)`。
2. `DecisionPromptRegistryService` 按顺序尝试：
   - MLflow Prompt URI
   - API 回退地址
   - 本地模板文件回退
3. 若启用 `decision_prompt_register_on_start=true`，会调用 `mlflow.genai.register_prompt(...)` 注册新版本。
4. `DecisionAnalyzer -> TemplateRenderer.render_enhanced(...)` 通过 `knowledge_base_content` 渲染占位，不做字符串拼接。

## 验证建议

- 单元测试：
  - `pytest tests/unit/test_decision_prompt_manager.py`
- 任务链路验证：
  - 运行 `safe_batch_decision_analysis`，检查日志中的 `prompt_source/prompt_uri/registered_prompt_uri` 元数据。

## 兼容性说明

- 现有 Prompt URI 不可用时会自动回退，不阻塞业务任务。
- 模板若未包含知识库占位，系统会自动补齐标准区块。

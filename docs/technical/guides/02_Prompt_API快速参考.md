# 提示词API快速参考

## 快速开始

### 1. 基本使用
```python
from utils.get_data import GetData
from global_const.global_const import settings

# 创建实例
get_data = GetData(
    urls=settings.data_source_url,
    host=settings.host.host,
    port=settings.host.port
)

# 获取提示词
prompt = get_data.get_mushroom_prompt()
```

### 2. 带降级处理
```python
prompt = get_data.get_mushroom_prompt()
if not prompt:
    prompt = settings.llama.mushroom_descripe_prompt
```

## API配置

### URL
```
http://10.77.77.39/prompt/api/v1/prompts/role-instruction/active
```

### 认证
```
Authorization: Bearer 4525d65ec96c4e3abade57493ac3a171
```

### 配置位置
- URL: `src/configs/settings.toml` → `[default.data_source_url].prompt_mushroom_description`
- Token: `src/configs/.secrets.toml` → `[development.prompt].backend_token`

## 测试命令

```bash
# 测试API功能
python scripts/test_prompt_api.py

# 查看使用示例
python examples/prompt_api_usage_example.py
```

## API响应格式

API返回以下JSON格式：

```json
{
  "success": true,
  "data": {
    "content": {
      "template": "提示词内容..."
    }
  }
}
```

**提示词位置**: `data.content.template`

## 错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| 连接超时 | 10秒后超时，使用默认提示词 |
| 连接失败 | 记录错误，使用默认提示词 |
| HTTP错误 | 记录状态码，使用默认提示词 |
| JSON解析错误 | 记录错误，使用默认提示词 |

## 日志标识

所有日志以 `[Prompt API]` 开头，便于过滤和监控：

```
[Prompt API] 正在从API获取提示词: http://...
[Prompt API] 成功获取提示词，长度: 5234 字符
[Prompt API] API请求失败，状态码: 404
[Prompt API] API获取失败，使用配置文件中的默认提示词
```

## 关键特性

- ✅ **缓存机制**: 首次从API获取，后续使用缓存
- ✅ **自动降级**: API失败时自动使用配置文件默认值
- ✅ **超时控制**: 10秒超时，避免长时间等待
- ✅ **详细日志**: 完整的请求和错误日志
- ✅ **多格式支持**: 自动识别多种API响应格式

## 修改的文件

| 文件 | 修改内容 |
|-----|---------|
| `src/utils/get_data.py` | 添加 `get_mushroom_prompt()` 方法 |
| `src/utils/mushroom_image_encoder.py` | 使用动态获取的提示词 |
| `scripts/test_prompt_api.py` | 测试脚本（新建） |
| `examples/prompt_api_usage_example.py` | 使用示例（新建） |
| `docs/prompt_api_integration_guide.md` | 详细文档（新建） |

## 常见问题

### Q: API请求失败怎么办？
A: 系统会自动降级到配置文件中的默认提示词，不影响正常使用。

### Q: 如何刷新缓存？
A: 创建新的 `GetData` 实例即可刷新缓存。

### Q: 如何监控API状态？
A: 查看日志中的 `[Prompt API]` 标识，监控成功率和响应时间。

### Q: 生产环境如何配置？
A: 更新 `settings.toml` 和 `.secrets.toml` 中的 `[production]` 部分。

## 相关文档

- 📖 [详细集成指南](docs/prompt_api_integration_guide.md)
- 📝 [完整实现总结](PROMPT_API_INTEGRATION_SUMMARY.md)
- 🧪 [测试脚本](scripts/test_prompt_api.py)
- 💡 [使用示例](examples/prompt_api_usage_example.py)

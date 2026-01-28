# 提示词API集成指南

## 概述

本文档说明如何使用动态API获取蘑菇描述提示词，替代原有的静态配置方式。

## 功能特性

- ✅ 从远程API动态获取提示词
- ✅ 自动缓存机制，避免频繁请求
- ✅ 完善的错误处理和日志记录
- ✅ 自动降级到配置文件中的默认提示词
- ✅ 配置化的API地址和认证信息

## 配置说明

### 1. API配置 (settings.toml)

在 `src/configs/settings.toml` 中添加API URL配置：

```toml
[default.data_source_url]
prompt_mushroom_description="http://{host}/prompt/api/v1/prompts/role-instruction/active"
```

### 2. 认证配置 (.secrets.toml)

在 `src/configs/.secrets.toml` 中添加认证令牌：

```toml
[development.prompt]
username = "admin"
password = "admin123"
backend_token = "Bearer 4525d65ec96c4e3abade57493ac3a171"

[production.prompt]
username = "admin"
password = "admin123"
backend_token = "Bearer 4525d65ec96c4e3abade57493ac3a171"
```

## 使用方法

### 基本使用

```python
from global_const.global_const import settings
from utils.get_data import GetData

# 初始化GetData实例
get_data = GetData(
    urls=settings.data_source_url,
    host=settings.host.host,
    port=settings.host.port
)

# 获取提示词
prompt = get_data.get_mushroom_prompt()

if prompt:
    print(f"成功获取提示词，长度: {len(prompt)}")
else:
    print("获取提示词失败")
```

### 在mushroom_image_encoder.py中使用

原有代码：
```python
{
    "role": "system",
    "content": f"{settings.llama.mushroom_descripe_prompt}"
}
```

修改后：
```python
# 在类初始化时创建GetData实例
self.get_data = GetData(
    urls=settings.data_source_url,
    host=settings.host.host,
    port=settings.host.port
)

# 使用时获取提示词
prompt = self.get_data.get_mushroom_prompt()
{
    "role": "system",
    "content": prompt or settings.llama.mushroom_descripe_prompt  # 降级处理
}
```

## API响应格式

API返回以下JSON格式：

```json
{
  "success": true,
  "data": {
    "id": 23,
    "promptId": "role-instruction",
    "version": "1.3.0",
    "content": {
      "inputSchema": {...},
      "outputSchema": {...},
      "template": "提示词内容...",
      "constraints": {...}
    },
    "status": "ready",
    "createdBy": "admin",
    "createdAt": "2026-01-15T06:58:04.746Z"
  }
}
```

**提示词位置**: `data.content.template`

代码会自动从以下路径提取提示词（按优先级）：
1. `data.content.template` （主要路径）
2. `data.content`
3. `data.prompt`
4. `content`
5. `prompt`
6. 其他常见字段名

## 错误处理

### 自动降级机制

当API请求失败时，系统会自动降级到配置文件中的默认提示词：

```python
# 如果API失败，使用配置文件中的提示词
fallback_prompt = settings.llama.mushroom_descripe_prompt
```

### 错误类型

系统会处理以下错误类型：

1. **连接超时** - 10秒超时限制
2. **连接失败** - 网络不可达
3. **HTTP错误** - 非200状态码
4. **JSON解析错误** - 响应格式错误
5. **未知错误** - 其他异常情况

所有错误都会记录详细日志，便于排查问题。

## 缓存机制

为了提高性能和减少API请求，系统实现了简单的缓存机制：

- 首次调用时从API获取提示词
- 后续调用直接返回缓存的提示词
- 缓存在GetData实例的生命周期内有效

如需刷新缓存，可以创建新的GetData实例。

## 测试

运行测试脚本验证功能：

```bash
python scripts/test_prompt_api.py
```

测试内容包括：
1. 从API获取提示词
2. 验证缓存机制
3. 显示配置信息

## 日志示例

成功获取提示词：
```
[Prompt API] 正在从API获取提示词: http://10.77.77.39/prompt/api/v1/prompts/role-instruction/active
[Prompt API] 成功获取提示词，长度: 5234 字符
```

API失败降级：
```
[Prompt API] API请求失败，状态码: 404, 响应: Not Found
[Prompt API] API获取失败，使用配置文件中的默认提示词
[Prompt API] 已加载配置文件中的后备提示词
```

## 注意事项

1. **网络依赖** - 确保应用能够访问API服务器
2. **认证令牌** - 定期更新backend_token以保证安全性
3. **超时设置** - 默认10秒超时，可根据网络情况调整
4. **降级策略** - 保留配置文件中的默认提示词作为后备
5. **日志监控** - 关注日志中的API请求状态

## 迁移步骤

从静态配置迁移到API获取：

1. ✅ 在settings.toml中添加API URL配置
2. ✅ 在.secrets.toml中添加认证令牌
3. ✅ 修改代码使用`get_mushroom_prompt()`方法
4. ✅ 运行测试脚本验证功能
5. ✅ 监控日志确保正常工作
6. ⚠️ 保留配置文件中的默认提示词作为后备

## 故障排查

### 问题1: API请求超时

**症状**: 日志显示"API请求超时"

**解决方案**:
- 检查网络连接
- 验证API服务器是否正常运行
- 考虑增加超时时间

### 问题2: 认证失败

**症状**: 状态码401或403

**解决方案**:
- 验证backend_token是否正确
- 检查token是否过期
- 确认API权限配置

### 问题3: 响应格式错误

**症状**: "API响应中未找到提示词内容"

**解决方案**:
- 检查API实际返回的JSON结构
- 根据实际格式调整字段提取逻辑
- 联系API提供方确认响应格式

## 相关文件

- `src/utils/get_data.py` - 核心实现
- `src/configs/settings.toml` - API URL配置
- `src/configs/.secrets.toml` - 认证配置
- `scripts/test_prompt_api.py` - 测试脚本
- `src/utils/mushroom_image_encoder.py` - 使用示例

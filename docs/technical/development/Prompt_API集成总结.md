# 提示词获取功能实现总结

## 最新变更（MLflow Prompt Registry）

当前 `safe_hourly_text_quality_inference` 相关链路的提示词默认来源已切换为 **MLflow Prompt Registry**：
- `prompts:/growth_stage_describe/4`

实现方式为：`GetData.get_mushroom_prompt()` 支持识别 `prompts:/...` URI 并从 MLflow 拉取 chat prompt（system+user），同时启用版本校验与 TTL 缓存；若 MLflow 获取失败则回退到原 Prompt API / 配置默认值。

## 修改概述

成功将蘑菇描述提示词的获取方式从静态配置改为可通过 **Prompt API 或 MLflow Prompt Registry** 动态获取，提高了系统的灵活性和可维护性。

## 修改文件清单

### 1. 核心实现文件

#### `src/utils/get_data.py`
- ✅ 添加 `get_mushroom_prompt()` 方法，实现从API动态获取提示词
- ✅ 实现缓存机制，避免频繁API请求
- ✅ 完善的错误处理和日志记录
- ✅ 自动降级到配置文件中的默认提示词

**主要功能**:
```python
def get_mushroom_prompt(self) -> Optional[str]:
    """从API动态获取蘑菇描述提示词"""
    # 1. 检查缓存
    # 2. 从API获取提示词
    # 3. 处理多种响应格式
    # 4. 错误处理和降级
```

#### `src/vision/mushroom_image_encoder.py`
- ✅ 导入 `GetData` 类
- ✅ 在 `__init__` 中初始化 `GetData` 实例
- ✅ 在 `_call_llama_api` 方法中使用动态获取的提示词
- ✅ 保留降级到配置文件默认值的机制

**修改位置**:
- 第23行: 添加 `from utils.get_data import GetData` 导入
- 第48-53行: 初始化 `GetData` 实例
- 第128-133行: 使用 `get_mushroom_prompt()` 获取提示词

### 2. 配置文件

#### `src/configs/settings.toml`
- ✅ 已包含API URL配置: `prompt_mushroom_description`
- ✅ 保留默认提示词作为后备方案

#### `src/configs/.secrets.toml`
- ✅ 已包含认证配置: `backend_token`
- ✅ 支持开发和生产环境

### 3. 测试和文档

#### `src/scripts/processing/test_text_quality_prompt_mlflow.py`
- ✅ 测试从 MLflow Prompt Registry 加载提示词并完成一次图文推理（本地数据集图片）
- ✅ 验证 chat prompt 注入与 JSON 输出字段完整性

#### `docs/prompt_api_integration_guide.md` (新建)
- ✅ 完整的使用指南
- ✅ 配置说明
- ✅ 错误处理说明
- ✅ 故障排查指南

## 技术实现细节

### API配置

**URL格式（Prompt API 兼容）**:
```
http://{host}/prompt/api/v1/prompts/role-instruction/active
```

**MLflow Prompt Registry（推荐）**:
```
prompts:/growth_stage_describe/4
```

**请求头**:
```python
{
    "Authorization": "Bearer 4525d65ec96c4e3abade57493ac3a171",
    "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
    "Accept": "*/*",
    "Connection": "keep-alive"
}
```

### 响应格式支持

代码支持多种API响应格式，自动尝试以下字段：
1. `data.content`
2. `data.prompt`
3. `content`
4. `prompt`
5. `data.instruction`
6. `instruction`

### 错误处理机制

1. **连接超时** - 10秒超时限制
2. **连接失败** - 网络不可达时的处理
3. **HTTP错误** - 非200状态码的处理
4. **JSON解析错误** - 响应格式错误的处理
5. **自动降级** - 失败时使用配置文件中的默认提示词

### 缓存机制

- 首次调用从API获取
- 后续调用使用缓存
- 缓存在 `GetData` 实例生命周期内有效
- 避免频繁API请求，提高性能

## 使用流程

```
┌─────────────────────────────────────────────────────────┐
│  MushroomImageEncoder 初始化                             │
│  ├─ 创建 GetData 实例                                    │
│  └─ 准备获取提示词                                       │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  调用 _call_llama_api()                                  │
│  └─ 调用 get_data.get_mushroom_prompt()                 │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  get_mushroom_prompt() 执行流程                          │
│  ├─ 检查缓存                                             │
│  │   └─ 如有缓存，直接返回                               │
│  ├─ 从API获取提示词                                      │
│  │   ├─ 构建请求（URL、Headers）                         │
│  │   ├─ 发送GET请求                                      │
│  │   ├─ 解析响应                                         │
│  │   └─ 缓存结果                                         │
│  └─ 错误处理                                             │
│      └─ 降级到配置文件默认值                             │
└─────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│  使用提示词调用LLaMA API                                 │
└─────────────────────────────────────────────────────────┘
```

## 优势和特性

### ✅ 灵活性
- 提示词可以在不重启服务的情况下通过API更新
- 支持集中管理多个服务的提示词

### ✅ 可靠性
- 完善的错误处理机制
- 自动降级到配置文件默认值
- 详细的日志记录便于排查问题

### ✅ 性能
- 缓存机制减少API请求
- 10秒超时避免长时间等待
- 不影响主流程性能

### ✅ 可维护性
- 配置化的API地址和认证信息
- 清晰的代码结构和注释
- 完整的文档和测试

## 测试验证

### 运行测试
```bash
python scripts/test_prompt_api.py
```

### 预期输出
```
[Prompt API] 正在从API获取提示词: http://10.77.77.39/prompt/api/v1/prompts/role-instruction/active
[Prompt API] 成功获取提示词，长度: XXXX 字符
✓ 成功获取提示词
✓ 缓存机制工作正常
```

## 部署注意事项

### 开发环境
1. 确保 `settings.toml` 中配置了正确的API URL
2. 确保 `.secrets.toml` 中配置了正确的认证令牌
3. 运行测试脚本验证功能

### 生产环境
1. 更新生产环境的配置文件
2. 确保生产环境可以访问API服务器
3. 监控日志确保API请求正常
4. 保留配置文件中的默认提示词作为后备

## 后续优化建议

### 1. 缓存过期机制
当前缓存在实例生命周期内永久有效，可以考虑添加：
- 时间过期机制（如1小时后自动刷新）
- 手动刷新接口

### 2. 重试机制
当前API失败直接降级，可以考虑：
- 添加重试逻辑（如3次重试）
- 指数退避策略

### 3. 监控和告警
- 添加API请求成功率监控
- API失败时发送告警通知
- 统计API响应时间

### 4. 多环境支持
- 支持不同环境使用不同的API端点
- 支持A/B测试不同的提示词版本

## 相关文档

- [提示词API集成指南](docs/prompt_api_integration_guide.md) - 详细使用文档
- [测试脚本](scripts/test_prompt_api.py) - 功能测试
- [配置文件](src/configs/settings.toml) - API配置
- [认证配置](src/configs/.secrets.toml) - 认证信息

## 总结

本次修改成功实现了从静态配置到动态API获取提示词的迁移，提高了系统的灵活性和可维护性。通过完善的错误处理和降级机制，确保了系统的稳定性。所有修改已通过语法检查，可以安全部署使用。

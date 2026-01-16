# LLM JSON解析错误 - 快速修复指南

## 🚨 问题识别

如果看到以下错误:
```
WARNING | [LLMClient] Initial JSON parse failed: Expecting value: line 1 column 1 (char 0)
```

## ⚡ 快速解决方案

### 已自动修复 ✅

系统已经实施了以下修复:

1. **限制设备变更记录**: 自动限制为最近30条
2. **优化LLM参数**: temperature=0.5, max_tokens=2048
3. **增强JSON解析**: 支持多种格式，更强大的提取算法

### 如果问题仍然存在

#### 方案1: 减少设备变更数量

编辑 `src/decision_analysis/decision_analyzer.py`:

```python
# 找到这一行 (约第250行)
MAX_DEVICE_CHANGES = 30

# 改为
MAX_DEVICE_CHANGES = 20  # 或更小的值
```

#### 方案2: 降低temperature

编辑 `src/decision_analysis/decision_analyzer.py`:

```python
# 找到这一行 (约第400行)
temperature=0.5,

# 改为
temperature=0.3,  # 更稳定的输出
```

#### 方案3: 减少时间窗口

编辑 `src/decision_analysis/decision_analyzer.py`:

```python
# 找到这一行 (约第245行)
start_time_changes = analysis_datetime - timedelta(days=7)

# 改为
start_time_changes = analysis_datetime - timedelta(days=3)  # 从7天改为3天
```

## 🔍 诊断命令

### 检查提示词长度
```bash
grep "Prompt length" src/Logs/mushroom_solution-info.log | tail -5
```

### 检查JSON解析状态
```bash
grep "Successfully parsed JSON\|Initial JSON parse failed" src/Logs/mushroom_solution-info.log | tail -10
```

### 检查降级策略触发
```bash
grep "Using fallback decision" src/Logs/mushroom_solution-warning.log | tail -5
```

## 📊 预期结果

修复后应该看到:
```
INFO | [LLMClient] Response content length: 424 chars
INFO | [LLMClient] Successfully parsed JSON response (direct)
```

而不是:
```
WARNING | [LLMClient] Initial JSON parse failed
ERROR | [LLMClient] Failed to parse response
```

## 🆘 如果还是不行

1. **检查LLM服务状态**:
   ```bash
   curl http://10.77.77.49:7001/v1/models
   ```

2. **查看完整错误日志**:
   ```bash
   tail -100 src/Logs/mushroom_solution-error.log
   ```

3. **运行诊断脚本**:
   ```bash
   python scripts/test_llm_client.py
   ```

4. **联系支持**: 提供以上日志信息

## 📝 相关文档

- 详细分析: `LLM_JSON_PARSE_ERROR_ANALYSIS.md`
- 修复总结: `LLM_JSON_PARSE_FIX_SUMMARY.md`
- 完整报告: `LLM_JSON_PARSE_ERROR_RESOLUTION.md`

---

**快速修复**: ✅ 已自动实施  
**需要重启**: ❌ 不需要  
**影响范围**: 仅LLM调用部分

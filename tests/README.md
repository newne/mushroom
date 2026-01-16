# 测试目录

本目录包含蘑菇种植智能调控系统的所有测试代码。

## 目录结构

### unit/ - 单元测试
测试单个模块或类的功能。

**测试文件：**
- `test_clip_matcher.py` - CLIP 匹配器测试
- `test_llm_client.py` - LLM 客户端测试
- `test_template_renderer.py` - 模板渲染器测试
- `test_output_handler.py` - 输出处理器测试
- `test_decision_analyzer.py` - 决策分析器测试

**运行单元测试：**
```bash
# 运行所有单元测试
pytest tests/unit/

# 运行特定测试文件
pytest tests/unit/test_clip_matcher.py

# 运行特定测试函数
pytest tests/unit/test_clip_matcher.py::test_find_similar_cases
```

### integration/ - 集成测试
测试多个模块之间的集成和交互。

**测试文件：**
- `test_validate_env_params_integration.py` - 环境参数验证集成测试
- `test_json_format_integration.py` - JSON 格式集成测试
- `verify_system_integration.py` - 系统集成验证

**运行集成测试：**
```bash
pytest tests/integration/
```

### performance/ - 性能测试
测试系统性能和响应时间。

**测试文件：**
- `test_query_performance.py` - 数据库查询性能测试
- `verify_task9_performance.py` - Task 9 性能验证

**运行性能测试：**
```bash
python tests/performance/test_query_performance.py
```

### functional/ - 功能测试
测试完整的功能流程和业务逻辑。

**测试文件：**
- `test_extract_device_changes.py` - 设备变更提取测试
- `test_extract_embedding_data.py` - 嵌入数据提取测试
- `test_extract_env_daily_stats.py` - 环境统计提取测试
- `test_find_similar_cases.py` - 相似案例查找测试
- `test_get_data_prompt.py` - 数据提示获取测试
- `test_validate_env_params.py` - 环境参数验证测试
- `test_llama_json_format.py` - LLaMA JSON 格式测试
- `test_llama_json_with_real_images.py` - LLaMA 真实图像测试

**运行功能测试：**
```bash
pytest tests/functional/
```

### debug/ - 调试脚本
用于调试和问题诊断的脚本。

**脚本文件：**
- `debug_llm_json_parse.py` - LLM JSON 解析调试
- `debug_prompt_config.py` - Prompt 配置调试

**使用调试脚本：**
```bash
python tests/debug/debug_llm_json_parse.py
```

### verification/ - 验证脚本
用于验证系统功能和数据完整性的脚本。

**脚本文件：**
- `verify_task_3_2.py` - Task 3.2 验证
- 其他验证脚本

**运行验证脚本：**
```bash
python tests/verification/verify_task_3_2.py
```

## 测试规范

### 命名规范
- 测试文件：`test_<module_name>.py`
- 测试类：`Test<ClassName>`
- 测试函数：`test_<function_name>`

### 测试结构
```python
import pytest
from module import function_to_test

class TestModuleName:
    """测试模块名称"""
    
    def setup_method(self):
        """每个测试方法前执行"""
        pass
    
    def teardown_method(self):
        """每个测试方法后执行"""
        pass
    
    def test_basic_functionality(self):
        """测试基本功能"""
        result = function_to_test()
        assert result == expected_value
    
    def test_edge_cases(self):
        """测试边界情况"""
        pass
    
    def test_error_handling(self):
        """测试错误处理"""
        with pytest.raises(ValueError):
            function_to_test(invalid_input)
```

### 测试覆盖率
```bash
# 运行测试并生成覆盖率报告
pytest --cov=src --cov-report=html tests/

# 查看覆盖率报告
open htmlcov/index.html
```

## 运行所有测试

```bash
# 运行所有测试
pytest tests/

# 运行测试并显示详细输出
pytest -v tests/

# 运行测试并显示打印输出
pytest -s tests/

# 运行测试并在第一个失败时停止
pytest -x tests/

# 运行特定标记的测试
pytest -m slow tests/
```

## 持续集成

测试应该在以下情况下自动运行：
1. 提交代码前
2. 创建 Pull Request 时
3. 合并到主分支前
4. 定期（每日/每周）

## 测试数据

测试数据应该：
1. 使用 fixtures 管理
2. 与生产数据隔离
3. 可重复和可预测
4. 清理测试后的数据

## 注意事项

1. **不要在测试中使用生产数据库**
2. **使用 mock 对象模拟外部依赖**
3. **测试应该独立且可重复**
4. **测试应该快速执行**
5. **测试应该有清晰的断言和错误消息**

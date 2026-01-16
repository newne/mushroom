# Examples Directory / 示例目录

This directory contains example scripts demonstrating how to use various modules in the mushroom growing solution system.

本目录包含演示如何使用蘑菇种植解决方案系统中各种模块的示例脚本。

## Available Examples / 可用示例

### 1. Decision Analysis Example / 决策分析示例
**File**: `decision_analysis_example.py`

Comprehensive examples demonstrating the decision analysis module for mushroom growing environment control.

演示蘑菇种植环境调控决策分析模块的综合示例。

**Features / 功能**:
- 8 different usage scenarios / 8个不同的使用场景
- Bilingual comments (Chinese/English) / 双语注释（中文/英文）
- Error handling demonstrations / 错误处理演示
- Output format examples / 输出格式示例
- Performance monitoring / 性能监控

**Usage / 使用方法**:
```bash
# Run all examples / 运行所有示例
python examples/decision_analysis_example.py

# Or import specific examples in your code / 或在代码中导入特定示例
python -c "from examples.decision_analysis_example import example_1_basic_usage; example_1_basic_usage()"
```

**Examples included / 包含的示例**:
1. Basic usage / 基本使用
2. Specify analysis datetime / 指定分析时间
3. Error handling / 错误处理
4. Output formats / 输出格式
5. Save to JSON / 保存为JSON
6. Batch analysis for multiple rooms / 批量分析多个库房
7. Custom configuration / 自定义配置
8. Performance monitoring / 性能监控

### 2. Prompt API Usage Example / 提示词API使用示例
**File**: `prompt_api_usage_example.py`

Examples demonstrating how to use the dynamic prompt API in different scenarios.

演示如何在不同场景下使用动态提示词API的示例。

**Features / 功能**:
- Basic usage / 基本使用
- Fallback handling / 降级处理
- Caching demonstration / 缓存演示
- LLaMA context integration / LLaMA上下文集成
- Error handling / 错误处理

**Usage / 使用方法**:
```bash
python examples/prompt_api_usage_example.py
```

## Requirements / 依赖要求

### System Requirements / 系统要求
- Python 3.8+
- PostgreSQL database with mushroom data / 包含蘑菇数据的PostgreSQL数据库
- LLaMA API endpoint configured / 配置好的LLaMA API端点

### Python Dependencies / Python依赖
- loguru
- dynaconf
- sqlalchemy
- pandas
- numpy
- requests

Install all dependencies / 安装所有依赖:
```bash
pip install -r requirements.txt
```

## Configuration / 配置

Before running examples, ensure you have configured:

运行示例前，请确保已配置：

1. **Database Connection / 数据库连接**
   - Edit `src/configs/settings.toml`
   - Set database host, port, username, password
   - 编辑 `src/configs/settings.toml`
   - 设置数据库主机、端口、用户名、密码

2. **LLaMA API / LLaMA API**
   - Edit `src/configs/settings.toml`
   - Set LLaMA endpoint URL and model name
   - 编辑 `src/configs/settings.toml`
   - 设置LLaMA端点URL和模型名称

3. **Secrets / 密钥**
   - Edit `src/configs/.secrets.toml`
   - Set database password and API keys
   - 编辑 `src/configs/.secrets.toml`
   - 设置数据库密码和API密钥

## Running Examples / 运行示例

### Quick Start / 快速开始

```bash
# Navigate to project root / 导航到项目根目录
cd /path/to/mushroom_solution

# Run decision analysis example / 运行决策分析示例
python examples/decision_analysis_example.py

# Run prompt API example / 运行提示词API示例
python examples/prompt_api_usage_example.py
```

### Running Specific Examples / 运行特定示例

```python
import sys
from pathlib import Path

# Add project root to path / 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# Import and run specific example / 导入并运行特定示例
from examples.decision_analysis_example import example_1_basic_usage

result = example_1_basic_usage()
```

## Output / 输出

### Console Output / 控制台输出
Examples use `loguru` for colorful, structured logging output.

示例使用 `loguru` 提供彩色、结构化的日志输出。

### File Output / 文件输出
Some examples generate output files:
- `decision_analysis_example_output.json` - Decision analysis results in JSON format

某些示例会生成输出文件：
- `decision_analysis_example_output.json` - JSON格式的决策分析结果

## Troubleshooting / 故障排除

### Common Issues / 常见问题

1. **Import Error / 导入错误**
   ```
   ModuleNotFoundError: No module named 'decision_analysis'
   ```
   **Solution / 解决方案**: Make sure you're running from the project root directory and `src` is in the Python path.
   
   确保从项目根目录运行，并且 `src` 在Python路径中。

2. **Database Connection Error / 数据库连接错误**
   ```
   sqlalchemy.exc.OperationalError: could not connect to server
   ```
   **Solution / 解决方案**: Check your database configuration in `settings.toml` and ensure PostgreSQL is running.
   
   检查 `settings.toml` 中的数据库配置，并确保PostgreSQL正在运行。

3. **LLaMA API Error / LLaMA API错误**
   ```
   requests.exceptions.ConnectionError: Failed to establish connection
   ```
   **Solution / 解决方案**: Verify LLaMA API endpoint is accessible and configured correctly.
   
   验证LLaMA API端点可访问且配置正确。

4. **No Data Found / 未找到数据**
   ```
   WARNING: No embedding data found for room 611
   ```
   **Solution / 解决方案**: This is expected if there's no data for the specified time/room. The system will use fallback strategies.
   
   如果指定时间/库房没有数据，这是正常的。系统将使用降级策略。

## Performance Notes / 性能说明

- **Example 6** (Batch analysis) analyzes 4 rooms and may take 2-5 minutes
- **Example 8** (Performance monitoring) runs 3 analyses and may take 1-3 minutes
- LLM API calls typically take 20-40 seconds per request

- **示例6**（批量分析）分析4个库房，可能需要2-5分钟
- **示例8**（性能监控）运行3次分析，可能需要1-3分钟
- LLM API调用通常每次请求需要20-40秒

## Contributing / 贡献

To add new examples:

添加新示例：

1. Create a new Python file in this directory / 在此目录中创建新的Python文件
2. Follow the existing example structure / 遵循现有示例结构
3. Include bilingual comments / 包含双语注释
4. Add error handling / 添加错误处理
5. Update this README / 更新此README

## Support / 支持

For questions or issues:
- Check the main project documentation / 查看主项目文档
- Review the design documents in `.kiro/specs/` / 查看 `.kiro/specs/` 中的设计文档
- Contact the development team / 联系开发团队

## License / 许可证

See the main project LICENSE file.

查看主项目LICENSE文件。

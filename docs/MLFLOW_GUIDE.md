
# MLflow 实验追踪与模型管理最佳实践指南

本指南旨在规范团队的 MLflow 使用流程，确保实验的可检索性、可复现性和模型的全生命周期管理。

## 1. 核心概念与规范

### 1.1 命名规范
- **Experiment Name**: 采用 `{project_name}/{module_name}` 格式。
  - 示例: `mushroom/vision_offline`, `mushroom/growth_prediction`
- **Run Name**: 建议包含日期、算法和关键特征，或使用自动生成的 `{timestamp}_{user}`。
- **Registered Model Name**: 采用小写字母和下划线，清晰描述模型用途。
  - 示例: `mushroom_growth_classifier`

### 1.2 标签体系 (Tags)
`ExperimentTracker` 会自动记录以下基础标签，用户可按需扩展：
- `os.user`: 运行用户
- `git.commit`: 代码版本 Hash
- `source.type`: 运行来源 (script/notebook)
- `model.type`: 模型架构 (e.g., ResNet50, LLaMA)
- `run.mode`: 运行模式 (training/validation/inference)

## 2. 快速上手

### 2.1 环境配置
确保安装依赖：
```bash
pip install mlflow sklearn pandas numpy
```

设置 Tracking URI（默认本地 `./mlruns`，生产环境请指向远程服务器）：
```bash
export MLFLOW_TRACKING_URI=http://your-mlflow-server:5000
```

### 2.2 使用 ExperimentTracker
我们封装了 `ExperimentTracker` 类以简化操作并强制规范。

```python
from src.utils.experiment_tracker import ExperimentTracker

# 初始化
tracker = ExperimentTracker("mushroom/demo")

# 开始运行
with tracker.start_run(run_name="my_run", tags={"model.type": "CNN"}):
    # 记录参数
    tracker.log_params({"learning_rate": 0.01, "batch_size": 32})
    
    # 记录环境依赖
    tracker.log_environment()
    
    # 记录指标
    tracker.log_metrics({"accuracy": 0.98, "loss": 0.05})
    
    # 记录并注册模型
    tracker.log_model(
        model=my_model,
        artifact_path="model",
        input_example=X_train[:1], # 自动生成 Signature
        registered_model_name="my_cnn_model"
    )
```

## 3. 模型生命周期管理

### 3.1 模型注册
通过 `log_model(..., registered_model_name="name")` 自动注册模型。
注册后，模型将进入 Model Registry，初始版本默认为 `Staging` 阶段（需手动或脚本控制，本工具类示例中设为 Staging）。

### 3.2 阶段流转 (Stage Transition)
- **None**: 初始状态
- **Staging**: 预发布/测试阶段
- **Production**: 生产发布阶段
- **Archived**: 归档

可以通过 MLflow UI 或 API 修改阶段：
```python
client = mlflow.MlflowClient()
client.transition_model_version_stage(
    name="my_model",
    version=1,
    stage="Production"
)
```

## 4. 操作手册

### 4.1 运行实验
```bash
python src/scripts/examples/mlflow_train_demo.py --n_estimators 200
```

### 4.2 查询与对比
启动 UI：
```bash
mlflow ui --port 5000
```
访问 `http://localhost:5000`，选择 Experiment，选中多个 Run 点击 "Compare" 进行参数和指标对比。

### 4.3 复现实验
查看 Run 的 `git.commit` 标签，检出对应代码版本：
```bash
git checkout <commit_hash>
```
查看 Run 的 Artifacts 中的 `requirements.txt`，恢复环境：
```bash
pip install -r requirements.txt
```
使用记录的参数重新运行脚本。

### 4.4 模型部署
加载指定版本的模型进行推理：
```python
import mlflow

# 加载 Staging 版本的模型
model = mlflow.pyfunc.load_model("models:/mushroom_growth_classifier/Staging")

# 推理
prediction = model.predict(data)
```

### 4.5 归档与清理
定期清理无效 Run（标记为 Deleted）：
```bash
mlflow gc --experiment-ids <experiment_id>
```

## 5. 测试验证
运行集成测试确保追踪链路正常：
```bash
pytest tests/test_mlflow_lifecycle.py
```

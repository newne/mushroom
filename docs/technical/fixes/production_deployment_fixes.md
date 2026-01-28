# 生产环境部署问题修复总结

## 问题概述

在生产环境容器部署后，系统出现以下关键问题：

1. **决策分析模块导入失败**：`No module named 'run_enhanced_decision_analysis'`
2. **设定点监控模块导入失败**：`No module named 'dataframe_utils'`
3. **MinIO SSL连接失败**：`[SSL] record layer failure`
4. **模型属性错误**：`type object 'MushroomImageEmbedding' has no attribute 'growth_stage'`

## 根本原因分析

### 1. 容器文件结构问题
- **问题**：Dockerfile只复制了`dist/src/`到容器的`/app`目录
- **影响**：`scripts`、`examples`等目录缺失，导致模块导入失败
- **原因**：构建脚本未将必要目录包含到`dist`中

### 2. Python路径配置问题
- **问题**：容器中的Python路径与开发环境不一致
- **影响**：相对导入和绝对导入都失败
- **原因**：`PYTHONPATH`配置不完整

### 3. SSL证书验证问题
- **问题**：MinIO使用自签名证书，容器环境SSL验证失败
- **影响**：无法访问图像存储，CLIP推理任务失败
- **原因**：生产环境SSL配置与开发环境不同

### 4. 数据库模型不一致
- **问题**：代码中仍使用已删除的`growth_stage`字段
- **影响**：统计查询失败
- **原因**：表结构优化后代码未完全同步

## 修复方案

### 1. Dockerfile修复

**文件**：`docker/Dockerfile`

```dockerfile
# 修复前
COPY dist/src/ ./

# 修复后
COPY dist/src/ ./src/
COPY scripts/ ./scripts/
COPY examples/ ./examples/
COPY models/ ./models/
COPY data/ ./data/

# 设置Python路径
ENV PYTHONPATH="/app:/app/src"
```

### 2. 启动脚本修复

**文件**：`docker/run.sh`

```bash
# 修复Python路径
export PYTHONPATH="$APP_ROOT:$APP_ROOT/src"

# 修复Streamlit启动路径
STREAMLIT_CMD="$PYTHON -m streamlit run src/streamlit_app.py ..."

# 修复FastAPI启动方式
FASTAPI_CMD="$PYTHON -c 'import sys; sys.path.insert(0, \"/app/src\"); from main import app; import uvicorn; uvicorn.run(app, host=\"0.0.0.0\", port=5000, workers=1)'"

# 修复定时任务启动路径
cd "$APP_ROOT/src"
nohup $PYTHON main.py 2>&1 | tee -a "$TIMER_LOG" &
```

### 3. 构建脚本修复

**文件**：`docker/build_codeenigma.sh`

```bash
# 确保必要目录被复制到dist
if [ "${ENCRYPT}" = "false" ]; then
    cp -r src dist/
    cp -r scripts dist/ 2>/dev/null || echo "Warning: scripts directory not found"
    cp -r examples dist/ 2>/dev/null || echo "Warning: examples directory not found"
fi

# 加密版本也需要复制scripts等目录
[ -d scripts ] && cp -r scripts dist/ 2>/dev/null || echo "Warning: scripts directory not found"
[ -d examples ] && cp -r examples dist/ 2>/dev/null || echo "Warning: examples directory not found"
```

### 4. 代码导入路径修复

**文件**：`src/tasks/decision/decision_tasks.py`

```python
# 修复决策分析模块导入
import os
app_root = os.environ.get('APP_ROOT', '/app')
scripts_path = os.path.join(app_root, 'scripts', 'analysis')
src_path = os.path.join(app_root, 'src')

# 添加必要的路径到sys.path
for path in [app_root, src_path, scripts_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from run_enhanced_decision_analysis import execute_enhanced_decision_analysis
except ImportError as import_error:
    # 备用导入方式
    import importlib.util
    script_file = os.path.join(scripts_path, "run_enhanced_decision_analysis.py")
    if os.path.exists(script_file):
        spec = importlib.util.spec_from_file_location("run_enhanced_decision_analysis", script_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        execute_enhanced_decision_analysis = module.execute_enhanced_decision_analysis
```

**文件**：`src/tasks/monitoring/monitoring_tasks.py`

```python
# 修复dataframe_utils导入
import os
app_root = os.environ.get('APP_ROOT', '/app')
src_path = os.path.join(app_root, 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)
    
from utils.dataframe_utils import get_all_device_configs
from utils.data_preprocessing import query_data_by_batch_time
```

### 5. MinIO SSL连接修复

**文件**：`src/utils/minio_client.py`

```python
import urllib3
from urllib3.exceptions import InsecureRequestWarning

# 修复SSL连接问题
urllib3.disable_warnings(InsecureRequestWarning)

def _create_client(self, http_client: Optional[PoolManager] = None) -> Minio:
    """创建MinIO客户端，包含SSL连接问题修复"""
    # 如果使用HTTPS，创建自定义的HTTP客户端来处理SSL问题
    if secure and not http_client:
        # 创建不验证SSL证书的HTTP客户端（仅用于内网环境）
        http_client = PoolManager(
            timeout=30,
            retries=urllib3.Retry(
                total=5,
                backoff_factor=0.2,
                status_forcelist=[500, 502, 503, 504]
            ),
            cert_reqs='CERT_NONE',
            assert_hostname=False
        )
```

### 6. 数据库模型修复

**文件**：`src/clip/mushroom_image_encoder.py`

```python
# 修复前：使用已删除的growth_stage字段
stage_stats = session.query(
    MushroomImageEmbedding.growth_stage,
    func.count(MushroomImageEmbedding.id).label('count')
).group_by(MushroomImageEmbedding.growth_stage).all()

# 修复后：使用growth_day字段
growth_day_stats = session.query(
    MushroomImageEmbedding.growth_day,
    func.count(MushroomImageEmbedding.id).label('count')
).group_by(MushroomImageEmbedding.growth_day).all()
```

## 部署验证

### 1. 重新构建镜像

```bash
# 使用修复后的构建脚本
./docker/build_codeenigma.sh
```

### 2. 验证容器启动

```bash
# 检查容器日志
docker logs <container_id>

# 验证服务状态
curl http://localhost:5000/health
curl http://localhost:7002
```

### 3. 验证任务执行

```bash
# 检查定时任务日志
docker exec <container_id> tail -f /app/Logs/mushroom_solution-info.log

# 验证数据库连接
docker exec <container_id> python -c "from src.global_const.global_const import pgsql_engine; print('DB OK')"
```

## 预防措施

### 1. 完善构建流程
- 确保所有必要目录都被包含在构建产物中
- 添加构建后的完整性检查

### 2. 统一开发和生产环境
- 使用相同的Python路径配置
- 统一SSL证书处理方式

### 3. 加强测试覆盖
- 添加容器环境的集成测试
- 验证所有模块导入路径

### 4. 监控和告警
- 添加模块导入失败的监控
- 设置SSL连接失败的告警

## 总结

通过以上修复，解决了生产环境部署的关键问题：

1. ✅ **模块导入问题**：修复了Python路径配置和文件结构
2. ✅ **SSL连接问题**：添加了SSL证书验证的绕过机制
3. ✅ **数据库模型问题**：更新了已删除字段的引用
4. ✅ **容器启动问题**：修复了服务启动路径和配置

这些修复确保了系统在生产环境中的稳定运行，同时保持了开发环境的兼容性。
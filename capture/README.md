# Mushroom_CLI（XCloud 截图服务 - 纯 Python 版）

把 `XCloudSDKDemo_CLI` 的“C++截图 + Python上传”方案，迁移为**统一 Python 服务**（本目录即纯 Python 实现）：
- **截图**：Python 通过 `ctypes` 直接调用 `libXCloudSDK.so`（仍依赖厂商 SDK 共享库）
- **上传**：Python 直接用 `boto3` 上传到 MinIO（不再需要 C++ 调用外部 Python 脚本）
- **API**：保持与现有调用方式兼容（`/dynamic_capture`、`/pool_capture`）

## 运行环境
- Linux（Ubuntu 20.04/22.04/24.04）
- Python >= 3.10
- `libXCloudSDK.so`（同现有项目 `lib/x86_64/Release/`）
- Headless 服务器建议用 `xvfb-run`（与现有 systemd 方式一致）

## 快速开始（开发/测试）
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e .

# 与现有项目保持一致：工作目录下放置 SDK 配置与 MinIO 配置
# - XCloudSDKTest_config.ini   (实际为 JSON)
# - minio_config.json
# - lib/x86_64/Release/libXCloudSDK.so

xvfb-run -a --server-args="-screen 0 1920x1080x24" \
  xcloudsdk-py --http 7003 --lib ./lib/x86_64/Release/libXCloudSDK.so
```

## API 使用
```bash
# 本地存储
curl -X GET "http://localhost:7003/dynamic_capture?ip=192.168.1.231&user=admin&storage=local&filename=test/7/20260114"

# 上传到 MinIO
curl -X GET "http://localhost:7003/dynamic_capture?ip=192.168.1.231&user=admin&storage=cloud&filename=test/7/20260114"

# 连接池（推荐：减少重复登录/开流）
curl -X GET "http://localhost:7003/pool_capture?ip=192.168.1.231&user=admin&storage=cloud&filename=hourly_$(date +%Y%m%d_%H)"
```

## 文档
- 架构说明：`docs/ARCHITECTURE.md`
- systemd 示例：`systemd/xcloud-capture-py.service`
- 生产部署：`docs/DEPLOYMENT.md`
- Docker 运行：`docs/DOCKER.md`

## 离线 Linux 服务器部署（无网）
在**有网的 Mac/构建机**打离线包，然后拷贝到**无网 Linux 服务器**启动：
```bash
cd Mushroom_CLI
chmod +x scripts/make_offline_bundle.sh

# Apple Silicon（M 系列）务必指定 linux/amd64（厂商 so 为 x86_64）
PLATFORM=linux/amd64 TAG=xcloudsdk-py:0.1.0 ./scripts/make_offline_bundle.sh
```

把 `dist/xcloudsdk_py_offline_*/` 整个目录拷到无网服务器后：
```bash
cd xcloudsdk_py_offline_YYYYMMDD_HHMMSS
chmod +x scripts/offline_start.sh
./scripts/offline_start.sh

# 健康检查（宿主机端口默认 27003 -> 容器内 7003）
curl -s "http://localhost:27003/healthz" && echo
curl -i "http://localhost:27003/readyz"
```

如果你希望宿主机直接用 `7003` 端口（而不是默认 `27003`），可以：
```bash
HOST_PORT=7003 ./scripts/offline_start.sh
curl -s "http://localhost:7003/healthz" && echo
```

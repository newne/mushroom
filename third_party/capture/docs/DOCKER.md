# Docker 运行（替代 systemd）

目标：把 `Mushroom_CLI` 作为一个容器运行，Java 定时任务仍然只需要调用：
`http://localhost:27003/dynamic_capture?...`

说明：容器内服务端口固定为 `7003`，默认通过 `docker-compose.yml` / `docker-compose.offline.yml` 映射到宿主机 `27003`（可用 `HOST_PORT=7003` 覆盖）。

## 1. 必备文件（宿主机）
在 `Mushroom_CLI/` 目录准备：
- `lib/x86_64/Release/libXCloudSDK.so`（厂商 SDK so；以及它依赖的其他 `.so` 如果有）
- `Device/`（可选但推荐：`Device/eketfo.txt`、`Device/local_eketkey.txt`，SDK 可能会读取）
- `XCloudSDKTest_config.ini`（实际是 JSON 文本）
- `minio_config.json`（可选；`storage=cloud` 需要）

> 建议不要把 `minio_config.json`（包含密钥）烘焙进镜像，用挂载方式提供。

## 2. docker compose（推荐）
```bash
cd Mushroom_CLI
docker compose up -d --build
docker compose logs -f
```

## 2.1 离线服务器（无网）完整流程（推荐）
目标：在“有网的构建机”打一个离线包，把目录整体拷贝到“无网服务器”后即可启动。

### A) 有网构建机：生成离线包
在 `Mushroom_CLI/` 目录执行：
```bash
chmod +x scripts/make_offline_bundle.sh

# 确认本机 Docker 可用（Docker Desktop 需要先启动）
docker info

# Apple Silicon（M1/M2/M3）必须指定 linux/amd64，否则默认构建 linux/arm64 镜像不可用于 x86_64 服务器
# PLATFORM=linux/amd64 TAG=xcloudsdk-py:0.1.0 ./scripts/make_offline_bundle.sh

# 如果构建阶段出现 “Temporary failure resolving ...” 或 “connect: network is unreachable”，一般是 Docker Desktop 的 DNS/IPv6/网络问题：
# 1) Docker Desktop 推荐：BUILDER=desktop-linux（不要用 BUILDER=default，通常会指向 /var/run/docker.sock）
# 2) 再尝试：BUILD_NETWORK=host
# 3) 或在 Docker Desktop -> Settings -> Docker Engine 配置 dns（例如 223.5.5.5 / 119.29.29.29），必要时关闭 IPv6
#
# 如果 Docker Hub 拉基础镜像失败（例如 auth.docker.io TLS handshake timeout / registry-1.docker.io 超时），可以用镜像源：
# BASE_IMAGE=docker.m.daocloud.io/library/python:3.10-slim-bullseye TAG=xcloudsdk-py:0.1.0 ./scripts/make_offline_bundle.sh

# 可选：把真实 minio_config.json（含密钥）也打进离线包
# INCLUDE_MINIO_CONFIG=1 TAG=xcloudsdk-py:0.1.0 OUT_DIR=dist/xcloudsdk_py_offline ./scripts/make_offline_bundle.sh

TAG=xcloudsdk-py:0.1.0 ./scripts/make_offline_bundle.sh
```

如果构建阶段 `apt-get install` 频繁报 `unexpected EOF` / `Failed to fetch`，可尝试：
```bash
# 1) 让构建使用宿主机网络（Docker Desktop 场景更常用）
BUILD_NETWORK=host TAG=xcloudsdk-py:0.1.0 ./scripts/make_offline_bundle.sh

# 2) 切换 APT 镜像源（示例：阿里云）
APT_MIRROR_URL=https://mirrors.aliyun.com TAG=xcloudsdk-py:0.1.0 ./scripts/make_offline_bundle.sh
```

如果你的生产配置文件不在 `Mushroom_CLI/` 根目录，可以用环境变量指定来源路径：
```bash
SDK_CONFIG_SRC=/path/to/XCloudSDKTest_config.ini \
MINIO_CONFIG_SRC=/path/to/minio_config.json \
INCLUDE_MINIO_CONFIG=1 \
TAG=xcloudsdk-py:0.1.0 ./scripts/make_offline_bundle.sh
```

如果厂商运行库/密钥文件也不在本项目目录，可同样指定来源路径：
```bash
SDK_LIB_DIR_SRC=/path/to/lib/x86_64/Release \
DEVICE_DIR_SRC=/path/to/Device \
TAG=xcloudsdk-py:0.1.0 ./scripts/make_offline_bundle.sh
```

生成目录形如：`dist/xcloudsdk_py_offline_YYYYMMDD_HHMMSS/`
把该目录整体拷贝到无网服务器（U盘/scp 到内网跳板等）。

如果你本机 `dist/` 下生成了多个离线包目录，可用下面命令选择最新一个：
```bash
cd "$(ls -dt dist/xcloudsdk_py_offline_* | head -n 1)"
```

### B) 无网服务器：启动
进入离线包目录：
```bash
# 目录名以你实际拷贝为准（不要用通配符，避免匹配到多个目录）
cd /path/to/xcloudsdk_py_offline_YYYYMMDD_HHMMSS
chmod +x scripts/offline_start.sh || true
./scripts/offline_start.sh

# 查看日志
docker compose logs -f   # 或 docker-compose logs -f

# 健康检查
curl -s "http://localhost:27003/healthz"
curl -i "http://localhost:27003/readyz"
```

如果你希望宿主机直接暴露为 `7003` 端口（而不是默认 `27003`），用环境变量覆盖端口映射即可：
```bash
HOST_PORT=7003 ./scripts/offline_start.sh
curl -s "http://localhost:7003/healthz"
```

### C) 调用（与 C++ 完全一致）
```bash
curl -X GET "http://localhost:27003/dynamic_capture?ip=192.168.1.231&user=admin&storage=cloud&filename=test/7/20260114"
```

健康检查：
```bash
curl -s "http://localhost:27003/healthz"
curl -i "http://localhost:27003/readyz"
```

截图测试（与 C++ 完全一致）：
```bash
curl -X GET "http://localhost:27003/dynamic_capture?ip=192.168.1.231&user=admin&storage=cloud&filename=test/7/20260114"
```

## 3. docker run（可选）
```bash
docker build -t xcloudsdk-py:latest .
docker run --rm -p 27003:7003 \
  -v "$PWD/lib/x86_64/Release:/app/lib/x86_64/Release:ro" \
  -v "$PWD/XCloudSDKTest_config.ini:/app/XCloudSDKTest_config.ini:ro" \
  -v "$PWD/minio_config.json:/app/minio_config.json:ro" \
  -v "$PWD/saved_datas:/app/saved_datas" \
  -v "$PWD/log:/app/log" \
  xcloudsdk-py:latest
```

## 4. 常见问题
### 4.1 lib 依赖缺失
如果容器启动时报 “cannot open shared object file”，说明 `libXCloudSDK.so` 依赖的系统库缺失：
- 先在宿主机用 `ldd lib/x86_64/Release/libXCloudSDK.so` 看缺哪些库
- 在 `Dockerfile` 的 `apt-get install` 里补对应包

### 4.2 `/healthz` 连接被重置（connection reset by peer）
现象：执行 `curl -v http://127.0.0.1:27003/healthz` 返回 `Recv failure: Connection reset by peer`。

常见原因：容器内实际没有监听到 `7003`（例如厂商 SDK 初始化卡住导致服务未完成启动）。

排查步骤：
```bash
CID=$(docker compose -f docker-compose.yml ps -q xcloud-capture)
docker compose -f docker-compose.yml ps
docker port "$CID"
docker logs --tail 200 "$CID"

# 容器内确认是否监听 7003
docker exec "$CID" sh -lc 'ss -ltnp || netstat -tlnp || true'
docker exec "$CID" sh -lc 'curl -v http://127.0.0.1:7003/healthz || true'
```

说明：
- `/healthz`：只表示进程存活（应该始终返回 200）
- `/readyz`：表示 SDK 初始化就绪（200=ready；503=初始化中/失败）

### 4.3 Xvfb 相关
本镜像默认会在容器内启动 `Xvfb`（日志写入 `/app/log/xvfb.log`），以提升“无头截图”的成功率。

如果你的环境不需要虚拟显示（或怀疑 Xvfb 引起启动问题），可在 `docker-compose.yml` 里加：
```yaml
environment:
  USE_XVFB: "0"
```

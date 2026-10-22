# Docker 构建系统

## 概述

统一的 Docker 构建系统，支持加密和非加密构建，优化了构建速度和缓存效率。

## 文件说明

- `build.sh` - 统一构建脚本，支持多种配置选项
- `Dockerfile` - 多阶段优化 Dockerfile，支持缓存和快速构建
- `run.sh` - 容器启动脚本，支持多服务模式
- `.dockerignore` - Docker 构建忽略文件
- `.env` - 环境变量配置文件

## 使用方法

### 基本构建（加密）
```bash
./docker/build.sh
```

### 不加密构建
```bash
ENCRYPT=true ./docker/build.sh
```

### 使用 PyArmor 加密
```bash
ENCRYPT=true OBFUSCATION_TOOL=pyarmor ./docker/build.sh
```

### 只构建不推送
```bash
PUSH_IMAGE=false ./docker/build.sh
```

### 禁用缓存
```bash
USE_CACHE=false ./docker/build.sh
```

## 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DOCKER_REGISTRY` | `registry.cn-beijing.aliyuncs.com/ncgnewne` | Docker 镜像仓库地址 |
| `TARGET` | `solution` | 构建目标：`solution`（`docker/Dockerfile`）或 `patrol`（`docker/Dockerfile.patrol`，生产在用） |
| `ENCRYPT` | `false` | 是否启用代码加密（`TARGET=patrol` 时恒不加密） |
| `OBFUSCATION_TOOL` | `codeenigma` | 混淆工具选择 (codeenigma/pyarmor) |
| `BUILD_IMAGE` | `true` | 是否构建镜像 |
| `PUSH_IMAGE` | `true` | 是否推送镜像 |
| `USE_CACHE` | `true` | 是否使用构建缓存 |
| `EXPIRATION_DATE` | - | CodeEnigma 过期日期 (YYYY-MM-DD) |
| `BUMP` | `patch` | 版本基线怎么走：`patch` / `minor` / `major` / `none` |
| `VERSION_OVERRIDE` | - | 直接钉版本号（如 `0.3.0-rc1`），跳过 bump |
| `VERSION_ONLY` | `false` | `true` 时只打印版本号并退出（发版前确认） |

## 构建优化

1. **多阶段构建** - 分离依赖安装和应用代码
2. **缓存优化** - 使用 Docker 层缓存和 UV 缓存
3. **并行构建** - 利用 BuildKit 并行处理
4. **最小化镜像** - 清理不必要的文件和依赖

## 支持的加密工具

### CodeEnigma (推荐)
- 更好的性能和兼容性
- 支持过期日期设置
- 更小的运行时开销

### PyArmor
- 传统的 Python 代码保护工具
- 需要许可证
- 运行时依赖较大

## 故障排除

### 构建失败
1. 检查 Docker 是否正常运行
2. 确认网络连接正常
3. 检查磁盘空间是否充足

### 加密失败
1. 确认加密工具已正确安装
2. 检查许可证是否有效（PyArmor）
3. 查看构建日志中的错误信息

### 推送失败
1. 确认已登录到镜像仓库
2. 检查网络连接
3. 验证仓库权限

## 版本标签

镜像版本格式：`{base_version}-{timestamp}-{git_hash}`

例如：`0.1.0-20260126112355-a515252`

### 版本号从 git 标签推导（小版本自动往前推）

`base_version` **不再**取 pyproject、也不用手改文件，规则是"**最近一个 `vX.Y.Z` 标签 + BUMP**"：

| 最近标签 | `BUMP`（默认 patch） | 本次构建的 `base_version` |
| --- | --- | --- |
| `v0.2.1` | `patch` | `0.2.2` |
| `v0.2.1` | `minor` | `0.3.0` |
| `v0.2.1` | `major` | `1.0.0` |
| `v0.2.1` | `none` | `0.2.1`（重建当前版本） |

所以发版循环是：**构建 → 在构建点打 `v{base_version}` → 推标签**，下一次构建自然就 +1。
脚本收尾会直接把这两条命令打出来；忘了打标签的话，下次会重复同一个版本号。

```bash
# 先看这次会打成什么版本（不构建、不改任何文件）
VERSION_ONLY=true ./docker/build.sh

# 小版本（默认）：v0.2.1 的仓库构建出 0.2.2-<时间>-<提交号>
TARGET=patrol ./docker/build.sh

# 大改：v0.2.1 → 0.3.0
TARGET=patrol BUMP=minor ./docker/build.sh

# 特殊版本（预发/带后缀），直接钉，不再 bump
TARGET=patrol VERSION_OVERRIDE=0.3.0-rc1 ./docker/build.sh
```

### `TARGET=patrol`（生产在用的那个镜像）

```bash
TARGET=patrol ./docker/build.sh              # -f docker/Dockerfile.patrol，构建 + 推送
TARGET=patrol PUSH_IMAGE=false ./docker/build.sh   # 只本地构建（自检用）
```

patrol 与老 solution 的区别，脚本里已经写死：

- **不混淆、不复制 `dist/`**：构建上下文就是仓库根目录（ADR-0012）。
- **不滚 `latest`**：生产只钉 `版本号-时间-提交号`，避免 `latest` 把线上带走样。

## 构建信息

构建完成后会生成 `build_info.json` 文件，包含：
- 项目信息
- 版本号
- Git 哈希
- 构建时间
- 加密状态
- 构建配置

## 快速开始

### 1. 构建镜像

```bash
# 基本构建
./docker/build.sh

# 加密构建
ENCRYPT=true ./docker/build.sh

# 不推送到仓库
PUSH_IMAGE=false ./docker/build.sh
```

### 2. 运行容器

```bash
# 使用 Docker Compose
docker-compose -f docker/mushroom_solution.yml up -d

# 直接运行
docker run -d \
  --name mushroom_solution \
  -p 7002:7002 \
  -p 5000:5000 \
  registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_solution:latest
```

## 端口说明

- `7002` - Streamlit Web 界面
- `5000` - FastAPI 健康检查接口

## 日志查看

```bash
# 查看容器日志
docker logs mushroom_solution

# 实时查看日志
docker logs -f mushroom_solution
```
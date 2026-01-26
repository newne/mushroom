# Redis配置问题修复方案

## 问题描述

生产环境容器启动时出现 `AttributeError: 'Settings' object has no attribute 'REDIS'` 错误。

## 根本原因分析

1. **配置访问方式不一致**：代码中存在错误的大写REDIS访问方式
2. **配置文件映射正常**：Docker Compose配置文件映射正确
3. **环境变量设置正确**：生产环境检测和切换正常

## 修复方案

### 1. 修复配置访问代码

**问题文件**: `fix_encrypted_config.py`
- 移除错误的 `settings.REDIS['host']` 访问方式
- 统一使用 `settings.redis.host` 小写访问方式

### 2. 增强Docker Compose配置

**文件**: `docker/mushroom_solution.yml`
- 添加Redis环境变量作为备用配置：
  ```yaml
  environment:
    REDIS_HOST: 172.17.0.1
    REDIS_PORT: 26379
  ```

### 3. 增强global_const配置加载

**文件**: `src/global_const/global_const.py`
- 添加异常处理机制
- 支持环境变量备用配置
- 增加连接池配置优化

### 4. 添加配置验证工具

**新增文件**:
- `docker/validate_config.py`: 配置验证脚本
- `docker/fix_redis_config.py`: 配置修复脚本

### 5. 更新启动脚本

**文件**: `docker/run.sh`
- 在启动前运行配置验证
- 自动修复配置问题

## 配置文件结构确认

### settings.toml
```toml
[production.redis]
host="172.17.0.1"
port = 26379
```

### .secrets.toml
```toml
[production.redis]
password = "Pl5SpB72sllM8DsT"
```

## 部署验证步骤

1. **构建新镜像**：
   ```bash
   cd docker
   ./build.sh
   ```

2. **启动服务**：
   ```bash
   docker-compose -f mushroom_solution.yml up -d
   ```

3. **检查日志**：
   ```bash
   docker logs mushroom_solution
   ```

4. **验证配置**：
   ```bash
   docker exec mushroom_solution python /app/docker/validate_config.py
   ```

## 预防措施

1. **统一配置访问方式**：始终使用小写属性访问
2. **添加配置验证**：在启动时验证所有必要配置
3. **环境变量备用**：为关键配置提供环境变量备用方案
4. **错误处理**：在配置加载中添加完善的异常处理

## 监控要点

- 容器启动日志中的配置验证结果
- Redis连接池创建状态
- 应用运行时的Redis连接状态

## 回滚方案

如果修复后仍有问题，可以：
1. 回滚到之前的镜像版本
2. 检查Redis服务是否正常运行
3. 验证网络连接配置

## 相关文件

- `docker/mushroom_solution.yml` - Docker Compose配置
- `src/global_const/global_const.py` - 全局配置加载
- `fix_encrypted_config.py` - 配置修复脚本
- `docker/validate_config.py` - 配置验证工具
- `docker/fix_redis_config.py` - Redis配置修复工具
- `docker/run.sh` - 容器启动脚本
# 设定点监控模块重构迁移指南

## 迁移概述

本指南提供了从原版本 `setpoint_change_monitor.py` 迁移到重构版本的详细步骤和注意事项。

## 迁移前准备

### 1. 备份原文件

```bash
# 备份原始文件
cp src/utils/setpoint_change_monitor.py src/utils/setpoint_change_monitor_backup.py

# 备份相关脚本
cp scripts/monitor_setpoint_changes.py scripts/monitor_setpoint_changes_backup.py
cp scripts/batch_setpoint_monitoring.py scripts/batch_setpoint_monitoring_backup.py
```

### 2. 检查依赖关系

```bash
# 搜索项目中对原模块的引用
grep -r "from.*setpoint_change_monitor import" src/
grep -r "import.*setpoint_change_monitor" src/
```

### 3. 数据库备份

```sql
-- 备份现有的设定点变更数据
CREATE TABLE device_setpoint_changes_backup AS 
SELECT * FROM device_setpoint_changes;
```

## 迁移步骤

### 步骤1: 安装新的配置文件

```bash
# 确保配置文件存在
ls -la src/configs/setpoint_monitor_config.json

# 如果不存在，从模板创建
cp src/configs/setpoint_monitor_config.json.template src/configs/setpoint_monitor_config.json
```

### 步骤2: 更新数据库模型

由于主键类型从 `Integer` 改为 `PgUUID`，需要处理数据库结构：

```python
# 运行数据库迁移脚本
python scripts/migrate_setpoint_table.py
```

或手动执行SQL：

```sql
-- 方案1: 如果数据不重要，直接重建表
DROP TABLE IF EXISTS device_setpoint_changes;

-- 方案2: 如果需要保留数据，创建新表并迁移
ALTER TABLE device_setpoint_changes RENAME TO device_setpoint_changes_old;

-- 使用新的表结构创建表
-- (通过运行 create_tables() 函数)

-- 迁移数据 (需要生成UUID)
INSERT INTO device_setpoint_changes (
    id, room_id, device_type, device_name, point_name, point_description,
    change_time, previous_value, current_value, change_type, 
    change_detail, change_magnitude, detection_time, created_at
)
SELECT 
    gen_random_uuid() as id,  -- 生成新的UUID
    room_id, device_type, device_name, point_name, point_description,
    change_time, previous_value, current_value, change_type,
    change_detail, change_magnitude, detection_time, created_at
FROM device_setpoint_changes_old;
```

### 步骤3: 替换模块文件

```bash
# 替换主模块文件
mv src/utils/setpoint_change_monitor_refactored.py src/utils/setpoint_change_monitor.py

# 添加新的配置管理模块
# (setpoint_config.py 已经创建)
```

### 步骤4: 更新导入语句

如果有其他文件导入了原模块，需要更新导入：

```python
# 原导入方式保持不变
from utils.setpoint_change_monitor import (
    DeviceSetpointChangeMonitor,
    batch_monitor_setpoint_changes,
    validate_batch_monitoring_environment
)

# 新增的配置管理器导入
from utils.setpoint_config import get_setpoint_config_manager
```

### 步骤5: 更新调用代码

大部分API保持向后兼容，但有一些可选的改进：

```python
# 原调用方式 (仍然支持)
monitor = DeviceSetpointChangeMonitor()
changes = monitor.monitor_room_setpoint_changes("611", hours_back=1)

# 新调用方式 (推荐)
config_manager = get_setpoint_config_manager()
monitor = DeviceSetpointChangeMonitor(config_manager)
changes = monitor.monitor_room_setpoint_changes("611")  # 使用配置的默认值
```

### 步骤6: 更新调度器配置

如果在调度器中使用了设定点监控，需要更新：

```python
# 在 src/scheduling/optimized_scheduler.py 中
from utils.setpoint_change_monitor import batch_monitor_setpoint_changes
from utils.setpoint_config import get_setpoint_config_manager

def safe_setpoint_monitoring():
    """安全的设定点监控任务"""
    try:
        config_manager = get_setpoint_config_manager()
        
        # 使用配置的默认时间范围
        end_time = datetime.now()
        time_limits = config_manager.get_time_limits()
        hours_back = time_limits.get('default_hours_back', 1)
        start_time = end_time - timedelta(hours=hours_back)
        
        result = batch_monitor_setpoint_changes(
            start_time=start_time,
            end_time=end_time,
            store_results=True,
            config_manager=config_manager
        )
        
        if result['success']:
            logger.info(f"Setpoint monitoring completed: {result['total_changes']} changes detected")
        else:
            logger.error("Setpoint monitoring failed")
            
    except Exception as e:
        logger.error(f"Setpoint monitoring task failed: {e}")
```

## 配置文件说明

### 默认配置结构

```json
{
  "default_rooms": ["607", "608", "611", "612"],
  "time_limits": {
    "max_batch_days": 30,
    "default_hours_back": 1
  },
  "database": {
    "table_name": "device_setpoint_changes",
    "batch_size": 1000,
    "required_fields": [...]
  },
  "thresholds": {
    "air_cooler": {
      "temp_set": 0.5,
      "temp_diffset": 0.2,
      ...
    },
    ...
  },
  "device_types": {
    "air_cooler": {
      "monitored_points": [...]
    },
    ...
  }
}
```

### 自定义配置

可以根据实际需求修改配置文件：

```json
{
  "default_rooms": ["607", "608", "611", "612", "613"],  // 添加新房间
  "thresholds": {
    "air_cooler": {
      "temp_set": 0.3,  // 调整温度阈值
      "temp_diffset": 0.1
    }
  }
}
```

## 测试验证

### 1. 运行测试脚本

```bash
# 运行重构测试脚本
python scripts/test_setpoint_refactor.py
```

### 2. 功能验证

```python
# 验证基本功能
from utils.setpoint_change_monitor import create_setpoint_monitor
from utils.setpoint_config import get_setpoint_config_manager

# 创建监控器
config_manager = get_setpoint_config_manager()
monitor = create_setpoint_monitor(config_manager)

# 测试单个库房监控
changes = monitor.monitor_room_setpoint_changes("611")
print(f"检测到 {len(changes)} 个变更")

# 测试批量监控
from datetime import datetime, timedelta
end_time = datetime.now()
start_time = end_time - timedelta(hours=2)

result = batch_monitor_setpoint_changes(
    start_time=start_time,
    end_time=end_time,
    store_results=False  # 测试时不存储
)
print(f"批量监控结果: {result['success']}")
```

### 3. 数据库验证

```sql
-- 检查表结构
\d device_setpoint_changes

-- 检查数据
SELECT COUNT(*) FROM device_setpoint_changes;
SELECT * FROM device_setpoint_changes LIMIT 5;
```

## 回滚方案

如果迁移过程中出现问题，可以按以下步骤回滚：

### 1. 恢复原文件

```bash
# 恢复原始模块文件
mv src/utils/setpoint_change_monitor.py src/utils/setpoint_change_monitor_refactored.py
mv src/utils/setpoint_change_monitor_backup.py src/utils/setpoint_change_monitor.py
```

### 2. 恢复数据库

```sql
-- 如果备份了数据
DROP TABLE device_setpoint_changes;
ALTER TABLE device_setpoint_changes_backup RENAME TO device_setpoint_changes;
```

### 3. 移除新文件

```bash
# 移除新增的配置文件 (可选)
rm src/utils/setpoint_config.py
rm src/configs/setpoint_monitor_config.json
```

## 常见问题解决

### 问题1: 配置文件加载失败

**症状**: `Failed to load config from xxx: No such file or directory`

**解决方案**:
```bash
# 检查配置文件是否存在
ls -la src/configs/setpoint_monitor_config.json

# 如果不存在，创建默认配置
python -c "
from utils.setpoint_config import SetpointConfigManager
config = SetpointConfigManager()
config.save_config()
"
```

### 问题2: 数据库主键冲突

**症状**: `duplicate key value violates unique constraint`

**解决方案**:
```sql
-- 清空表并重建
TRUNCATE TABLE device_setpoint_changes;

-- 或者删除重复记录
DELETE FROM device_setpoint_changes 
WHERE id IN (
    SELECT id FROM (
        SELECT id, ROW_NUMBER() OVER (
            PARTITION BY room_id, device_name, point_name, change_time 
            ORDER BY created_at
        ) as rn
        FROM device_setpoint_changes
    ) t WHERE t.rn > 1
);
```

### 问题3: 静态配置访问失败

**症状**: `Failed to get rooms from static config`

**解决方案**:
```python
# 检查静态配置是否正确加载
from global_const.global_const import static_settings

try:
    rooms = static_settings.mushroom.rooms
    print(f"Static rooms: {list(rooms.keys())}")
except Exception as e:
    print(f"Static config error: {e}")
    # 系统会自动使用配置文件中的默认房间列表
```

### 问题4: 阈值配置不生效

**症状**: 监控阈值与预期不符

**解决方案**:
```python
# 检查阈值配置
from utils.setpoint_config import get_setpoint_config_manager

config_manager = get_setpoint_config_manager()
threshold = config_manager.get_threshold('air_cooler', 'temp_set')
print(f"Current threshold: {threshold}")

# 如果需要更新
config_manager.update_threshold('air_cooler', 'temp_set', 0.5)
config_manager.save_config()
```

## 性能优化建议

### 1. 配置缓存

```python
# 配置管理器使用单例模式，避免重复加载
config_manager = get_setpoint_config_manager()  # 全局实例
```

### 2. 批量处理

```python
# 使用配置的批量大小
db_config = config_manager.get_database_config()
batch_size = db_config.get('batch_size', 1000)
```

### 3. 监控优化

```python
# 根据实际需求调整时间范围
time_limits = config_manager.get_time_limits()
max_days = time_limits.get('max_batch_days', 30)
```

## 迁移检查清单

- [ ] 备份原始文件和数据库
- [ ] 创建配置文件 `setpoint_monitor_config.json`
- [ ] 添加配置管理模块 `setpoint_config.py`
- [ ] 替换主模块文件
- [ ] 更新数据库表结构
- [ ] 更新导入语句 (如有必要)
- [ ] 更新调用代码 (如有必要)
- [ ] 运行测试脚本验证功能
- [ ] 检查数据库数据完整性
- [ ] 更新相关文档
- [ ] 通知团队成员配置变更

## 后续维护

### 1. 配置管理

- 定期检查配置文件的有效性
- 根据业务需求调整阈值和监控点
- 备份重要的配置变更

### 2. 监控优化

- 根据实际使用情况调整时间范围
- 优化监控点配置，避免无效监控
- 定期清理历史数据

### 3. 性能监控

- 监控批量处理的性能
- 关注数据库存储的增长
- 优化查询和索引

---

通过遵循本迁移指南，可以安全、平滑地从原版本迁移到重构版本，享受配置化、模块化带来的便利和可维护性提升。
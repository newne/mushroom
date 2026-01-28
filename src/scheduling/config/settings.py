"""
调度器配置模块
"""
from typing import Dict, Any

class SchedulerConfig:
    """调度器配置类"""
    
    # 调度器默认配置
    MISFIRE_GRACE_TIME = 300
    MAX_JOB_INSTANCES = 1
    COALESCE = True
    REPLACE_EXISTING = True
    
    # 运行参数
    MAIN_LOOP_INTERVAL = 5
    MAX_FAILURES = 3
    MAX_INIT_RETRIES = 5
    INIT_RETRY_DELAY = 10
    
    @classmethod
    def get_job_defaults(cls) -> Dict[str, Any]:
        """获取任务默认配置"""
        return {
            "misfire_grace_time": cls.MISFIRE_GRACE_TIME,
            "max_instances": cls.MAX_JOB_INSTANCES,
            "coalesce": cls.COALESCE,
            "replace_existing": cls.REPLACE_EXISTING,
        }

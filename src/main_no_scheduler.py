import os

import uvicorn
from loguru import logger

from api.app_factory import create_app


app = create_app(
    startup_messages=[
        "[MAIN] 应用启动，跳过调度器初始化...",
        "[MAIN] 调度器初始化已禁用",
        "[MAIN] 后台定时任务已禁用",
    ]
)


if __name__ == "__main__":
    # 启动 Web 服务 (端口 5001)，不启动调度器
    logger.info("[MAIN] 启动 Web 服务 (端口 5001)...")
    workers = int(os.getenv("UVICORN_WORKERS", "1"))
    reload_enabled = os.getenv("UVICORN_RELOAD", "false").lower() == "true"

    # 当启用 workers/reload 时，Uvicorn 要求 app 使用 import string
    if workers > 1 or reload_enabled:
        uvicorn.run(
            "main_no_scheduler:app",
            host="0.0.0.0",
            port=5001,
            workers=workers,
            reload=reload_enabled,
        )
    else:
        uvicorn.run(app, host="0.0.0.0", port=5001)

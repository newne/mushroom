from scheduling.optimized_scheduler import main
from utils.exception_listener import router
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
import os

from utils.loguru_setting import loguru_setting

# 创建FastAPI应用实例
app = FastAPI(
    title="Load Scheduling Health Check API",
    description="API for monitoring the health status of load scheduling tasks",
    version="1.0.0"
)

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="Load Scheduling Health Check API",
        description="API for monitoring the health status of load scheduling tasks",
        version="1.0.0",
        routes=app.routes,
    )
    app.openapi_schema = openapi_schema
    return app.openapi_schema
app.openapi = custom_openapi

# 注册健康检查路由
app.include_router(router)

if __name__ == '__main__':
    loguru_setting()
    # Start the main scheduling tasks
    main()
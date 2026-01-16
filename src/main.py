from scheduling.optimized_scheduler import main
from utils.exception_listener import router
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
import os
import sys

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
    # 设置日志
    loguru_setting()
    
    # 检查是否在容器环境中
    if os.path.exists('/app') and os.getcwd() == '/app':
        print("[MAIN] 检测到容器环境，启动调度器...")
        # 在容器中运行调度器
        main()
    else:
        print("[MAIN] 检测到开发环境，请使用 uvicorn 启动 FastAPI 或直接运行调度器")
        print("FastAPI: uvicorn main:app --host 0.0.0.0 --port 5000")
        print("调度器: python -c 'from scheduling.optimized_scheduler import main; main()'")
        sys.exit(0)
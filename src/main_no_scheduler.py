import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from loguru import logger

from api.decision_analysis_status import router as decision_analysis_status_router
from api.image_text_quality import router as image_text_quality_router
from api.monitoring_points import router as monitoring_points_router
from utils.exception_listener import router as health_router
from utils.loguru_setting import loguru_setting


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：不启动调度器"""
    # 初始化日志
    loguru_setting()
    logger.info("[MAIN] 应用启动，跳过调度器初始化...")
    logger.info("[MAIN] 调度器初始化已禁用")
    logger.info("[MAIN] 后台定时任务已禁用")
    
    yield
    
    logger.info("[MAIN] 应用关闭")


# 创建FastAPI应用实例
app = FastAPI(
    title="Load Scheduling Health Check API",
    description="API for monitoring the health status of load scheduling tasks",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置
cors_origins = os.getenv("CORS_ORIGINS", "*")
allow_origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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
app.include_router(health_router)

# 注册业务路由
app.include_router(image_text_quality_router)
app.include_router(monitoring_points_router)
app.include_router(decision_analysis_status_router)


if __name__ == "__main__":
    # 启动 Web 服务 (端口 5000)，不启动调度器
    logger.info("[MAIN] 启动 Web 服务 (端口 5000)...")
    workers = int(os.getenv("UVICORN_WORKERS", "1"))
    uvicorn.run(app, host="0.0.0.0", port=5000, workers=workers)

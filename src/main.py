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
from api.mushroom_batch_yield import router as mushroom_batch_yield_router
from scheduling.core.scheduler import OptimizedScheduler
from utils.exception_listener import router as health_router
from utils.loguru_setting import loguru_setting

# 全局调度器实例
scheduler_instance = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：启动和关闭调度器"""
    global scheduler_instance

    # 初始化日志
    loguru_setting()
    logger.info("[MAIN] 应用启动，初始化调度器...")

    try:
        # 创建并初始化调度器
        scheduler_instance = OptimizedScheduler()
        # 初始化（连接数据库、建表、注册任务）
        # 注意：这可能会花费一些时间，如果太长可能会阻塞 startup
        # 但必须在应用就绪前完成，否则健康检查可能会报错
        scheduler_instance.initialize()

        # 启动调度器（后台运行）
        scheduler_instance.start()
        logger.info("[MAIN] 调度器已在后台启动")

        yield

    except Exception as e:
        logger.critical(f"[MAIN] 启动失败: {e}", exc_info=True)
        raise
    finally:
        # 关闭调度器
        if (
            scheduler_instance
            and scheduler_instance.scheduler
            and scheduler_instance.scheduler.running
        ):
            logger.info("[MAIN] 正在关闭调度器...")
            scheduler_instance.scheduler.shutdown()
            logger.info("[MAIN] 调度器已关闭")


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
app.include_router(mushroom_batch_yield_router)
app.include_router(monitoring_points_router)
app.include_router(decision_analysis_status_router)

if __name__ == "__main__":
    # 无论在开发环境还是容器环境，都使用 uvicorn 启动
    # 这将触发 lifespan 事件，从而启动调度器
    logger.info("[MAIN] 启动 Web 服务 (端口 5000)...")
    workers = int(os.getenv("UVICORN_WORKERS", "1"))
    uvicorn.run(app, host="0.0.0.0", port=5000, workers=workers)

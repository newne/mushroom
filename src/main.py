import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from multiprocessing import Process

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from loguru import logger

from api.decision_analysis_status import router as decision_analysis_status_router
from api.image_text_quality import router as image_text_quality_router
from api.monitoring_points import router as monitoring_points_router
from api.mushroom_batch_yield import router as mushroom_batch_yield_router
from utils.exception_listener import router as health_router
from utils.loguru_setting import loguru_setting


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理：仅初始化 Web 应用，不启动调度器"""
    # 初始化日志
    loguru_setting()
    logger.info("[MAIN] 应用启动，调度器由独立进程管理")

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
app.include_router(mushroom_batch_yield_router)
app.include_router(monitoring_points_router)
app.include_router(decision_analysis_status_router)


def run_fastapi() -> None:
    """启动 FastAPI 服务。"""
    logger.info("[MAIN] 启动 Web 服务 (端口 5001)...")
    logger.info("[MAIN] 统一启动模式固定 Uvicorn workers=1")
    uvicorn.run(app, host="0.0.0.0", port=5001)


def run_scheduler_service() -> None:
    """启动调度器服务。"""
    from scheduling.core.scheduler import run_scheduler

    logger.info("[MAIN] 启动调度器服务...")
    run_scheduler()


def run_streamlit_service() -> None:
    """启动 Streamlit 服务。"""
    logger.info("[MAIN] 启动 Streamlit 服务...")
    streamlit_command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "streamlit_app.py",
        "--server.port=7005",
        "--server.address=0.0.0.0",
        "--browser.gatherUsageStats=false",
        "--server.enableCORS=false",
        "--server.enableXsrfProtection=false",
        "--server.maxUploadSize=100",
        "--server.maxMessageSize=200",
    ]
    import subprocess

    subprocess.run(streamlit_command, check=True)


def run_all_services() -> None:
    """统一启动并守护 FastAPI、Scheduler、Streamlit。"""
    loguru_setting()
    logger.info("[MAIN] 统一启动模式：FastAPI + Scheduler + Streamlit")

    processes = {
        "fastapi": Process(target=run_fastapi, name="fastapi-process"),
        "scheduler": Process(target=run_scheduler_service, name="scheduler-process"),
        "streamlit": Process(target=run_streamlit_service, name="streamlit-process"),
    }

    for service_name, process in processes.items():
        process.start()
        logger.info(f"[MAIN] {service_name} 已启动，PID={process.pid}")

    def _shutdown_handler(signum, _frame):
        signal_name = signal.Signals(signum).name
        logger.info(f"[MAIN] 收到退出信号: {signal_name}，开始停止全部服务")
        for service_name, process in processes.items():
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
                logger.info(f"[MAIN] {service_name} 已停止")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    while True:
        for service_name, process in processes.items():
            if not process.is_alive():
                exit_code = process.exitcode
                logger.error(
                    f"[MAIN] {service_name} 进程异常退出，exit_code={exit_code}"
                )
                for other_name, other_process in processes.items():
                    if other_name != service_name and other_process.is_alive():
                        other_process.terminate()
                        other_process.join(timeout=10)
                        logger.info(f"[MAIN] {other_name} 已停止")
                raise SystemExit(1)
        time.sleep(5)


if __name__ == "__main__":
    run_all_services()

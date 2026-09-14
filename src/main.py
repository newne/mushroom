import signal
import sys
import time
from multiprocessing import Process

import uvicorn
from loguru import logger

from api.app_factory import create_app
from utils.loguru_setting import loguru_setting


app = create_app(startup_messages=["[MAIN] 应用启动，调度器由独立进程管理"])


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

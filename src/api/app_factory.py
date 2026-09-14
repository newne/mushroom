import os
from collections.abc import Sequence
from contextlib import asynccontextmanager

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


APP_TITLE = "Load Scheduling Health Check API"
APP_DESCRIPTION = "API for monitoring the health status of load scheduling tasks"
APP_VERSION = "1.0.0"


def _build_lifespan(startup_messages: Sequence[str]):
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        loguru_setting()
        for message in startup_messages:
            logger.info(message)
        yield
        logger.info("[MAIN] 应用关闭")

    return lifespan


def _configure_openapi(app: FastAPI) -> None:
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=APP_TITLE,
            description=APP_DESCRIPTION,
            version=APP_VERSION,
            routes=app.routes,
        )
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi


def _configure_cors(app: FastAPI) -> None:
    cors_origins = os.getenv("CORS_ORIGINS", "*")
    allow_origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _register_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(image_text_quality_router)
    app.include_router(mushroom_batch_yield_router)
    app.include_router(monitoring_points_router)
    app.include_router(decision_analysis_status_router)


def create_app(*, startup_messages: Sequence[str]) -> FastAPI:
    app = FastAPI(
        title=APP_TITLE,
        description=APP_DESCRIPTION,
        version=APP_VERSION,
        lifespan=_build_lifespan(startup_messages),
    )
    _configure_cors(app)
    _configure_openapi(app)
    _register_routers(app)
    return app
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from .capture import CaptureService
from .config import load_minio_config, load_sdk_init_json
from .minio_uploader import MinIOUploader
from .sdk import XCloudSDK, XCloudSDKError

logger = logging.getLogger("xcloudsdk_py.server")


@dataclass(frozen=True)
class AppConfig:
    lib_path: str
    sdk_config_path: str = "XCloudSDKTest_config.ini"
    minio_config_path: str = "minio_config.json"
    picture_dir: str = "saved_datas/picture"
    enable_pool: bool = True


def create_app(config: AppConfig) -> FastAPI:
    app = FastAPI(title="Mushroom_CLI", version="0.1.0")

    class _Runtime:
        def __init__(self, cfg: AppConfig) -> None:
            self.cfg = cfg
            self._lock = threading.Lock()
            self._init_started = False
            self._init_done = threading.Event()
            self._init_error: Optional[str] = None
            self._init_thread: Optional[threading.Thread] = None
            self.sdk: Optional[XCloudSDK] = None
            self.capture: Optional[CaptureService] = None

        def start_init_async(self) -> None:
            with self._lock:
                if self._init_started:
                    return
                self._init_started = True
                self._init_thread = threading.Thread(target=self._init_worker, name="xcloudsdk-init", daemon=True)
                self._init_thread.start()

        def _init_worker(self) -> None:
            sdk: Optional[XCloudSDK] = None
            try:
                logger.info("SDK init starting...")
                init_json = load_sdk_init_json(self.cfg.sdk_config_path)
                sdk = XCloudSDK(lib_path=self.cfg.lib_path, init_json=init_json)

                minio: Optional[MinIOUploader] = None
                try:
                    if Path(self.cfg.minio_config_path).exists():
                        minio_cfg = load_minio_config(self.cfg.minio_config_path)
                        minio = MinIOUploader(minio_cfg)
                except Exception:
                    minio = None

                capture = CaptureService(
                    sdk=sdk, picture_dir=self.cfg.picture_dir, minio=minio, enable_pool=self.cfg.enable_pool
                )
                with self._lock:
                    self.sdk = sdk
                    self.capture = capture
                    self._init_error = None
                logger.info("SDK init done")
            except Exception as e:
                msg = f"{type(e).__name__}: {e}"
                logger.exception("SDK init failed: %s", msg)
                with self._lock:
                    self._init_error = msg
                    self.capture = None
                    self.sdk = None
                if sdk is not None:
                    try:
                        sdk.close()
                    except Exception:
                        pass
            finally:
                self._init_done.set()

        def snapshot(self) -> dict[str, object]:
            with self._lock:
                ready = self.capture is not None and self._init_error is None and self._init_done.is_set()
                return {
                    "init_started": self._init_started,
                    "init_done": self._init_done.is_set(),
                    "ready": ready,
                    "init_error": self._init_error,
                }

        def get_capture_or_error(self) -> tuple[Optional[CaptureService], Optional[JSONResponse]]:
            self.start_init_async()

            snap = self.snapshot()
            if not bool(snap.get("init_done")):
                return None, JSONResponse(
                    {"success": False, "message": "service initializing", "detail": snap}, status_code=503
                )
            if snap.get("init_error"):
                return None, JSONResponse({"success": False, "message": "service init failed", "detail": snap}, status_code=503)

            capture = self.capture
            if not isinstance(capture, CaptureService):
                return None, JSONResponse({"success": False, "message": "service not ready", "detail": snap}, status_code=503)
            return capture, None

        def close(self) -> None:
            capture = self.capture
            if isinstance(capture, CaptureService) and capture.pool:
                try:
                    capture.pool.clear()
                except Exception:
                    pass

            sdk = self.sdk
            if isinstance(sdk, XCloudSDK):
                try:
                    sdk.close()
                except Exception:
                    pass

    runtime = _Runtime(config)

    @app.on_event("startup")
    def _startup() -> None:
        # 重要：不要在 startup 阶段同步初始化厂商 SDK。
        # 某些环境下 XCloudSDK_Init/回调注册可能卡住，导致 HTTP 端口无法监听，从而出现“connection reset by peer”。
        runtime.start_init_async()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        runtime.close()

    @app.get("/")
    def index() -> dict[str, object]:
        return {
            "service": "Mushroom_CLI",
            "endpoints": ["/dynamic_capture", "/fast_capture", "/pool_capture", "/capture"],
        }

    @app.get("/healthz")
    def healthz() -> dict[str, object]:
        return {"ok": True, **runtime.snapshot()}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        snap = runtime.snapshot()
        if bool(snap.get("ready")):
            return JSONResponse({"ok": True, **snap}, status_code=200)
        return JSONResponse({"ok": False, **snap}, status_code=503)

    @app.get("/dynamic_capture")
    def dynamic_capture(
        ip: str = Query(""),
        user: str = Query("admin"),
        pwd: str = Query(""),
        storage: str = Query("local"),
        filename: str = Query(""),
        channel: int = Query(0),
    ):
        if not (ip or "").strip():
            return JSONResponse({"success": False, "message": "Missing required parameter: ip"}, status_code=400)
        capture, err = runtime.get_capture_or_error()
        if err is not None:
            return err
        result = capture.dynamic_capture(ip=ip, user=user, pwd=pwd, storage=storage, filename=filename, channel=channel)
        code = 200 if result.get("success") else 500
        return JSONResponse(result, status_code=code)

    @app.get("/fast_capture")
    def fast_capture(
        ip: str = Query(""),
        user: str = Query("admin"),
        pwd: str = Query(""),
        storage: str = Query("local"),
        filename: str = Query(""),
        channel: int = Query(0),
    ):
        if not (ip or "").strip():
            return JSONResponse({"success": False, "message": "Missing required parameter: ip"}, status_code=400)
        capture, err = runtime.get_capture_or_error()
        if err is not None:
            return err
        result = capture.dynamic_capture(ip=ip, user=user, pwd=pwd, storage=storage, filename=filename, channel=channel)
        code = 200 if result.get("success") else 500
        return JSONResponse(result, status_code=code)

    @app.get("/capture")
    def capture_compat(
        ip: str = Query(""),
        user: str = Query("admin"),
        pwd: str = Query(""),
        storage: str = Query("local"),
        filename: str = Query(""),
        channel: int = Query(0),
    ):
        if not (ip or "").strip():
            return JSONResponse({"success": False, "message": "Missing required parameter: ip"}, status_code=400)
        # 兼容旧接口：统一走 dynamic_capture 的单次登录/开流/抓图流程
        capture, err = runtime.get_capture_or_error()
        if err is not None:
            return err
        result = capture.dynamic_capture(ip=ip, user=user, pwd=pwd, storage=storage, filename=filename, channel=channel)
        code = 200 if result.get("success") else 500
        return JSONResponse(result, status_code=code)

    @app.get("/pool_capture")
    def pool_capture(
        ip: str = Query(""),
        user: str = Query("admin"),
        pwd: str = Query(""),
        storage: str = Query("local"),
        filename: str = Query(""),
        channel: int = Query(0),
    ):
        if not (ip or "").strip():
            return JSONResponse({"success": False, "message": "Missing required parameter: ip"}, status_code=400)
        capture, err = runtime.get_capture_or_error()
        if err is not None:
            return err
        if ip in ("test", "stats"):
            pool = capture.pool
            return JSONResponse(
                {"success": True, "message": "连接池状态", "pool_stats": pool.stats() if pool else None}, status_code=200
            )

        result = capture.pool_capture(ip=ip, user=user, pwd=pwd, storage=storage, filename=filename, channel=channel)
        code = 200 if result.get("success") else 500
        return JSONResponse(result, status_code=code)

    return app

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from .minio_uploader import MinIOUploader
from .pool import DeviceConnectionPool
from .sdk import XCloudSDK


logger = logging.getLogger("xcloudsdk_py.capture")


def _login_error_detail(code: int) -> str:
    match int(code):
        case -1:
            return "用户名或密码错误"
        case -2:
            return "设备不在线"
        case -3:
            return "连接超时"
        case _:
            return "未知错误"


def _snap_error_detail(code: int, *, file_ready: bool) -> str:
    if int(code) >= 0 and not file_ready:
        return "截图接口返回成功但文件未生成/未稳定，判定截图失败"
    match int(code):
        case -1239510:
            return "无头模式截图失败，可能需要虚拟显示或不同的截图方法"
        case -1:
            return "一般性截图失败"
        case -2:
            return "播放句柄无效"
        case -3:
            return "文件路径无效"
        case _:
            return f"未知错误码: {code}"


def _sanitize_filename(filename: str) -> str:
    name = (filename or "").strip()
    if not name:
        return ""
    name = name.lstrip("/").lstrip("\\")
    if name.startswith("..") or "/.." in name or "\\.." in name:
        raise ValueError("invalid filename: path traversal")
    if ":" in name:
        raise ValueError("invalid filename: ':' not allowed")
    return name


def _ensure_jpg_suffix(filename: str) -> str:
    if filename.lower().endswith(".jpg"):
        return filename
    return f"{filename}.jpg"


def _wait_for_file_ready(path: Path, *, timeout_s: float = 5.0, interval_s: float = 0.1) -> bool:
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_size = -1
    stable = 0
    while time.monotonic() < deadline:
        try:
            stat = path.stat()
            if stat.st_size <= 0:
                stable = 0
            else:
                if stat.st_size == last_size:
                    stable += 1
                else:
                    stable = 0
                last_size = stat.st_size
                if stable >= 1:
                    return True
        except FileNotFoundError:
            stable = 0
        time.sleep(interval_s)
    return False


class CaptureService:
    def __init__(
        self,
        *,
        sdk: XCloudSDK,
        picture_dir: str | Path,
        minio: Optional[MinIOUploader] = None,
        enable_pool: bool = True,
    ) -> None:
        self._sdk = sdk
        self._picture_dir = Path(picture_dir)
        self._minio = minio
        self._op_lock = None  # lazy init to keep module import light

        self._pool: Optional[DeviceConnectionPool] = None
        if enable_pool:
            self._pool = DeviceConnectionPool(sdk=sdk)

    @property
    def pool(self) -> Optional[DeviceConnectionPool]:
        return self._pool

    def _lock(self):
        import threading

        if self._op_lock is None:
            self._op_lock = threading.Lock()
        return self._op_lock

    def dynamic_capture(
        self,
        *,
        ip: str,
        user: str = "admin",
        pwd: str = "",
        storage: str = "local",
        filename: str = "",
        channel: int = 0,
    ) -> dict[str, Any]:
        with self._lock():
            try:
                logger.info("dynamic_capture: ip=%s user=%s storage=%s filename=%s", ip, user, storage, filename)
                result = self._dynamic_capture_locked(
                    ip=ip, user=user, pwd=pwd, storage=storage, filename=filename, channel=channel
                )
                logger.info("dynamic_capture done: ip=%s success=%s", ip, bool(result.get("success")))
                return result
            except Exception as e:
                logger.exception("dynamic_capture exception: ip=%s", ip)
                return {"success": False, "message": f"Exception occurred while processing request: {e}"}

    def pool_capture(
        self,
        *,
        ip: str,
        user: str = "admin",
        pwd: str = "",
        storage: str = "local",
        filename: str = "",
        channel: int = 0,
    ) -> dict[str, Any]:
        if not self._pool:
            return {"success": False, "message": "connection pool disabled"}
        with self._lock():
            try:
                logger.info("pool_capture: ip=%s user=%s storage=%s filename=%s channel=%s", ip, user, storage, filename, channel)
                result = self._pool_capture_locked(ip=ip, user=user, pwd=pwd, storage=storage, filename=filename, channel=channel)
                logger.info("pool_capture done: ip=%s success=%s", ip, bool(result.get("success")))
                return result
            except Exception as e:
                logger.exception("pool_capture exception: ip=%s", ip)
                return {"success": False, "message": f"Exception occurred while processing request: {e}"}

    def _build_paths(self, *, ip: str, filename: str) -> tuple[str, Path]:
        safe_name = _sanitize_filename(filename)
        if not safe_name:
            safe_name = f"{ip}_{int(time.time())}"
        safe_name = _ensure_jpg_suffix(safe_name)

        file_path = (self._picture_dir / safe_name).resolve()
        base = self._picture_dir.resolve()
        if base not in file_path.parents and file_path != base:
            raise ValueError("invalid filename: outside picture_dir")

        file_path.parent.mkdir(parents=True, exist_ok=True)
        return safe_name, file_path

    def _dynamic_capture_locked(
        self,
        *,
        ip: str,
        user: str,
        pwd: str,
        storage: str,
        filename: str,
        channel: int,
    ) -> dict[str, Any]:
        storage = (storage or "local").lower()
        device_id = ip

        object_name, file_path = self._build_paths(ip=ip, filename=filename)
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass

        login_seq = self._sdk.next_seq()
        self._sdk.set_device_credentials(device_id, user, pwd)
        login_timeout_s = 30.0
        login_handle, login_rc = self._sdk.dev_login(device_id, seq=login_seq, timeout_s=login_timeout_s)
        if login_handle <= 0:
            return {
                "success": False,
                "message": "Failed to initiate device login",
                "login_handle": login_handle,
            }
        if login_rc == -99991:
            self._sdk.dev_logout(device_id)
            return {
                "success": False,
                "message": f"Device login timeout ({int(login_timeout_s)} seconds)",
                "detail": "设备在30秒内未响应登录请求",
            }
        if login_rc < 0:
            self._sdk.dev_logout(device_id)
            return {
                "success": False,
                "message": "Device login failed",
                "error_code": login_rc,
                "detail": _login_error_detail(login_rc),
            }

        play_seq = self._sdk.next_seq()
        hwnd = self._sdk.get_play_window_handle()
        display_env = os.environ.get("DISPLAY") or ""
        play_handle, play_rc = self._sdk.media_realplay(
            device_id, channel=channel, stream_type=0, hwnd=hwnd, seq=play_seq, timeout_s=8.0
        )
        if play_handle <= 0:
            self._sdk.dev_logout(device_id)
            return {"success": False, "message": "Failed to start real play", "play_handle": play_handle}
        if play_rc < 0:
            try:
                self._sdk.stop_media_play(play_handle)
            finally:
                self._sdk.dev_logout(device_id)
            return {
                "success": False,
                "message": "Real play start callback failed or timeout",
                "play_handle": play_handle,
                "real_play_seq": play_seq,
                "real_play_callback_result": play_rc,
            }

        keyframe_rc = self._sdk.make_keyframe(device_id, channel=channel, stream_type=0)
        play_data_ready, play_data_msg_id, play_data_p1, play_data_p2 = self._sdk.wait_play_data(play_handle, timeout_s=5.0)

        snap_method = "MediaSnapImageEx"
        snap_rc = self._sdk.snap_image_ex(play_handle, str(file_path), channel_index=0, param="")
        if snap_rc < 0:
            snap_method = "MediaSnapImage"
            snap_rc = self._sdk.snap_image(play_handle, str(file_path))

        file_ready = False
        if snap_rc >= 0:
            file_ready = _wait_for_file_ready(file_path, timeout_s=5.0, interval_s=0.1)

        self._sdk.stop_media_play(play_handle)
        self._sdk.dev_logout(device_id)

        if snap_rc < 0 or not file_ready:
            return {
                "success": False,
                "message": "Failed to capture screenshot",
                "error_code": snap_rc,
                "error_detail": _snap_error_detail(snap_rc, file_ready=file_ready),
                "play_handle": play_handle,
                "headless_mode": not bool(os.environ.get("DISPLAY")),
                "display": display_env,
                "x11_window_handle": hwnd,
                "snap_method": snap_method,
                "real_play_seq": play_seq,
                "real_play_callback_result": play_rc,
                "force_keyframe_result": keyframe_rc,
                "play_data_ready": play_data_ready,
                "play_data_msg_id": play_data_msg_id,
                "play_data_param1": play_data_p1,
                "play_data_param2": play_data_p2,
            }

        response: dict[str, Any] = {
            "success": True,
            "message": "Screenshot captured successfully",
            "device_ip": ip,
            "filename": object_name,
            "file_path": str(file_path),
            "file_exists": True,
            "display": display_env,
            "x11_window_handle": hwnd,
            "snap_result": snap_rc,
            "snap_method": snap_method,
            "timestamp": int(time.time()),
            "force_keyframe_result": keyframe_rc,
            "play_data_ready": play_data_ready,
            "play_data_msg_id": play_data_msg_id,
            "play_data_param1": play_data_p1,
            "play_data_param2": play_data_p2,
            "real_play_seq": play_seq,
            "real_play_callback_result": play_rc,
        }

        if storage == "cloud":
            if not self._minio:
                response["cloud_uploaded"] = False
                response["cloud_error"] = "MinIO is not configured"
            else:
                try:
                    result = self._minio.upload_file(file_path=str(file_path), object_name=object_name)
                    response["cloud_uploaded"] = True
                    response["cloud_url"] = f"{self._minio.endpoint.rstrip('/')}/{result.bucket}/{result.object_name}"
                except Exception:
                    response["cloud_uploaded"] = False
                    response["cloud_error"] = "Failed to upload to MinIO"

            # 与 C++ 版本保持一致：cloud 模式上传后删除本地文件（不论上传是否成功）
            if file_path.exists():
                try:
                    file_path.unlink(missing_ok=True)
                    response["local_file_deleted"] = True
                except Exception as e:
                    response["local_file_deleted"] = False
                    response["delete_error"] = str(e)

        return response

    def _pool_capture_locked(
        self,
        *,
        ip: str,
        user: str,
        pwd: str,
        storage: str,
        filename: str,
        channel: int,
    ) -> dict[str, Any]:
        storage = (storage or "local").lower()
        assert self._pool is not None
        t_conn_start = time.monotonic()

        object_name, file_path = self._build_paths(ip=ip, filename=filename)
        try:
            file_path.unlink(missing_ok=True)
        except Exception:
            pass

        try:
            conn = self._pool.get_or_create(device_id=ip, user=user, pwd=pwd, channel=channel)
            connection_time_ms = int((time.monotonic() - t_conn_start) * 1000)
        except Exception as e:
            connection_time_ms = int((time.monotonic() - t_conn_start) * 1000)
            return {
                "success": False,
                "message": "Failed to get connection from pool",
                "device_ip": ip,
                "connection_time_ms": connection_time_ms,
                "error": str(e),
            }

        t_capture_start = time.monotonic()
        self._sdk.make_keyframe(ip, channel=channel, stream_type=0)
        self._sdk.wait_play_data(conn.play_handle, timeout_s=5.0)

        snap_method = "MediaSnapImageEx"
        snap_rc = self._sdk.snap_image_ex(conn.play_handle, str(file_path), channel_index=0, param="")
        if snap_rc < 0:
            snap_method = "MediaSnapImage"
            snap_rc = self._sdk.snap_image(conn.play_handle, str(file_path))

        file_ready = False
        if snap_rc >= 0:
            file_ready = _wait_for_file_ready(file_path, timeout_s=5.0, interval_s=0.1)

        capture_time_ms = int((time.monotonic() - t_capture_start) * 1000)
        self._pool.release(conn)

        if snap_rc < 0 or not file_ready:
            # 句柄可能已失效：移除连接，下次重建
            self._pool.remove(conn)
            return {
                "success": False,
                "message": "Failed to capture screenshot (connection pool)",
                "device_ip": ip,
                "error_code": snap_rc,
                "file_path": str(file_path),
                "file_exists": file_path.exists(),
                "connection_time_ms": connection_time_ms,
                "capture_time_ms": capture_time_ms,
                "total_time_ms": connection_time_ms + capture_time_ms,
            }

        response: dict[str, Any] = {
            "success": True,
            "message": "Screenshot captured successfully (connection pool optimized)",
            "device_ip": ip,
            "filename": object_name,
            "file_path": str(file_path),
            "file_exists": True,
            "snap_result": snap_rc,
            "play_handle": conn.play_handle,
            "login_handle": conn.login_handle,
            "timestamp": int(time.time()),
            "optimization": "connection_pool_enabled",
            "connection_time_ms": connection_time_ms,
            "capture_time_ms": capture_time_ms,
            "total_time_ms": connection_time_ms + capture_time_ms,
        }

        if storage == "cloud":
            if not self._minio:
                response["cloud_uploaded"] = False
                response["cloud_error"] = "MinIO is not configured"
            else:
                try:
                    result = self._minio.upload_file(file_path=str(file_path), object_name=object_name)
                    response["cloud_uploaded"] = True
                    response["cloud_url"] = f"{self._minio.endpoint.rstrip('/')}/{result.bucket}/{result.object_name}"
                    try:
                        file_path.unlink(missing_ok=True)
                        response["local_file_deleted"] = True
                    except Exception as e:
                        response["local_file_deleted"] = False
                        response["delete_error"] = str(e)
                except Exception:
                    response["cloud_uploaded"] = False
                    response["cloud_error"] = "Failed to upload to MinIO"

        return response

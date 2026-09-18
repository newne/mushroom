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


#: limited-range（studio swing）的两个端点：SDK 把满量程压到这里。
LEVELS_LOW = 16
LEVELS_HIGH = 235
#: "已经是满量程"的判据。**留了余量**：JPEG 在强边缘有振铃，被压过的图实测
#: `min/max = 11/239`（并不是干净的 16/235），所以门槛必须低于 16/高于 235，
#: 否则一张已经正确的图会被再拉一遍。
FULL_RANGE_LO = 4
FULL_RANGE_HI = 251


def _levels_enabled() -> bool:
    """电平修正默认开着；现场可用 `CAPTURE_NORMALIZE_LEVELS=0` 一键关掉。

    留这个开关，是为了"补丁本身出问题"时能**不改文件、不重建镜像**就退回去。
    """
    return os.environ.get("CAPTURE_NORMALIZE_LEVELS", "1").strip().lower() \
        not in ("0", "false", "no", "off")


def _normalize_levels(file_path: Path) -> dict[str, Any]:
    """把 SDK 压进 16..235 的电平拉回满量程；返回可直接并进响应的诊断字段。

    ## 为什么要做这件事

    SDK 的成像管线把**满量程**输入（相机 RTSP 是 `yuvj420p` + `color_range=pc`）
    当 limited-range 输出，写出的 JPEG 服从 `out = 16 + in*219/255`。而 JPEG 没有
    "我这是 limited range"的元数据——任何解码方（浏览器、PIL、ffmpeg）都按满量程解释，
    于是黑停在 16、白停在 235：整幅发灰、对比度塌掉。现场对它的描述是"图像太糊、
    和相机软件不一样"，但**细节其实没丢**，只是电平契约错了。2026-09-18 现场实测：

    | 来源 | 灰度 min/max | 近白(>240) | 拉开电平后梯度能量 |
    | --- | --- | --- | --- |
    | XCloudSDK 抓拍（修正前） | 11 / 239 | 0.00% | 11.117 → 12.666 |
    | 同相机 RTSP 直出帧 | 0 / 255 | 0.32% | 13.894 → 15.386 |

    抓拍的 max 永远到不了 240 —— 这就是"它被压过"最直接的证据。

    ## 为什么修在采图侧，而不是显示侧

    MinIO 里存的就是这些字节。在显示侧修，等于要求**每一处分发点**都记得再修一遍
    （历史回看、离线分析、以后新增的消费者），而且历史图永远是错的。在这里修一次，
    入库即正确。

    ## 失败必须放行（fail-open）

    没装 Pillow、或读写失败时**原样返回**，只记一条 warning 并在响应里带
    `levels_normalized=false`。这台服务掉了就是一张图都没有，比"图发灰"严重得多；
    而且 Pillow 对厂商镜像是**可选依赖**（镜像只装了 fastapi/uvicorn/boto3），
    现场靠挂载注入，所以"没有它"是预期内的情况而不是异常。

    ## 落盘用临时文件 + 原子替换

    直接 `save(file_path)` 一旦中途失败（磁盘满、被杀），留下一张**截断的 JPEG**，
    而且它已经覆盖了唯一一份有效数据。所以写同目录的临时文件、成功后再 `os.replace`。
    """
    if not _levels_enabled():
        return {"levels_normalized": False, "levels_reason": "disabled_by_env"}
    try:
        from PIL import Image
    except Exception as e:  # noqa: BLE001 - 没装 Pillow 是**预期内**的情况
        logger.warning("levels: Pillow 不可用，按原图放行（原因：%s）", e)
        return {"levels_normalized": False, "levels_reason": "pillow_unavailable"}

    tmp_path = file_path.with_name(file_path.name + ".levels-tmp")
    try:
        with Image.open(file_path) as im:
            im.load()
            lo, hi = im.convert("L").getextrema()
            if lo <= FULL_RANGE_LO or hi >= FULL_RANGE_HI:
                logger.info("levels: 已是满量程（min=%d max=%d），不动", lo, hi)
                return {"levels_normalized": False, "levels_reason": "already_full_range",
                        "levels_before": [lo, hi]}
            span = LEVELS_HIGH - LEVELS_LOW
            lut = [min(255, max(0, round((v - LEVELS_LOW) * 255 / span))) for v in range(256)]
            fixed = im.point(lut * len(im.getbands()))

        # 重编码质量取 95，**故意不用 `quality="keep"`**：
        #   1) `im.point()` 派生出来的新图不带 `format`/`quantization`，而 Pillow 的
        #      'keep' 判据正是 `im.format != "JPEG"`（JpegImagePlugin.py:707/753），
        #      必然抛错；要让它成立得把 provenance 手工搬过去，等于多一条只在特定
        #      Pillow 版本上成立的代码路径。
        #   2) 就算搬得动，'keep' 沿用**原图那套较粗的量化表**，是在原有损失上再加一代
        #      同样粗的损失；q=95 用更细的表，多出来这一代的损失反而更小。
        # 代价是文件大 19%（实测 1050 KB → 1246 KB），换来的是更少的新增损失。
        fixed.save(tmp_path, format="JPEG", quality=95)

        with Image.open(tmp_path) as chk:
            chk.load()
            after_lo, after_hi = chk.convert("L").getextrema()
        os.replace(tmp_path, file_path)     # 原子：不会留下半张图
        logger.info("levels: 电平修正 min/max %d/%d -> %d/%d（%d 字节）",
                    lo, hi, after_lo, after_hi, file_path.stat().st_size)
        return {"levels_normalized": True, "levels_reason": "remapped",
                "levels_before": [lo, hi], "levels_after": [after_lo, after_hi]}
    except Exception as e:  # noqa: BLE001 - 任何失败都不能让抓图变成失败
        logger.warning("levels: 电平修正失败，按原图放行（原因：%s）", e)
        return {"levels_normalized": False, "levels_reason": f"error: {e}"}
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 清不掉临时文件不该影响结果
            pass


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

        levels = _normalize_levels(file_path)

        response: dict[str, Any] = {
            "success": True,
            "message": "Screenshot captured successfully",
            "device_ip": ip,
            "filename": object_name,
            "file_path": str(file_path),
            "file_exists": True,
            "display": display_env,
            **levels,
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

        levels = _normalize_levels(file_path)

        response: dict[str, Any] = {
            "success": True,
            "message": "Screenshot captured successfully (connection pool optimized)",
            "device_ip": ip,
            "filename": object_name,
            "file_path": str(file_path),
            "file_exists": True,
            **levels,
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

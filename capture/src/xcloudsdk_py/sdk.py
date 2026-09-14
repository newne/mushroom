from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .x11_window import X11HiddenWindow


ESXSDK_DEV_LOGIN = 12001
ESXSDK_MEDIA_START_REAL_PLAY = 12002

EUIMSG_END_BUFFER_DATA = 30004
EUIMSG_PLAY_INFO = 30006
EUIMSG_PLAY_SAVE_IMAGE_FILE = 30007
EUIMSG_YUV_DATA = 30010

EXSDK_DATA_FORMATE_FRAME = 103


class XCloudSDKError(RuntimeError):
    pass


@dataclass
class _Waiter:
    event: threading.Event
    msg_id: int = 0
    param1: int = 0
    param2: int = 0


_MessageCallback = ctypes.CFUNCTYPE(
    ctypes.c_int,  # return
    ctypes.c_int,  # hObject (XSDK_HANDLE)
    ctypes.c_int,  # nMsgId
    ctypes.c_int,  # nParam1
    ctypes.c_int,  # nParam2
    ctypes.c_int,  # nParam3
    ctypes.c_char_p,  # szString
    ctypes.c_void_p,  # pObject
    ctypes.c_longlong,  # lParam (int64)
    ctypes.c_int,  # nSeq
    ctypes.c_void_p,  # pUserData
    ctypes.c_void_p,  # pMsg
)


class XCloudSDK:
    """
    轻量 ctypes 封装：
    - 只覆盖当前业务路径需要的函数
    - 通过 nSeq 关联异步回调（login / realplay）
    - 通过 hObject(playHandle) 关联“播放数据已到达”回调
    """

    def __init__(
        self,
        *,
        lib_path: str,
        init_json: str,
        log_type: int = 2,
        log_level: int = 1,
    ) -> None:
        self._lock = threading.RLock()
        self._seq = 1
        self._x11_window: Optional[X11HiddenWindow] = None
        self._x11_tried = False

        try:
            self._lib = ctypes.CDLL(lib_path, mode=ctypes.RTLD_GLOBAL)
        except OSError as e:
            raise XCloudSDKError(f"failed to load libXCloudSDK.so: {lib_path}: {e}") from e

        self._bind()

        init_str = init_json.strip() if init_json and init_json.strip() else "{}"
        json.loads(init_str)
        rc = self._lib.XCloudSDK_Init(init_str.encode("utf-8"))
        if rc < 0:
            raise XCloudSDKError(f"XCloudSDK_Init failed: {rc}")

        self._lib.XCloudSDK_SetLogTypeAndLevel(int(log_type), int(log_level))

        self._pending_login: dict[int, _Waiter] = {}
        self._pending_realplay: dict[int, _Waiter] = {}
        self._playdata_wait: dict[int, _Waiter] = {}

        self._callback = _MessageCallback(self._on_message)
        self.h_user = int(self._lib.XCloudSDK_RegisterCallback(self._callback, None))
        if self.h_user < 0:
            raise XCloudSDKError(f"XCloudSDK_RegisterCallback failed: {self.h_user}")

    def get_play_window_handle(self) -> Optional[int]:
        """
        On Linux, screenshot/realplay can require a valid X11 window handle even under Xvfb.
        This replicates the C++ SimpleWindow approach and returns a tiny hidden window handle when possible.
        """
        if self._x11_window is not None:
            return int(self._x11_window.handle)
        if self._x11_tried:
            return None
        self._x11_tried = True

        # Only try when DISPLAY is set (Xvfb or real X11)
        if not (os.environ.get("DISPLAY") or "").strip():
            return None
        try:
            win = X11HiddenWindow.create()
        except Exception:
            win = None
        if win is None:
            return None
        self._x11_window = win
        return int(win.handle)

    def close(self) -> None:
        with self._lock:
            try:
                if getattr(self, "h_user", None) is not None and self.h_user >= 0:
                    self._lib.XCloudSDK_UnRegister(int(self.h_user))
            finally:
                self._lib.XCloudSDK_UnInit()
                if self._x11_window is not None:
                    try:
                        self._x11_window.close()
                    except Exception:
                        pass
                    self._x11_window = None

    def __enter__(self) -> "XCloudSDK":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def next_seq(self) -> int:
        with self._lock:
            seq = self._seq
            self._seq += 1
            if self._seq > 2_000_000_000:
                self._seq = 1
            return seq

    def set_device_credentials(self, dev_id: str, user: str, pwd: str) -> int:
        return int(
            self._lib.XCloudSDK_Device_SetLocalUserNameAndPwd(
                dev_id.encode("utf-8"), user.encode("utf-8"), (pwd or "").encode("utf-8")
            )
        )

    def dev_login(self, dev_id: str, *, seq: int, timeout_s: float = 8.0) -> tuple[int, int]:
        waiter = _Waiter(event=threading.Event())
        with self._lock:
            self._pending_login[seq] = waiter
            login_handle = int(self._lib.XCloudSDK_Device_DevLogin(int(self.h_user), dev_id.encode("utf-8"), int(seq)))
        if login_handle <= 0:
            with self._lock:
                self._pending_login.pop(seq, None)
            return login_handle, -1

        if not waiter.event.wait(timeout=max(0.0, timeout_s)):
            with self._lock:
                self._pending_login.pop(seq, None)
            return login_handle, -99991  # timeout-like
        with self._lock:
            self._pending_login.pop(seq, None)
        return login_handle, int(waiter.param1)

    def dev_logout(self, dev_id: str) -> int:
        return int(self._lib.XCloudSDK_Device_DevLogout(dev_id.encode("utf-8")))

    def media_realplay(
        self,
        dev_id: str,
        *,
        channel: int = 0,
        stream_type: int = 0,
        hwnd: Optional[int] = None,
        seq: int,
        timeout_s: float = 8.0,
    ) -> tuple[int, int]:
        waiter = _Waiter(event=threading.Event())
        hwnd_ptr = ctypes.c_void_p(0 if hwnd is None else int(hwnd))
        with self._lock:
            self._pending_realplay[seq] = waiter
            play_handle = int(
                self._lib.XCloudSDK_Device_MediaRealPlay(
                    int(self.h_user),
                    dev_id.encode("utf-8"),
                    int(channel),
                    int(stream_type),
                    hwnd_ptr,
                    int(seq),
                    b"",
                )
            )
        if play_handle <= 0:
            with self._lock:
                self._pending_realplay.pop(seq, None)
            return play_handle, -1

        if not waiter.event.wait(timeout=max(0.0, timeout_s)):
            with self._lock:
                self._pending_realplay.pop(seq, None)
            return play_handle, -99991
        with self._lock:
            self._pending_realplay.pop(seq, None)
        return play_handle, int(waiter.param1)

    def stop_media_play(self, play_handle: int) -> None:
        self._lib.XCloudSDK_Device_StopMediaPlay(int(play_handle))

    def make_keyframe(self, dev_id: str, *, channel: int, stream_type: int = 0) -> int:
        return int(self._lib.XCloudSDK_Device_MakeKeyFrame(dev_id.encode("utf-8"), int(channel), int(stream_type)))

    def wait_play_data(self, play_handle: int, *, timeout_s: float = 5.0) -> tuple[bool, int, int, int]:
        waiter = _Waiter(event=threading.Event())
        with self._lock:
            self._playdata_wait[int(play_handle)] = waiter

        try:
            ok = waiter.event.wait(timeout=max(0.0, timeout_s))
            return ok, int(waiter.msg_id), int(waiter.param1), int(waiter.param2)
        finally:
            with self._lock:
                self._playdata_wait.pop(int(play_handle), None)

    def snap_image(self, play_handle: int, file_path: str) -> int:
        return int(self._lib.XCloudSDK_Play_MediaSnapImage(int(play_handle), file_path.encode("utf-8")))

    def snap_image_ex(self, play_handle: int, file_path: str, *, channel_index: int = 0, param: str = "") -> int:
        return int(
            self._lib.XCloudSDK_Play_MediaSnapImageEx(
                int(play_handle), file_path.encode("utf-8"), int(channel_index), (param or "").encode("utf-8")
            )
        )

    def _bind(self) -> None:
        self._lib.XCloudSDK_Init.argtypes = [ctypes.c_char_p]
        self._lib.XCloudSDK_Init.restype = ctypes.c_int

        self._lib.XCloudSDK_UnInit.argtypes = []
        self._lib.XCloudSDK_UnInit.restype = None

        self._lib.XCloudSDK_SetLogTypeAndLevel.argtypes = [ctypes.c_int, ctypes.c_int]
        self._lib.XCloudSDK_SetLogTypeAndLevel.restype = None

        self._lib.XCloudSDK_RegisterCallback.argtypes = [_MessageCallback, ctypes.c_void_p]
        self._lib.XCloudSDK_RegisterCallback.restype = ctypes.c_int

        self._lib.XCloudSDK_UnRegister.argtypes = [ctypes.c_int]
        self._lib.XCloudSDK_UnRegister.restype = None

        self._lib.XCloudSDK_Device_SetLocalUserNameAndPwd.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
        self._lib.XCloudSDK_Device_SetLocalUserNameAndPwd.restype = ctypes.c_int

        self._lib.XCloudSDK_Device_DevLogin.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self._lib.XCloudSDK_Device_DevLogin.restype = ctypes.c_int

        self._lib.XCloudSDK_Device_DevLogout.argtypes = [ctypes.c_char_p]
        self._lib.XCloudSDK_Device_DevLogout.restype = ctypes.c_int

        self._lib.XCloudSDK_Device_MediaRealPlay.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_char_p,
        ]
        self._lib.XCloudSDK_Device_MediaRealPlay.restype = ctypes.c_int

        self._lib.XCloudSDK_Device_StopMediaPlay.argtypes = [ctypes.c_int]
        self._lib.XCloudSDK_Device_StopMediaPlay.restype = None

        self._lib.XCloudSDK_Device_MakeKeyFrame.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        self._lib.XCloudSDK_Device_MakeKeyFrame.restype = ctypes.c_int

        self._lib.XCloudSDK_Play_MediaSnapImage.argtypes = [ctypes.c_int, ctypes.c_char_p]
        self._lib.XCloudSDK_Play_MediaSnapImage.restype = ctypes.c_int

        self._lib.XCloudSDK_Play_MediaSnapImageEx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
        self._lib.XCloudSDK_Play_MediaSnapImageEx.restype = ctypes.c_int

    def _resolve_waiter(self, mapping: dict[int, _Waiter], seq: int) -> Optional[_Waiter]:
        waiter = mapping.get(seq)
        if waiter is not None:
            return waiter
        if seq == 0 and len(mapping) == 1:
            return next(iter(mapping.values()))
        return None

    def _on_message(
        self,
        hObject: int,
        nMsgId: int,
        nParam1: int,
        nParam2: int,
        nParam3: int,
        szString: Optional[bytes],
        pObject: int,
        lParam: int,
        nSeq: int,
        pUserData: int,
        pMsg: int,
    ) -> int:
        try:
            if nMsgId == ESXSDK_DEV_LOGIN:
                with self._lock:
                    waiter = self._resolve_waiter(self._pending_login, int(nSeq))
                    if waiter is not None:
                        waiter.msg_id = int(nMsgId)
                        waiter.param1 = int(nParam1)
                        waiter.param2 = int(nParam2)
                        waiter.event.set()
                return 0

            if nMsgId == ESXSDK_MEDIA_START_REAL_PLAY:
                with self._lock:
                    waiter = self._resolve_waiter(self._pending_realplay, int(nSeq))
                    if waiter is not None:
                        waiter.msg_id = int(nMsgId)
                        waiter.param1 = int(nParam1)
                        waiter.param2 = int(nParam2)
                        waiter.event.set()
                return 0

            if nMsgId in (EUIMSG_END_BUFFER_DATA, EUIMSG_PLAY_INFO, EUIMSG_YUV_DATA, EXSDK_DATA_FORMATE_FRAME):
                with self._lock:
                    waiter = self._playdata_wait.get(int(hObject))
                    if waiter is not None and not waiter.event.is_set():
                        waiter.msg_id = int(nMsgId)
                        waiter.param1 = int(nParam1)
                        waiter.param2 = int(nParam2)
                        waiter.event.set()
                return 0

            if nMsgId == EUIMSG_PLAY_SAVE_IMAGE_FILE:
                # 截图结果（这里不做同步依赖，业务以文件就绪检测为准）
                return 0
        except Exception:
            # 回调里绝不抛出异常到 C 层
            return 0

        return 0

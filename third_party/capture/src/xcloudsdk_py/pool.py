from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .sdk import XCloudSDK


@dataclass
class DeviceConnection:
    device_id: str
    user: str
    pwd: str
    channel: int
    login_handle: int
    play_handle: int
    created_at: float
    last_used: float


class DeviceConnectionPool:
    """
    连接池：复用 (login_handle, play_handle)。
    - 不开后台线程；每次 get_or_create 时顺带做一次清理（避免 stop 卡住之类的问题）
    - 适配“每小时调用一次”的场景：默认 2 小时空闲/2 小时最大寿命
    """

    def __init__(
        self,
        *,
        sdk: XCloudSDK,
        max_idle_s: float = 7200.0,
        max_age_s: float = 7200.0,
    ) -> None:
        self._sdk = sdk
        self._max_idle_s = float(max_idle_s)
        self._max_age_s = float(max_age_s)
        self._pool: dict[str, DeviceConnection] = {}

    @staticmethod
    def _key(device_id: str, user: str, channel: int) -> str:
        return f"{device_id}:{user}:{channel}"

    def stats(self) -> dict[str, int]:
        now = time.monotonic()
        total = len(self._pool)
        idle = 0
        for conn in self._pool.values():
            if now - conn.last_used > self._max_idle_s:
                idle += 1
        return {"totalConnections": total, "idleConnections": idle, "activeConnections": total - idle}

    def clear(self) -> None:
        for key in list(self._pool.keys()):
            self._close_and_remove(key)

    def get_or_create(
        self,
        *,
        device_id: str,
        user: str,
        pwd: str,
        channel: int = 0,
        login_timeout_s: float = 8.0,
        realplay_timeout_s: float = 8.0,
    ) -> DeviceConnection:
        self._cleanup_if_needed()

        key = self._key(device_id, user, channel)
        existing = self._pool.get(key)
        if existing and self._is_valid(existing):
            existing.last_used = time.monotonic()
            return existing
        if existing:
            self._close_and_remove(key)

        # create new connection
        self._sdk.set_device_credentials(device_id, user, pwd)
        login_seq = self._sdk.next_seq()
        login_handle, login_rc = self._sdk.dev_login(device_id, seq=login_seq, timeout_s=login_timeout_s)
        if login_handle <= 0 or login_rc < 0:
            raise RuntimeError(f"device login failed: handle={login_handle}, rc={login_rc}")

        play_seq = self._sdk.next_seq()
        hwnd = self._sdk.get_play_window_handle()
        play_handle, play_rc = self._sdk.media_realplay(
            device_id, channel=channel, stream_type=0, hwnd=hwnd, seq=play_seq, timeout_s=realplay_timeout_s
        )
        if play_handle <= 0 or play_rc < 0:
            self._sdk.dev_logout(device_id)
            raise RuntimeError(f"realplay failed: handle={play_handle}, rc={play_rc}")

        now = time.monotonic()
        conn = DeviceConnection(
            device_id=device_id,
            user=user,
            pwd=pwd,
            channel=channel,
            login_handle=login_handle,
            play_handle=play_handle,
            created_at=now,
            last_used=now,
        )
        self._pool[key] = conn
        return conn

    def release(self, conn: DeviceConnection) -> None:
        conn.last_used = time.monotonic()

    def remove(self, conn: DeviceConnection) -> None:
        key = self._key(conn.device_id, conn.user, conn.channel)
        self._close_and_remove(key)

    def _cleanup_if_needed(self) -> None:
        for key in list(self._pool.keys()):
            conn = self._pool.get(key)
            if not conn:
                continue
            if not self._is_valid(conn):
                self._close_and_remove(key)

    def _is_valid(self, conn: DeviceConnection) -> bool:
        now = time.monotonic()
        if now - conn.created_at > self._max_age_s:
            return False
        if now - conn.last_used > self._max_idle_s:
            return False
        if conn.play_handle <= 0 or conn.login_handle <= 0:
            return False
        return True

    def _close_and_remove(self, key: str) -> None:
        conn = self._pool.pop(key, None)
        if not conn:
            return
        try:
            if conn.play_handle > 0:
                self._sdk.stop_media_play(conn.play_handle)
        finally:
            self._sdk.dev_logout(conn.device_id)

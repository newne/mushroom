from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass
from typing import Optional


class X11WindowError(RuntimeError):
    pass


def _try_getattr(lib: ctypes.CDLL, name: str):
    try:
        return getattr(lib, name)
    except AttributeError:
        return None


def _load_x11() -> Optional[ctypes.CDLL]:
    for soname in ("libX11.so.6", "libX11.so"):
        try:
            return ctypes.CDLL(soname)
        except OSError:
            continue
    return None


@dataclass
class X11HiddenWindow:
    """
    Minimal hidden X11 window (like C++ SimpleWindow):
    - XOpenDisplay(NULL)
    - XCreateSimpleWindow(root, 1x1)
    - (optional) XMapWindow + XFlush
    """

    _lib: ctypes.CDLL
    _display: ctypes.c_void_p
    _window: int

    @property
    def handle(self) -> int:
        return int(self._window)

    @classmethod
    def create(cls) -> Optional["X11HiddenWindow"]:
        if not (os.environ.get("DISPLAY") or "").strip():
            return None

        lib = _load_x11()
        if lib is None:
            return None

        x_open = _try_getattr(lib, "XOpenDisplay")
        x_close = _try_getattr(lib, "XCloseDisplay")
        x_create = _try_getattr(lib, "XCreateSimpleWindow")
        x_destroy = _try_getattr(lib, "XDestroyWindow")

        if not (x_open and x_close and x_create and x_destroy):
            return None

        x_open.argtypes = [ctypes.c_char_p]
        x_open.restype = ctypes.c_void_p
        x_close.argtypes = [ctypes.c_void_p]
        x_close.restype = ctypes.c_int

        # Prefer function helpers if present (avoid relying on macros/struct layout)
        x_default_root = _try_getattr(lib, "XDefaultRootWindow")
        x_default_screen = _try_getattr(lib, "XDefaultScreen")
        x_root_window = _try_getattr(lib, "XRootWindow")

        if x_default_root:
            x_default_root.argtypes = [ctypes.c_void_p]
            x_default_root.restype = ctypes.c_ulong
        if x_default_screen:
            x_default_screen.argtypes = [ctypes.c_void_p]
            x_default_screen.restype = ctypes.c_int
        if x_root_window:
            x_root_window.argtypes = [ctypes.c_void_p, ctypes.c_int]
            x_root_window.restype = ctypes.c_ulong

        x_create.argtypes = [
            ctypes.c_void_p,  # Display*
            ctypes.c_ulong,  # parent Window
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        x_create.restype = ctypes.c_ulong

        dpy = ctypes.c_void_p(x_open(None))
        if not dpy:
            return None

        try:
            root: int | None = None
            if x_default_root:
                root = int(x_default_root(dpy))
            elif x_default_screen and x_root_window:
                screen = int(x_default_screen(dpy))
                root = int(x_root_window(dpy, screen))
            elif x_root_window:
                root = int(x_root_window(dpy, 0))

            if not root:
                raise X11WindowError("failed to get X11 root window")

            win = int(x_create(dpy, ctypes.c_ulong(root), 0, 0, 1, 1, 0, 0, 0))
            if not win:
                raise X11WindowError("XCreateSimpleWindow returned 0")

            # Optional: map/flush to ensure server realizes the window.
            x_map = _try_getattr(lib, "XMapWindow")
            x_flush = _try_getattr(lib, "XFlush")
            if x_map:
                x_map.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                x_map.restype = ctypes.c_int
                x_map(dpy, ctypes.c_ulong(win))
            if x_flush:
                x_flush.argtypes = [ctypes.c_void_p]
                x_flush.restype = ctypes.c_int
                x_flush(dpy)

            return cls(_lib=lib, _display=dpy, _window=win)
        except Exception:
            try:
                x_close(dpy)
            except Exception:
                pass
            raise

    def close(self) -> None:
        x_destroy = _try_getattr(self._lib, "XDestroyWindow")
        x_close = _try_getattr(self._lib, "XCloseDisplay")
        if not (x_destroy and x_close):
            return

        try:
            x_destroy.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            x_destroy.restype = ctypes.c_int
            x_destroy(self._display, ctypes.c_ulong(int(self._window)))
        finally:
            x_close.argtypes = [ctypes.c_void_p]
            x_close.restype = ctypes.c_int
            x_close(self._display)

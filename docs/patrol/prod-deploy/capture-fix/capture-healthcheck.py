#!/usr/bin/env python3
"""容器内健康检查：**必须覆盖虚拟显示**，不能只看 HTTP。

镜像自带的 ``/healthz`` 只反映「进程活着 + SDK 初始化完成」——DOCKER.md 自己也写着
"``/healthz``：只表示进程存活（应该始终返回 200）"。所以 Xvfb 挂掉时它照样 200，
拿它当 docker healthcheck 等于没探：历史上"进程健康、`/healthz` 绿、而每次采图
500"正是这么被放过的。

本检查做三件事，缺一不可：
  1. **X11 unix socket 真能连上**（残留 socket 文件会拒绝连接 → 识破"假就绪"）；
  2. HTTP ``/healthz`` 可达；
  3. ``/healthz`` 里 **``ready`` 必须为 true**——注意 ``ok`` 在 ``ready:false`` 时也是
     true（实测：SDK 还没初始化完，``/healthz`` 依然 200 + ``ok:true``），只看 ``ok``
     会把"SDK 没起来、采图必然 503"的服务判成健康。``start_period`` 负责放过启动期。

**故意不做真实抓拍**：采图服务是单 worker 串行的，且相机与老系统共享，健康检查
按固定周期去打相机既会互相排队、也会干扰共用同一台 238 的老系统。

退出码：0 = 健康；1 = 不健康（docker 记为 unhealthy，并给出可读原因）。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import urllib.error
import urllib.request

DEFAULT_DISPLAY = ":99"
HTTP_TIMEOUT_S = 3.0
SOCKET_TIMEOUT_S = 1.0


def display_number() -> str:
    disp = os.environ.get("XVFB_DISPLAY") or os.environ.get("DISPLAY") or DEFAULT_DISPLAY
    return disp[1:] if disp.startswith(":") else disp


def check_x_socket() -> tuple[bool, str]:
    path = f"/tmp/.X11-unix/X{display_number()}"
    if not os.path.exists(path):
        return False, f"no X socket at {path}"
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(SOCKET_TIMEOUT_S)
        s.connect(path)
        s.close()
    except OSError as exc:
        # 文件在、连不上 → 典型的残留 socket（Xvfb 已死）
        return False, f"X socket {path} not connectable: {exc}"
    return True, f"X socket {path} ok"


def check_http() -> tuple[bool, str]:
    port = os.environ.get("PORT", "7003")
    url = f"http://127.0.0.1:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return False, f"GET {url} failed: {exc}"
    try:
        payload = json.loads(body)
    except ValueError:
        return False, f"GET {url} returned non-JSON: {body[:80]!r}"
    ok = payload.get("ok") is True
    ready = payload.get("ready") is True
    if ok and ready:
        return True, f"GET {url} ok (ready)"
    if ok and not ready:
        # 真实踩到过：容器刚起时 /healthz 返回 200 + ok:true，但 ready:false，
        # 此时任何采图都会 503 service initializing。
        return False, (
            f"GET {url} ok=true 但 ready=false（SDK 未就绪，采图会 503）"
            f" init_error={payload.get('init_error')!r}"
        )
    return False, f"GET {url} not ok: {body[:120]}"


def main() -> int:
    x_ok, x_msg = check_x_socket()
    http_ok, http_msg = check_http()
    verdict = "healthy" if (x_ok and http_ok) else "UNHEALTHY"
    print(f"[{verdict}] {x_msg}; {http_msg}")
    if not x_ok:
        print(
            "        提示：X socket 不可连 = 采图会 500。查看 /app/log/xvfb.log，"
            "容器应自动重启；若持续 crash-loop 则该 display 被别的进程占用。"
        )
    return 0 if (x_ok and http_ok) else 1


if __name__ == "__main__":
    sys.exit(main())

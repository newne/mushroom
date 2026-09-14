#!/usr/bin/env python3
"""本机开发用的巡检台前端服务器（静态页 + `/api/` 反代）。

为什么需要它：前后端分离之后（ADR-0015），页面**不能再用 `file://` 打开**——页面里的
取数是 `/api/...` 这样的绝对路径，`file://` 下会变成 `file:///api/...`。上机时这层
由 nginx 承担（`nginx.conf`），本机开发时用它顶上，省掉起容器。

    cd patrol-workspace && uv run --frozen uvicorn deploy.console:create_app --factory --port 8001
    python web/console/dev.py                                                # → http://127.0.0.1:8080/
    python web/console/dev.py --api http://10.77.77.39:8001 --host 0.0.0.0   # 指到真机 / 给平板看

只做两件事，与 nginx.conf 一致：
  * `/` 与未知路径都给 `index.html`，且带 no-store（改完刷新就是最新的）；
  * `/api/*` 原样转发到后端（保留前缀），把状态码与 body 透传回来。
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socket
import socketserver
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = "index.html"


class DevHandler(http.server.SimpleHTTPRequestHandler):
    """静态文件 + `/api/` 反代；其余路径回落到 `index.html`。"""

    api_base = "http://127.0.0.1:8001"

    def _is_api(self) -> bool:
        # `/healthz` 与 nginx.conf 一样走反代：否则会被回落到 index.html——
        # **200 但什么都没说**，正是本项目吃过亏的那种"探针骗人"。
        return self.path in ("/api", "/healthz") or self.path.startswith("/api/")

    def _proxy(self, method: str) -> None:
        url = self.api_base.rstrip("/") + self.path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=method)
        # 只透传内容类型：Host/Authorization 之类由 urllib 自己决定，浏览器侧的
        # Cookie 不该被带到一个不同 origin 的后端上。
        if self.headers.get("Content-Type"):
            req.add_header("Content-Type", self.headers["Content-Type"])
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = resp.read()
                status = resp.status
                ctype = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as exc:
            # 403/409 是**正常的业务回答**（巡检中拒绝手动、指令不合法），必须原样透传，
            # 否则页面永远只看到 500，现场就没法判断到底发生了什么。
            payload = exc.read()
            status = exc.code
            ctype = exc.headers.get("Content-Type", "application/json")
        except urllib.error.URLError as exc:
            reason = exc.reason
            payload = f'{{"ok": false, "error": "后端不可达: {reason}"}}'.encode()
            status, ctype = 502, "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _fallback_entry(self) -> None:
        """与 nginx 的 `try_files $uri $uri/ /index.html` 一致。

        未知路径**不是 404**，而是把 `index.html` 交出去——页面自己按 `location.search`
        解析深链（`?mode=history&station=S105`）。开发时若在这里 404、上机却不 404，
        就会得到一个"本地怎么都复现不出来"的差异，所以两边必须同规矩。
        """
        path, sep, query = self.path.partition("?")
        if path in ("", "/"):
            self.path = "/" + ENTRY + sep + query
            return
        try:
            exists = (ROOT / path.lstrip("/")).resolve().is_file()
        except OSError:
            exists = False
        if not exists:
            self.path = "/" + ENTRY + sep + query

    def do_GET(self) -> None:
        if self._is_api():
            self._proxy("GET")
            return
        self._fallback_entry()
        super().do_GET()

    def do_POST(self) -> None:
        self._proxy("POST")

    def do_DELETE(self) -> None:
        self._proxy("DELETE")

    def list_directory(self, path):
        # 目录列表在这里从没有用：未知路径已经回落到 index.html，真走到这儿说明
        # 请求的是一个不存在的目录——按"没有"处理，别渲染成文件浏览器。
        self.send_error(404, "No directory listing")

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("  " + (fmt % args) + "\n")


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the patrol console front-end (dev).")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8080, help="TCP port (default 8080)")
    ap.add_argument("--api", default="http://127.0.0.1:8001", help="后端 origin")
    args = ap.parse_args()

    if not (ROOT / ENTRY).is_file():
        sys.stderr.write(f"entry point not found: {ROOT / ENTRY}\n")
        return 1

    handler = type("BoundDevHandler", (DevHandler,), {"api_base": args.api})
    try:
        httpd = ReusableTCPServer(
            (args.host, args.port), functools.partial(handler, directory=str(ROOT))
        )
    except OSError as exc:
        sys.stderr.write(f"cannot bind {args.host}:{args.port} -> {exc}\n")
        return 1

    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print("巡检台前端（开发）")
    print(f"  页面: http://{shown}:{args.port}/")
    print(f"  接口: /api/  →  {args.api}")
    if args.host in ("0.0.0.0", "::"):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("10.255.255.255", 1))
            print(f"  局域网: http://{probe.getsockname()[0]}:{args.port}/")
        except OSError:
            pass
        finally:
            probe.close()
    print(f"  serving: {ROOT}")
    sys.stdout.flush()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

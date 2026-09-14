#!/usr/bin/env python3
"""Local dev server for the patrol console prototype.

Serves this directory over HTTP so the prototype can be opened in a real
browser (and from other devices on the LAN, e.g. a tablet on the mushroom
farm floor) instead of via file://.

    python serve.py                 # http://127.0.0.1:8800/
    python serve.py --port 9000
    python serve.py --host 0.0.0.0  # expose on the LAN

Behaviour notes:
  * GET / is mapped to /prototype.html, so the root URL works and any
    query string (e.g. ?mode=history&station=S105) is preserved for the
    front-end deep-link handling.
  * No caching headers are sent, so editing prototype.html and hitting
    reload always shows the latest version.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socket
import socketserver
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = "prototype.html"


class ConsoleHandler(http.server.SimpleHTTPRequestHandler):
    """Static handler that maps / onto the prototype entry point.

    The rewrite must be query-aware: the front-end reads deep-link params
    (?mode=history&station=S05) off location.search, so the query string has
    to survive. Matching the raw self.path is not enough -- "/?mode=history"
    is not equal to "/", and would otherwise fall through to directory
    listing.
    """

    def _rewrite_entry(self) -> None:
        path, sep, query = self.path.partition("?")
        if path in ("/", "", "/index.html"):
            self.path = "/" + ENTRY + sep + query

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        self._rewrite_entry()
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib naming
        self._rewrite_entry()
        super().do_HEAD()

    def list_directory(self, path):  # noqa: ANN001, D102 - stdlib override
        """Directory listings are never useful here; treat them as missing.

        Without this, a mistyped path silently renders a file browser instead
        of an error, which is confusing while iterating on the prototype.
        """
        self.send_error(404, "No directory listing")
        return None

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("  %s\n" % (fmt % args))


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def lan_ip() -> str:
    """Best-effort LAN address for the 'open this on your tablet' hint."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("10.255.255.255", 1))
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Serve the patrol console prototype.")
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8800, help="TCP port (default 8800)")
    args = ap.parse_args()

    entry = ROOT / ENTRY
    if not entry.is_file():
        sys.stderr.write("entry point not found: %s\n" % entry)
        return 1

    handler = functools.partial(ConsoleHandler, directory=str(ROOT))
    try:
        httpd = ReusableTCPServer((args.host, args.port), handler)
    except OSError as exc:
        sys.stderr.write("cannot bind %s:%s -> %s\n" % (args.host, args.port, exc))
        sys.stderr.write("another process is probably using that port; try --port 8801\n")
        return 1

    shown = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print("patrol console prototype")
    print("  root   : http://%s:%d/" % (shown, args.port))
    print("  history: http://%s:%d/?mode=history&station=S05" % (shown, args.port))
    if args.host in ("0.0.0.0", "::"):
        print("  LAN    : http://%s:%d/" % (lan_ip(), args.port))
    print("  serving: %s" % ROOT)
    print("Ctrl+C to stop.")
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

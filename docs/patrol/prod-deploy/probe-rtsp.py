#!/usr/bin/env python3
"""只读探测（带凭据）：找出这台 DVR 认的 RTSP 路径。

判据：认证通过后，**存在的**路径给 200 + SDP；不存在的给 404。
只做 DESCRIBE（协商，不开流、不录制）。支持 Basic 与 Digest 两种认证。

用法：python3 probe_rtsp_auth.py <ip> <user> <pwd>
"""
from __future__ import annotations

import base64
import hashlib
import re
import socket
import sys

IP, USER, PWD = sys.argv[1], sys.argv[2], sys.argv[3]
PORT = 554
CANDIDATES = [
    "/Streaming/Channels/101", "/Streaming/Channels/102",
    "/cam/realmonitor?channel=1&subtype=0", "/cam/realmonitor?channel=1&subtype=1",
    "/11", "/12", "/ch0.h264", "/ch1.h264", "/h264/ch1/main/av_stream",
    "/h264/ch1/sub/av_stream", "/live/ch0", "/av0_0", "/av0_1",
]


def talk(path: str, extra_headers: str = "") -> str:
    req = (f"DESCRIBE rtsp://{IP}:{PORT}{path} RTSP/1.0\r\nCSeq: 1\r\n"
           f"Accept: application/sdp\r\n{extra_headers}\r\n")
    with socket.create_connection((IP, PORT), timeout=4) as s:
        s.sendall(req.encode())
        return s.recv(1024).decode("utf-8", "replace")


def basic() -> str:
    token = base64.b64encode(f"{USER}:{PWD}".encode()).decode()
    return f"Authorization: Basic {token}\r\n"


def digest(challenge: str, path: str) -> str:
    def field(name: str) -> str:
        m = re.search(rf'{name}="?([^",]+)"?', challenge)
        return m.group(1) if m else ""

    realm, nonce, qop = field("realm"), field("nonce"), field("qop")
    uri = f"rtsp://{IP}:{PORT}{path}"
    ha1 = hashlib.md5(f"{USER}:{realm}:{PWD}".encode()).hexdigest()
    ha2 = hashlib.md5(f"DESCRIBE:{uri}".encode()).hexdigest()
    if qop:
        nc, cnonce = "00000001", "0a4f113b"
        resp = hashlib.md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}".encode()).hexdigest()
        parts = (f'username="{USER}", realm="{realm}", nonce="{nonce}", uri="{uri}", '
                 f"qop={qop}, nc={nc}, cnonce=\"{cnonce}\", response=\"{resp}\"")
    else:
        resp = hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
        parts = f'username="{USER}", realm="{realm}", nonce="{nonce}", uri="{uri}", response="{resp}"'
    return f"Authorization: Digest {parts}\r\n"


for path in CANDIDATES:
    try:
        first = talk(path)
        line = first.splitlines()[0] if first else "(无响应)"
        if "401" in line:
            challenge = next((ln for ln in first.splitlines()
                              if ln.lower().startswith("www-authenticate:")), "")
            scheme = "Digest" if "digest" in challenge.lower() else "Basic"
            if not challenge:                       # 有的设备不带 challenge 头：先试 Basic
                second = talk(path, basic())
            else:
                second = talk(path, digest(challenge, path) if scheme == "Digest" else basic())
            line2 = second.splitlines()[0] if second else "(无响应)"
            has_sdp = "application/sdp" in second.lower()
            mark = "✅ 可用" if "200" in line2 and has_sdp else f"✗ {line2}"
            print(f"{mark:<28} {scheme:<6} ← {path}")
        else:
            print(f"{line:<28} {'':<6} ← {path}")
    except OSError as e:
        print(f"(连接失败 {e})           ← {path}")

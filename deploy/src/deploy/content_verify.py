"""帧内容复核（票 13）：把"取到的是不是它自己那一份"变成可查的事实。

`ok=1` 只证明**这一站取到了文件**，证不了"内容属于这一站"。2026-10-09 那轮是反例：
48 帧里 **35 帧字节完全同一份**（同一张陈旧近黑帧，`p99`=2.0），横跨 S211→S401，
而 round 行写着 `ok=1 / 48 帧 / 0 失败`。对比 10-08 的近黑帧各站字节互不相同——
那是各站独立的真欠曝，两者**性质不同**，不能混为一类。

本模块只做一件事：把 `cloud_url` 取回来算 sha256，交给 `PatrolDaemon(content_hash=…)`
在写索引前逐帧标注（`content_sha256`；跨站重复再加 `frame_dup`/`dup_group`）。
**只读、不改图、不丢帧**；可疑帧与可疑轮必须留档，由下游按标记排除。

代价：每帧一次取回（线上实测中位 ≈700 KB/帧 ⇒ 48 站约 34 MB/轮），所以是**显式开关**
（`m1 --verify-content`），默认不查。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable


def _fetch(url: str, timeout_s: float = 15.0) -> bytes:
    """从 MinIO 取回对象字节（现场是回环/内网地址，不走代理）。"""
    import httpx

    resp = httpx.get(url, timeout=timeout_s)
    resp.raise_for_status()
    return resp.content


def make_content_hash(fetch: Callable[[str], bytes] | None = None,
                      log: Callable[[str], None] = print) -> Callable[[dict], str | None]:
    """造 `PatrolDaemon(content_hash=…)` 要的可调用对象：索引行 → sha256（无 URL 时 None）。

    取图失败**让异常往上抛**：daemon 会记下 `content_check_error` 并停止本轮判定——
    宁可"这一轮没查成"，也不要拿半个结果去说"没有重复"。
    """
    fetch = fetch or _fetch

    def content_hash(meta: dict) -> str | None:
        url = meta.get("cloud_url")
        if not url:
            return None
        return hashlib.sha256(fetch(url)).hexdigest()

    return content_hash

"""帧内容复核的部署侧（票 13）：sha256 取自真实取回的字节，且**不让取图失败静默**。"""

import hashlib

import pytest
from deploy.content_verify import make_content_hash


def test_content_hash_hashes_the_fetched_bytes():
    seen: list[str] = []
    content_hash = make_content_hash(fetch=lambda url: (seen.append(url), b"abc")[1])
    assert content_hash({"cloud_url": "http://minio/mogu/x.jpg"}) == \
        hashlib.sha256(b"abc").hexdigest()
    assert seen == ["http://minio/mogu/x.jpg"]


def test_content_hash_is_none_without_a_cloud_url():
    """没有 `cloud_url`（失败帧/未落云）⇒ 不产生指纹，也不去取图。"""
    seen: list[str] = []
    content_hash = make_content_hash(fetch=lambda url: (seen.append(url), b"x")[1])
    assert content_hash({"station_id": "S01"}) is None
    assert seen == []


def test_fetch_failure_escapes_instead_of_reporting_no_duplicates():
    """取图失败必须往上抛：让 daemon 记 `content_check_error`，而不是谎报"没有重复"。"""
    def boom(_url: str) -> bytes:
        raise RuntimeError("minio 502")

    with pytest.raises(RuntimeError, match="502"):
        make_content_hash(fetch=boom)({"cloud_url": "http://minio/x.jpg"})


def test_two_identical_payloads_hash_the_same():
    """同一份字节 ⇒ 同一指纹（这正是 2026-10-09 那轮 35 帧被判为复制判据的来源）。"""
    content_hash = make_content_hash(fetch=lambda _url: b"same-bytes")
    a = content_hash({"cloud_url": "u1"})
    b = content_hash({"cloud_url": "u2"})
    assert a == b == hashlib.sha256(b"same-bytes").hexdigest()

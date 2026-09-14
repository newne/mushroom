from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MinIOConfig:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str = "us-east-1"


def load_json_file(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be object: {file_path}")
    return data


def _pick_first(config: dict[str, Any], keys: list[str], default: str | None = None) -> str | None:
    for key in keys:
        value = config.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        return str(value)
    return default


def load_minio_config(path: str | Path = "minio_config.json") -> MinIOConfig:
    cfg = load_json_file(path)

    endpoint = _pick_first(cfg, ["endpoint", "Endpoint", "MLFLOW_S3_ENDPOINT_URL"])
    access_key = _pick_first(cfg, ["access_key", "AccessKey", "AWS_ACCESS_KEY_ID", "MINIO_ROOT_USER"])
    secret_key = _pick_first(cfg, ["secret_key", "SecretKey", "AWS_SECRET_ACCESS_KEY", "MINIO_ROOT_PASSWORD"])
    bucket = _pick_first(cfg, ["bucket", "Bucket", "MINIO_BUCKET_NAME"])
    region = _pick_first(cfg, ["region", "Region"], default="us-east-1")

    missing: list[str] = []
    if not endpoint:
        missing.append("endpoint/Endpoint/MLFLOW_S3_ENDPOINT_URL")
    if not access_key:
        missing.append("access_key/AccessKey/AWS_ACCESS_KEY_ID/MINIO_ROOT_USER")
    if not secret_key:
        missing.append("secret_key/SecretKey/AWS_SECRET_ACCESS_KEY/MINIO_ROOT_PASSWORD")
    if not bucket:
        missing.append("bucket/Bucket/MINIO_BUCKET_NAME")

    if missing:
        raise ValueError(f"minio config missing keys: {', '.join(missing)} (file: {path})")

    return MinIOConfig(
        endpoint=str(endpoint),
        access_key=str(access_key),
        secret_key=str(secret_key),
        bucket=str(bucket),
        region=str(region or "us-east-1"),
    )


def load_sdk_init_json(path: str | Path = "XCloudSDKTest_config.ini") -> str:
    """
    C++ 版本通过 XNetSDKCfg::ReadConfig 读到 JSON，然后传给 XCloudSDK_Init。
    这个 repo 里的 XCloudSDKTest_config.ini 本质就是一段 JSON 文本。
    """
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8").strip()
    if not text:
        return "{}"
    # 允许用户直接给 JSON 文本，也允许是 .ini 但内容为 JSON
    json.loads(text)
    return text


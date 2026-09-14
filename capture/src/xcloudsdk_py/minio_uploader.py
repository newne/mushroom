from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .config import MinIOConfig


class MinIOUploadError(RuntimeError):
    pass


@dataclass(frozen=True)
class UploadResult:
    bucket: str
    object_name: str
    etag: Optional[str] = None


class MinIOUploader:
    def __init__(self, config: MinIOConfig) -> None:
        self._cfg = config
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
            region_name=config.region,
        )
        self._bucket_ready: set[str] = set()

    @property
    def default_bucket(self) -> str:
        return self._cfg.bucket

    @property
    def endpoint(self) -> str:
        return self._cfg.endpoint

    def ensure_bucket(self, bucket: str) -> None:
        if bucket in self._bucket_ready:
            return
        try:
            self._client.head_bucket(Bucket=bucket)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchBucket"):
                self._client.create_bucket(Bucket=bucket)
            elif code in ("403", "AccessDenied"):
                # 某些策略下可能禁止 head_bucket，但 put_object 仍可用；直接放行。
                self._bucket_ready.add(bucket)
                return
            else:
                raise MinIOUploadError(f"head_bucket failed: {code}: {e}") from e
        self._bucket_ready.add(bucket)

    def upload_file(
        self,
        *,
        file_path: str | Path,
        object_name: str,
        bucket: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> UploadResult:
        bucket_name = bucket or self.default_bucket
        self.ensure_bucket(bucket_name)

        path = Path(file_path)
        if not path.exists():
            raise MinIOUploadError(f"file not found: {path}")

        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type
        else:
            guessed, _ = mimetypes.guess_type(str(path))
            if guessed:
                extra_args["ContentType"] = guessed

        try:
            if extra_args:
                self._client.upload_file(str(path), bucket_name, object_name, ExtraArgs=extra_args)
            else:
                self._client.upload_file(str(path), bucket_name, object_name)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            raise MinIOUploadError(f"upload_file failed: {code}: {e}") from e

        return UploadResult(bucket=bucket_name, object_name=object_name, etag=None)

from __future__ import annotations

import argparse

import uvicorn

from .server import AppConfig, create_app


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xcloudsdk-py")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--http", "--port", dest="port", type=int, default=7003)
    p.add_argument("--lib", dest="lib_path", default="lib/x86_64/Release/libXCloudSDK.so")
    p.add_argument("--sdk-config", dest="sdk_config_path", default="XCloudSDKTest_config.ini")
    p.add_argument("--minio-config", dest="minio_config_path", default="minio_config.json")
    p.add_argument("--picture-dir", dest="picture_dir", default="saved_datas/picture")
    p.add_argument("--disable-pool", action="store_true")
    p.add_argument("--reload", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = AppConfig(
        lib_path=args.lib_path,
        sdk_config_path=args.sdk_config_path,
        minio_config_path=args.minio_config_path,
        picture_dir=args.picture_dir,
        enable_pool=not args.disable_pool,
    )

    print(f"[xcloudsdk-py] starting http server on {args.host}:{int(args.port)}", flush=True)
    app = create_app(config)
    uvicorn.run(
        app,
        host=args.host,
        port=int(args.port),
        reload=bool(args.reload),
        workers=1,
    )
    return 0

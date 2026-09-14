# 架构梳理与迁移说明

## 1. 现状（XCloudSDKDemo_CLI）
- 采集：C++ 通过 `libXCloudSDK.so` 登录设备、开实时预览、抓图保存到文件
- 上传：C++ 通过 `python3 minio_upload_helper.py ...` 触发上传（boto3）
- 服务：自研 C++ HTTP server（单线程串行处理请求）
- 生产：systemd + `xvfb-run`（无头环境必须）
- 已知时序坑：长间隔触发时，若未等到新帧/关键帧就抓图，会拿到上一轮缓存帧

## 2. 目标（Mushroom_CLI）
把“业务逻辑”统一为 Python：
- Python 提供 HTTP API（保持现有参数/路径兼容）
- Python 直接上传 MinIO（不再调用外部脚本）
- Python 通过 `ctypes` 调用 `libXCloudSDK.so` 完成采集（仍依赖厂商 SDK）

> 说明：如果要求彻底移除厂商 SDK（不再依赖 `.so`），需要切换到 RTSP/ONVIF/FFmpeg 方案，这属于另一条路线。

## 3. 核心模块
- `xcloudsdk_py.sdk`: `ctypes` 封装 XCloudSDK（Init/RegisterCallback/Login/RealPlay/MakeKeyFrame/Snap/Stop/Logout）
- `xcloudsdk_py.capture`: 截图流程（dynamic/pool），文件路径与安全校验，文件就绪检测
- `xcloudsdk_py.pool`: 连接池（复用 login/play handle），按“2小时”空闲/年龄策略清理
- `xcloudsdk_py.minio_uploader`: MinIO 上传（boto3 S3 API，兼容现有 `minio_config.json` 多种字段）
- `xcloudsdk_py.server`: FastAPI HTTP 服务（/dynamic_capture、/fast_capture、/pool_capture）

## 4. 关键时序（避免“旧帧”）
每次截图都执行：
1) RealPlay 回调确认成功（`ESXSDK_MEDIA_START_REAL_PLAY`）
2) `MakeKeyFrame` 强制关键帧
3) 等待播放数据回调（如 `EUIMSG_END_BUFFER_DATA` / `EUIMSG_PLAY_INFO` / `EUIMSG_YUV_DATA` / `EXSDK_DATA_FORMATE_FRAME`）
4) 再执行抓图

这样在“每小时一次”这类长间隔触发下，显著降低抓到上一轮缓存帧的概率。

## 5. 并发策略
厂商 SDK 通常不保证线程安全；推荐：
- **单 worker** 部署（uvicorn `--workers 1`）
- 服务内部对 SDK 操作加全局互斥（串行化 SDK 调用）

## 6. 配置约定
- SDK 初始化配置：默认读取工作目录下 `XCloudSDKTest_config.ini`（实际为 JSON）
- MinIO 配置：默认读取 `minio_config.json`（大小写/`MLFLOW_*`/`AWS_*` 键兼容）
- 图片目录：默认 `./saved_datas/picture`

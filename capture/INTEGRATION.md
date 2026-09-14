# 巡检系统集成说明（capture ← patrol）

本目录由 `mushroom_cli-main` vendored 而来（2026-08-30，票 00），**代码原样保留，不做改造**。
它是验证过的 XCloud 相机厂商集成，详见 `README.md` 与 `docs/ARCHITECTURE.md`。

> 现场部署的**加固与运维说明**见 `docs/adr/0009-capture-service-virtual-display-hardening.md`。
> 现场服务**不是**用本目录的源码起容器，而是厂商离线镜像 `xcloudsdk-py:0.1.0`。

## patrol 如何调用（设计约定见 ../.scratch/fmc4030-camera-scan/spec.md §4.2）

- 采图入口二选一（**四个端点的参数口径完全一致**，实测）：
  - `GET http://<主机>:7003/pool_capture?ip=<相机IP>&user=admin&pwd=&storage=cloud&filename=<对象名>`
  - `GET http://<主机>:7003/dynamic_capture?ip=<相机IP>&user=admin&pwd=&storage=local&filename=<对象名>`
- `storage=cloud` 直传 MinIO（失败则返回错误），`storage=local` 存服务端
  `saved_datas/picture/`。响应 JSON 的 `success` 字段是采图成败判定。
- **相机无密码**（2026-09-12 现场确认）：`user=admin`，`pwd` 留空。
- `filename` 由 patrol 生成：`{box_id}/{date}/{box_id}_{station_id}_{angle}_{HHMMSS}`
  （支持子目录前缀，服务自动补 `.jpg` 并做路径校验）
- **单次采图实测 5.5–5.6 s**（含取流 + MakeKeyFrame + 等帧 + 落盘）——比早期估的 1–5 s 偏大，
  补光灯窗口与客户端超时按 **≥ 60 s** 预留（还要容忍与旧系统排队）。
- 服务为**单 worker 串行**（厂商 SDK 非线程安全），patrol 侧也保持串行调用，禁止并发采图。

## 健康检查口径（实测，**不要误读**）

| 端点 | 返回 | 含义 |
| --- | --- | --- |
| `GET /healthz` | `200 {"ok":true,"init_started":…,"init_done":…,"ready":…,"init_error":…}` | **只表示进程存活**（`DOCKER.md` 自述）。⚠️ `ready:false` 时它**依然 200 且 `ok:true`** —— 此时任何采图都返回 **503 service initializing** |
| `GET /readyz` | 同上 | 同上 |
| `GET /` | `{"service":"Mushroom_CLI","endpoints":[…]}` | 端点清单 |

- 判定"能不能采图"必须看 **`ready`**，不能只看 HTTP 状态码或 `ok`。
- 现场容器的 **docker healthcheck 已改为独立脚本**，同时校验
  「X11 socket 真能连」+「`/healthz` 的 `ready=true`」；**故意不做真实抓拍**（相机与旧系统共用）。
- 容器内**没有 `curl`**（`ps`/`pgrep` 也没有）——`DOCKER.md` 里 `docker exec … curl` 的排障指引
  对这个镜像不成立，请用 `python3` 或 `capture-status.sh`。

## 部署要点（Ubuntu x86_64，现场形态）

- 依赖厂商 `libXCloudSDK.so`（x86_64 only）与工作目录下的 `XCloudSDKTest_config.ini`（实为 JSON）、`minio_config.json`
- **无头环境必须有 X server**（SDK 需要隐藏 X11 窗口，否则每次采图都 500）：
  现场容器里由 `/entrypoint.sh` 启动 `Xvfb :99`（`USE_XVFB` 默认 1）。
  ⚠️ 该入口原本在 Xvfb 起不来时**只警告并继续跑**，造成"`/healthz` 绿、每次采图 500"且不会被
  `restart` 策略拉起 —— 已按 ADR-0009 换成 fail-closed 版本。
- 离线部署走 `scripts/make_offline_bundle.sh`（见 `docs/DOCKER.md`）
- 现场约束：
  - 相机 `192.168.1.238` **与旧静态多相机系统共用**，它每小时 `:01:30` 左右调用 6 台相机
    （238/235/236/233/231/232，整批约 30 s）→ 新系统**避开 `:01:00–:02:00`**
  - 老系统与 `patrol` 都访问同一台 `192.168.1.239` 控制器/相机网段，
    目标机 `10.77.77.39` 的 `eno1 = 192.168.1.250/24` 直连，不跨网段

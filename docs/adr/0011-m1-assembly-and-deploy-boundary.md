# ADR-0011：M1 装配入口与部署边界（patrol 纯逻辑 / deploy 胶水）

日期：2026-09-13
状态：已接受；**第 1 条与"后果"里关于 venv 部署的表述由 ADR-0012 修订**（两个工程合并、
部署形态改容器，但"patrol 库内零网络"这条边界不放宽）；第 6 条的时刻表由 ADR-0012 改为
"由算法工程的调度器每 3 小时触发一次"
背景：M1（主机调度巡检）的**逻辑**早就齐了——`round.PatrolRound`（一轮生命周期）、
`orchestrator.StationCapture`（单站闭环）、`daemon.PatrolDaemon`（重连/落库/同步）——
但它**起不来**（gap-list J1/J2/J3）：没人把依赖注入进去、没有时刻表循环、没有真实 HTTP。
再叠加两条已接受但未落地的约束：`patrol` 有"**库内零网络调用**"的安全基线（`patrol.links`
与 `patrol.capture_client` 的 docstring），而 ADR-0005 要求的"采图结果落图像索引"实测
**根本没写**（`StationCapture.run` 的 `object_name`/`cloud_url` 在 daemon 里被直接丢弃）。

## 决定

### 1. 部署边界：新增 `deploy` 包，patrol 保持零网络

- `deploy/` 是**部署侧胶水**包（uv workspace 成员），依赖 `patrol` + `httpx`，持有：
  真实 HTTP transport、M1 装配入口（`python -m deploy.m1`）、systemd 单元。
- `patrol` 一行网络代码都不加。这条边界不是为了洁癖：它让 patrol 可以脱离网络被单测、
  静态扫描与审计，而"能发网络请求"的能力被收在一个可枚举的包里。

### 2. Transport 的显式契约

- **目标白名单**：URL 的 `host:port` 不在名单内直接拒绝（`file:///etc/passwd`、同机别的
  端口都进不来）。patrol 侧的 URL 全是字面量常量，白名单把口头约定变成运行时可验证的约束。
- **超时按实测留足**：单次采图实测 `capture_time_ms = 5200 ms`，默认 90 s。服务慢不等于
  服务坏——超时小于设计内耗时会制造假故障。
- **`accept_json_errors`**：采图服务（vendored `capture`）用 **HTTP 500 表示"这一次没拍成"**，
  响应体仍是完整信封（`{"success": false, "error_code": ..., "message": ...}`）。对这类端点
  必须"解析后交给业务层"，否则会把业务失败误判成基础设施故障，并丢掉重试所需的 `error_code`。
  未声明信封键的非 2xx（如同步端点的 5xx）依旧按链路故障抛出，才能被 `RetryingTransport` 重试。

### 3. 链路错误必须折算进 `CaptureError`

`CaptureClient._request` 捕获 `TransportError` 并抛 `CaptureError`。这**不是**风格问题：
`TransportError` 不是 `CaptureError`，放它穿透会同时绕过 `StationCapture` 的站位级重试与
`PatrolRound` 的"跳过该站位"，实测表现为**一次网络抖动打断整轮 60 站位巡检**（`--once` 退出码 1、
outbox 空）。修复前已复现，修复后由 `test_transport_error_becomes_capture_error_so_the_round_keeps_going`
守护。

### 4. 图像索引落地（ADR-0005 的遗留）

`PatrolDaemon.run_cycle` 在写完 round 汇总后，把**每站结果逐条**落 outbox
（`kind="image_index"`，含 `object_name`/`cloud_url`/坐标/角度档/`ok`）；失败站位也落一行
（`ok=false` + `error`，`object_name` 为空）。`analysis` 侧补 `images` 表与 `/ingest` 分支、
`/images` 查询端点。主键取 `(ts, station_id, angle_profile)` 而非 ADR-0005 举例的
`(ts, object_name)`——失败行没有 `object_name`，含 NULL 的列做不了干净的幂等键。

### 5. 任何中断先急停

`PatrolDaemon.run_cycle` 用 `except BaseException` 包住整轮：运动失败、`KeyboardInterrupt`、
以及任何未预期异常，都先 `stop_everything()` 再决定怎么报。急停**自身**的失败只写日志，
不覆盖原始异常（否则现场看到的是与故障无关的报错）。绝不让轴停在运动中无人看管。

### 6. 时刻表默认避开老系统采图窗口

默认每小时第 5 分钟启动。采图服务与老系统**共用同一台相机**，老系统每小时
`:01:0x–:02:00` 批量采 6 台；`--at-minute` 可调，`--interval` 可改用固定间隔。

## 后果

- M1 单轮可跑通（冒烟 4 站位 `ok`、`n_failures=0`）；整轮时长以运行日志的每站
  `elapsed_s` 为准，不再靠估算。
- **已知残留（未根治）**：采图服务的**连接池在 idle 后失效**，首次调用必然失败
  （`error_code=-1239510`），靠 patrol 侧站位级重试（第二次 `get_or_create` 重建连接）覆盖。
  每轮代价约一次失败重试。根治要改 vendored 的 `capture`（池内自愈 + 重试），本轮未做，
  记在 gap-list。
- `--no-sync` 时用 `_NullSync` 占位而不是"缺 transport"：前者是部署者的明确选择，
  后者是配置漏了——`SyncClient.flush` 在缺 transport 时抛 `NotImplementedError`，
  会让每轮都刷一条"同步失败"，把真正的告警淹掉。
- prod 的 Ubuntu 24.04 是 externally-managed，Python 依赖经**独立 venv + 离线 wheel** 安装
  (`/opt/mushroom-patrol/venv`)，不污染系统解释器。

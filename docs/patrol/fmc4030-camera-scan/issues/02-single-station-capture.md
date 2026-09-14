# 02 — 单站位采图闭环（运动→补光→截图服务采图→落盘）

**Spec:** ../spec.md §2.3、§4.2、§4.5、§5.3

**What to build:** 对站位表中一个站位执行完整拍照时序：两段速接近 → 到位 → 振动衰减延时 → OUT0 补光灯亮 → 调用 mushroom_cli 截图服务 `GET :7003/pool_capture` 采图（连接池、storage=cloud 直传 MinIO）→ 灭灯，图片与元数据（站位、坐标、时间戳、对象名）按命名规范落盘。相机为 XCloud 网络相机，**不直接集成相机 SDK**——本票的工作是截图服务的部署连通 + 锁定成像参数 + 与运动时序对接。端到端行为：一条命令拿到库房某一框的合规照片和配套元数据。

**Blocked by:** 00 — 仓库治理（capture 客户端落位 `patrol/`）；01 — Python 封装 FMC4030 SDK：连通、回零、点动

**Status:** ready-for-agent

- [ ] 截图服务在 Ubuntu 主机部署可用（xvfb-run 无头运行，单 worker，/healthz 通过）
- [ ] 一条命令完成指定站位"移动→补光→采图→灭灯"全流程
- [ ] 采图调用带超时与一次重试；失败返回明确错误（登录失败/设备不在线/截图失败分类）
- [ ] 照片清晰无运动模糊（振动衰减延时 + 补光窗口现场整定，300–800ms）
- [ ] 相机成像参数锁定：手动对焦、固定曝光/白平衡、固定分辨率（记录在站位表）
- [ ] 图片对象名与元数据符合命名规范（box_id/station_id/角度档/时间戳，MinIO 可回查）

## Comments

- 2026-08-30 代码完成（commit `171e53f`）：`patrol/stations.py`（站位表 YAML + 对象名规范）、`patrol/capture_client.py`（pool_capture 客户端 + 登录/离线/采图错误分类）、`patrol/orchestrator.py`（StationCapture 单站时序：goto→衰减→补光→采图（重试，对象名按秒避让）→灭灯→元数据），单测覆盖时序顺序与失败灭灯。
- **设计决策（安全基线）**：库内不内置网络调用，`CaptureClient(transport=...)` 由部署侧注入 HTTP 实现（截图服务与本机同机、URL 字面量 127.0.0.1:7003）；硬触发取舍定案为**不使用**（XCloud 走 SDK 软触发），OUT1 转备用。
- **待现场验收**：截图服务实机部署（xvfb/systemd）、相机成像参数锁定、运动模糊实测、MinIO 回查。

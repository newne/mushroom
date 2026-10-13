# 03 — TDengine REST `6041` 暴露面：不是只绑宿主，是对 LAN/VPN 开放

**归属：** 平台侧（`tools_taos` **不属于**我们的 compose 项目 `mushroom_service`，见 §2）——
本票由发版官开，**发版官不动它**。
**开票：** 发版官（2026-10-13 现场只读实测）
**Status:** ready-for-human
**优先级:** 中（安全面，不阻塞任何发布）

## 1. 实测（2026-10-13，10.77.77.39，只读）

| 端口 | 绑定 | 说明 |
| --- | --- | --- |
| **6041/tcp** | **`0.0.0.0:6041->6041` 且 `[::]:6041->6041`** | TDengine REST，**对整个 LAN 与 VPN 开放**（docker-proxy pid 5305 / 5311） |
| 6030、6035–6040 tcp/udp | `172.17.0.1:…` | 只绑 docker0 网关，外部不可达 |

```
docker ps --format "{{.Names}}|{{.Image}}|{{.Ports}}"
# tools_taos|tdengine/tdengine:latest|172.17.0.1:6030->6030/tcp, … 0.0.0.0:6041->6041/tcp, [::]:6041->6041/tcp
ss -lntp | grep :6041
# LISTEN 0 4096 0.0.0.0:6041 0.0.0.0:* users:(("docker-proxy",pid=5305,fd=7))
# LISTEN 0 4096 [::]:6041 [::]:* users:(("docker-proxy",pid=5311,fd=7))
docker inspect tools_taos -f '{{range .NetworkSettings.Ports}}{{.}}{{end}} {{.HostConfig.NetworkMode}}'
# […][{0.0.0.0 6041} {:: 6041}] tools_tool-network
```

## 2. 边界（重要，别越界）

- `tools_taos` 的 compose 项目是 **`tools_`**（网络 `tools_tool-network`），**不是我们的
  `mushroom_service`**。本票**只记录事实与建议，不做任何现场动作**：不改 compose、不改端口、
  不重启容器、不动防火墙。
- 我们的消费方是 `analysis.env.ingest`，容器里走 `http://172.17.0.1:6041`——**收紧成只绑
  docker0 不影响我们**（迁移方案票 01 §2 已按 `172.17.0.1:6041` 写死），但会**打断**任何
  直接连 `10.77.77.39:6041` 或 `192.168.1.250:6041` 的外部使用方，所以要由平台侧先摸清
  谁在用。

## 3. 建议（供平台侧判断）

1. 把 6041 的发布改成 `172.17.0.1:6041:6041`（与本容器其它端口一致），需要外部访问时再
   按来源收紧（VPN 网段白名单）；
2. 或至少确认 TDengine 的 `root` 口令不是默认值（`taosdata`）——本次**未取、未试**，只在
   票里点出风险；
3. 改之前先枚举消费者（老系统 / 其它服务 / 临时排障脚本）。

## 4. 与我们的关系

- 迁移票 01 的 env-ingest 用 `TDENGINE_URL=http://172.17.0.1:6041`，**不受本票结论影响**；
- 若平台侧决定收紧，我们的容器照常可达，**无需重新发版**。

## Comments

- 2026-10-13 发版官只读实测开票（plink 只读；无 pull/重启/改文件/试口令）。
  发现来源：核票 12 §6 第 5 条"6041 是否只在宿主监听"时顺带量到。
- Chief 2026-10-13 裁定：**开票，但别动 `tools_taos`**。

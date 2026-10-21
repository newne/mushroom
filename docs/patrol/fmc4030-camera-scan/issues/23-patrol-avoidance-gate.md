# 23 — 避让闸门：蓝光/加湿期起轮前延后、轮中原地暂停（各 ≤30 min）

**Spec:** `env-analysis/issues/14-humidity-hold-signal.md`（加湿信号）；
`env-analysis/issues/22-control-dimension-light-cycle-and-device-state.md`（白/蓝分路、设备运行状态）

**归属：** 判定与装配在 `deploy/**`（巡检手领地）；`patrol/**` 只加一条「拍之前可以 sleep」的缝；
`analysis/**` 的 `GET /env/humid` 是环境官票 14 的接口面，本票触发其实现。

**Status:** implemented（2026-10-21 已上生产，`--gate on` 实测在决策；§5 的两条待办未闭）

**What to build:** 巡检碰上**补蓝光**或**加湿期**时自动避让，不再靠人手工按 hold：

* 白光补光不影响，照常拍；
* 还没起轮 ⇒ 延后最多 30 min 再起；
* 已起轮 ⇒ 在下一站前原地暂停，最多等 30 min，条件解除后从当前站继续（本轮不中止）。

## 1. 用户口径（2026-10-21）

| 工况 | 处置 |
| --- | --- |
| 补白光 | 不影响，正常巡检 |
| 补蓝光（未起轮） | 延后 ≤30 min |
| 补蓝光（已起轮） | 原地暂停 ≤30 min，解除后继续 |
| 加湿 | 同上两条 |
| 信号取不到 / 判不了（太黑） | **fail-open**：不拦，留一行日志 |
| 30 min 到点仍未解除 | 按口径放行（继续拍），不再等 |

## 2. 落点

| 文件 | 内容 |
| --- | --- |
| `deploy/src/deploy/patrol_gate.py` | 新增。`blue_from_jpeg()`（B/R ≥3.0，平均亮度 <15 判不了）、`humid_from_api()`、`PatrolGate`（两个等待点共用 `_round_deadline`）、`build_gate()`（按 `--gate` 造闸门，缺前置则 `None` = 行为与从前一致） |
| `deploy/src/deploy/m1.py` | 新增 `--gate on|off`、`--gate-api`、`--gate-budget-s`（默认 1800）、`--gate-poll-s`（默认 60）；`main()` 里造一次 `gate`，`before_station=` 注入 daemon，`--once` 分支起轮前调 `wait_before_round()` |
| `patrol/src/patrol/round.py` | `PatrolRound` 加 `before_station(index, total, station, prev_meta)`，每站**拍之前**调一次；与既有 `should_stop`（整轮停）分工不同，它不中止本轮；顺带把上一站 `meta` 传下去（轮中省一次抓拍） |
| `patrol/src/patrol/daemon.py` | 只透传 `before_station` |
| `analysis/src/analysis/env/humidity.py` | 新增。读 TDengine `SUP_<前缀>MDHUM01/02` 最近一条 `Running`（0/1 运行态、2/3 停止）；>600 s 视为过期 ⇒ `null` |
| `analysis/src/analysis/api.py` | 新增 `GET /env/humid?room_id=`，异常一律返回 `humidifying: null`（不许把避让信号失败放大成巡检失败） |
| `docker/mushroom_solution.yml` | prod 巡检 `command` 追加 `--gate on` |

蓝光只能靠图像当传感器：台账里没有"灯现在亮没亮"的实测点（`OnOff1..4` 是使能位、
`Choose1..4` 只是白/蓝选择、`Model`/`Running` 是模式）。判据实测依据见
`imaging-quality/issues/01-camera-imaging-quality.md`：中性帧 B/R 0.94–1.00，蓝光帧 11–16。

## 3. 验收

- [x] `--gate off`（或缺 `room_id` / `--ingest` / 采图客户端）⇒ 不造闸门，日志一行「避让：关闭」
      或「拿不到…——关闭」，巡检行为与从前一致
- [x] 加湿信号：`Running ∈ {0,1}` ⇒ `humidifying: true`；两路都新鲜且都是 2/3 ⇒ `false`；
      全未知/全过期 ⇒ `null`（不当 `false`）
- [x] 起轮前延后读的是**每轮重新探**的信号，不是起轮时的一次快照
- [x] 轮中暂停用的是**上一站刚拍的那一帧**（`prev_meta.cloud_url`），取不回才现拍
- [x] 起轮前与轮中**共用**同一份 30 min 预算（口径「最多等 30 min 总」）
- [x] 到点放行有明确日志（「已延后 30 min —— 按口径开始巡检」/「本轮已暂停 30 min —— 继续」）
- [x] 急停优先：等待循环每轮先看 `abort`，急停时立刻退出等待
- [x] 信号坏掉不停巡检：探针抓拍失败、`/env/humid` 取不到都 fail-open 并留痕
- [ ] 得一次**真实**「条件解除，继续巡检」的证据（上线后 1 轮未出现，见 §5）

## 4. 生产记录（2026-10-21）

| 项 | 值 |
| --- | --- |
| 镜像 | `registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0-20261021114234-gate` |
| 部署 | `docs/patrol/prod-deploy` 的 rpush/rexec 跑 `.scratch/deploy_gate.sh`；compose 与 `.env` 备份为 `*.bak-20261021-120634` |
| 生效 | 12:20:04 起容器 argv 含 `--gate on`；`/healthz` ok；`/env/humid` 200 |
| 蓝光链路 | 容器内 `CaptureClient.capture` + `_fetch` + `blue_from_jpeg` 实测 `blue: True`（415931 B） |
| 首个完整轮次 | 12:50:10 起轮（12:20–12:50 因加湿中/蓝光延后满 30 min），48 站，12:57:59 结束 |

实测日志摘录：

```text
[2026-10-21 12:20:04] 避让：开启（蓝光/加湿；起轮前延后 ≤30 min，轮中原地暂停 ≤30 min；信号 http://172.17.0.1:8000/env/humid + 抓拍）
[2026-10-21 12:20:04] 避让：起轮前遇加湿中，延后（剩余 30.0 min）
[2026-10-21 12:22:10] 避让：起轮前遇蓝光，延后（剩余 27.9 min）
[2026-10-21 12:50:10] 避让：起轮前遇蓝光，已延后 30 min —— 按口径开始巡检
[2026-10-21 12:50:20] 避让：[1/48] S101 原地暂停（蓝光）
[2026-10-21 12:57:36] 避让：[48/48] S401 原地暂停（加湿中）
[2026-10-21 12:57:59] 本轮结束：停到第一个格点 S101（Y=187.2 Z=-40.0）
```

加湿信号真实性旁证（同日下午，TDengine 只读）：两路 `Running` 在 `0`/`2` 之间按
9–15 min（运行段）/ 5–8 min（停止段）翻转，13:57 时为 `2` ⇒ `/env/humid` 返回
`humidifying: false`、`age_s 45.2`。⇒ 该信号确实在跟踪实际启停，不是一直卡在运行态。

## 5. 上线后实测发现（未闭）

**A. 预算共用导致「整轮照拍」**
起轮前把 30 min 一次花光后，本轮 48 站全程处在蓝光/加湿中却不再等（`_round_deadline`
已过期）。这符合用户「最多等 30 min 总」的字面口径，但今天的实际效果是：这一轮
**完全没有避让**。是否改成「起轮前 / 轮中各 30 min 独立预算」，或「起轮前最多让 N 轮后
强制开拍」，需要用户拍板——**不擅自改口径**。

**B. 预算耗尽后每站仍记两条「原地暂停」**
`before_station()` 先记「原地暂停（reason）」再发现 `left <= 0`，于是 48 站各记两行
（实测 49 条「原地暂停」+ 48 条「已暂停 30 min —— 继续」），日志看起来像"一直在暂停"，
实际一秒没停；而且每站仍取帧重算一次 B/R。建议：`_round_deadline` 已过期时直接短路
（只记一行「本轮预算已用完，放弃避让」并跳过取帧），把判定成本也省掉。

## 6. 不做什么

* 不动 TDengine（只读 `Running`），不改台账语义
* 不用 `Running` 积分冒充「喷了多久」
* 不在 `patrol/**` 里做判定：patrol 只提供 `before_station` 缝，判定留在 deploy（与
  `should_stop` 同一分工）
* 不因信号故障停巡检（fail-open 是刻意的）

# 23 — 避让闸门：蓝光/加湿期起轮前延后、轮中原地暂停（各 ≤30 min）

**Spec:** `env-analysis/issues/14-humidity-hold-signal.md`（加湿信号）；
`env-analysis/issues/22-control-dimension-light-cycle-and-device-state.md`（白/蓝分路、设备运行状态）

**归属：** 判定与装配在 `deploy/**`（巡检手领地）；`patrol/**` 只加一条「拍之前可以 sleep」的缝；
`analysis/**` 的 `GET /env/humid` 是环境官票 14 的接口面，本票触发其实现。

**Status:** implemented（2026-10-21 上生产两版：v1 `0.1.0-20261021114234-gate` 实测暴露 §5-A/B，
v2 `0.1.0-20261021144609-fb58042` 已改口径并上线；仅剩"下一次真实触发"的现场印证）

**What to build:** 巡检碰上**补蓝光**或**加湿期**时自动避让，不再靠人手工按 hold：

* 白光补光不影响，照常拍；
* 还没起轮 ⇒ 延后最多 30 min 再起；
* 已起轮 ⇒ 在下一站前原地暂停，最多等 30 min，条件解除后从当前站继续（本轮不中止）。

## 1. 用户口径（2026-10-21，含当日下午修订）

| 工况 | 处置 |
| --- | --- |
| 补白光 | 不影响，正常巡检 |
| 补蓝光（未起轮） | 延后 ≤30 min |
| 补蓝光（已起轮） | 原地暂停 ≤30 min，解除后继续 |
| 加湿 | 同上两条 |
| 信号取不到 / 判不了（太黑） | **fail-open**：不拦，留一行日志 |
| 30 min 到点仍未解除 | 按口径放行（继续拍），不再等 |
| 预算怎么算 | **起轮前 30 min 与轮中 30 min 各自独立**（v1 共用一份 ⇒ 起轮前忍满后整轮等于没避让，见 §5-A） |
| 连续 2 轮都被拦 | 第 2 轮**不再等** ⇒ 回原点、本轮不拍（§5-A 修订） |

## 2. 落点

| 文件 | 内容 |
| --- | --- |
| `deploy/src/deploy/patrol_gate.py` | 新增。`blue_from_jpeg()`（B/R ≥3.0，平均亮度 <15 判不了）、`humid_from_api()`、`PatrolGate`（`_pre_deadline` / `_mid_deadline` **两个独立预算**，`blocked_rounds` 连续被拦计数 + 落盘）、`build_gate()`（按 `--gate` 造闸门，缺前置则 `None` = 行为与从前一致） |
| `deploy/src/deploy/m1.py` | 新增 `--gate on|off`、`--gate-api`、`--gate-budget-s`（默认 1800，起轮前/轮中各自一份）、`--gate-poll-s`（默认 60）、`--gate-max-blocked-rounds`（默认 2）、`--gate-state`（默认 `<outbox>.gate.json`）；`main()` 里造 `gate`，`before_station=` / `before_round=` 注入 daemon |
| `patrol/src/patrol/round.py` | `PatrolRound` 加 `before_station(index, total, station, prev_meta)`，每站**拍之前**调一次；与既有 `should_stop`（整轮停）分工不同，它不中止本轮；顺带把上一站 `meta` 传下去（轮中省一次抓拍） |
| `patrol/src/patrol/daemon.py` | 透传 `before_station`；新增 `before_round`（准入之后、连接之前调）与 `_park_home()`（放弃本轮时回原点：不拍摄、不建取证、不写 round 行） |
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
- [x] 起轮前与轮中**各自一份** 30 min 预算（v1 共用一份 ⇒ §5-A；v2 已改，单测
      `test_pre_round_and_mid_round_budgets_are_independent`）
- [x] 连续 2 轮等满仍未解除 ⇒ 第 2 轮**不等**、回原点跳过（单测
      `test_second_blocked_round_goes_home_without_waiting`；
      `patrol` 侧 `test_before_round_false_parks_home_without_any_round`）
- [x] 计数跨轮存活：落盘 `<outbox>.gate.json`，新实例读回（单测
      `test_blocked_counter_survives_a_new_gate_instance`）；解除时清零也落盘
- [x] 回原点不污染轮次：不拍摄、不建取证、不写 round 行、outbox 无新增
- [x] 轮中预算用完即**短路**：余下各站不再取帧、不再记「原地暂停」（单测
      `test_mid_round_budget_exhausted_short_circuits`，对应 §5-B）
- [x] 到点放行/放弃都有明确日志（「已延后 30 min —— 按口径开始巡检」/
      「本轮暂停预算已用完 —— 余下各站不再判」/「连续 N 轮遇…——回原点、本轮不巡检」）
- [x] 急停优先：等待循环每轮先看 `abort`，急停时立刻退出等待
- [x] 信号坏掉不停巡检：探针抓拍失败、`/env/humid` 取不到都 fail-open 并留痕
- [ ] 得一次**真实**「条件解除，继续巡检」的证据（v1 那轮未出现）
- [ ] v2 的口径要等**下一次真实触发**才看得到（`patrol-serve` 是每 3 小时一次的触发驱动，
      容器重启后空闲等触发，日志里还没有「避让：开启（…两个预算独立…）」那一行）

## 4. 生产记录（2026-10-21）

| 项 | v1（共用预算） | v2（预算独立 + 连续 2 轮回原点 + 计数落盘） |
| --- | --- | --- |
| 镜像 | `0.1.0-20261021114234-gate` | `0.1.0-20261021144609-fb58042`（提交 `fb58042`） |
| 部署 | `.scratch/deploy_gate.sh`；compose 与 `.env` 备份 `*.bak-20261021-120634` | `.scratch/deploy_gate2.sh`（只换 `PATROL_IMAGE`，compose 里的 `--gate on` 已校验在位）；`.env` 备份 `*.bak-20261021-144019`、`*.bak-20261021-144749` |
| 生效 | 12:20:04 起 argv 含 `--gate on`；`/healthz` ok；`/env/humid` 200 | 14:48 起 `mushroom_patrol` / `mushroom_analysis` 均 `Up (healthy)`；容器内 `deploy.patrol_gate` 含 `default_state_path`/`load_blocked_rounds`/`save_blocked_rounds`，`DEFAULT_MAX_BLOCKED_ROUNDS=2`，状态文件推到 `/app/data/outbox.gate.json`（尚未生成，说明还没被拦） |
| 蓝光链路 | 容器内 `CaptureClient.capture` + `_fetch` + `blue_from_jpeg` 实测 `blue: True`（415931 B） | 同镜像同代码，未重跑（链路未变） |
| 首个完整轮次 | 12:50:10 起轮（12:20–12:50 因加湿中/蓝光延后满 30 min），48 站，12:57:59 结束 | 尚未发生（等下一次触发） |

单测（v2，本地 `.venv-win`）：`deploy/tests` **363 passed**、`patrol/tests` **442 passed, 2 skipped**。

v1 实测日志摘录（v2 的日志要等下一次触发才出现）：

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

## 5. 上线后实测发现（v1 暴露 → v2 已改）

**A. 预算共用导致「整轮照拍」⇒ 已改（用户 2026-10-21 下午拍板）**
起轮前把 30 min 一次花光后，本轮 48 站全程处在蓝光/加湿中却不再等（`_round_deadline`
已过期）。实测效果是这一轮**完全没有避让**。新口径：

* 起轮前 30 min 与轮中 30 min **各自独立**（`_pre_deadline` / `_mid_deadline`）；
* **连续 2 轮**等满仍未解除 ⇒ 第 2 轮不等、**回原点**跳过该轮（`daemon._park_home`，
  不拍摄、不写 round 行）；
* 计数必须落盘：`patrol-serve` 每轮都是新的一次 `m1.main()`，内存计数活不过一轮 ⇒
  默认写 `<outbox>.gate.json`（`--gate-state` 可覆盖），信号解除时清零也写回。

**B. 预算耗尽后每站仍记两条「原地暂停」⇒ 已改**
v1 的 `before_station()` 先记「原地暂停（reason）」再发现 `left <= 0`，48 站各记两行
（实测 49 条「原地暂停」+ 48 条「已暂停 30 min —— 继续」），日志看着像"一直在暂停"，
实际一秒没停，而且每站仍取帧重算一次 B/R。v2 改为：预算过期即 `_mid_done` 短路，
只记一行「轮中暂停预算已用完 —— 余下各站不再判」，后续各站直接返回。

**C. 待现场印证（未闭）**
v2 的运行时效果还没在真实触发里出现：`patrol-serve` 空闲等算法侧触发（每约 3 小时一次），
容器重启后不会有轮次日志，所以「避让：开启（…两个预算独立…；已拦 N 轮，计数
/app/data/outbox.gate.json）」这一行要等下一次触发。下一步只看三件事：
起轮前预算是否独立计时、轮中预算耗尽后是否只剩一行、连续两轮被拦时是否回原点且
outbox/rounds 无新增。

## 6. 不做什么

* 不动 TDengine（只读 `Running`），不改台账语义
* 不用 `Running` 积分冒充「喷了多久」
* 不在 `patrol/**` 里做判定：patrol 只提供 `before_station` 缝，判定留在 deploy（与
  `should_stop` 同一分工）
* 不因信号故障停巡检（fail-open 是刻意的）

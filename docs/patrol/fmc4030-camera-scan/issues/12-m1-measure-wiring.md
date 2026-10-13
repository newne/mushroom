# 11 — M1 装配：把 measure_fn 接进 PatrolDaemon（生产者接线落地）

**Spec:** ../spec.md §5；`env-analysis/issues/11-measurements-contract.md`（线格式）；
ADR-0024（标定与四量口径）

**归属：** `deploy/**` 由巡检手实现（`deploy/src/deploy/m1.py` 是他的领地）。
生产者的库（`measure/**`）已由测量师落好并有 75 项单测。

**Blocked by:** 无（测量师侧已可被调用）；建议在环境官 `measurements` 加列迁移之后上线，
否则 `room_id`/`station_id`/`angle_profile` 会在 `/ingest` 被静默丢。

**Status:** ready-for-agent

**What to build:** 生产装配里传 `measure_fn`。今天 `deploy/src/deploy/m1.py:359-374`
构造 `PatrolDaemon(...)` 时**没传** `measure_fn`，而 `patrol/src/patrol/daemon.py:78`
默认 `None`、`:251` 判空才写 `kind=measurement` 行 ⇒ 全仓只有测试传过 stub，
生产永远 0 行。这是"`analysis.measurements` 0 行"的**唯一根因**。

## 落点（精确到行）

`deploy/src/deploy/m1.py`：

1. **导入**（与现有 `from deploy.framing_wire import build as build_framing` 并列）：

   ```python
   from deploy.measure_wire import build as build_measure
   ```

2. **装配处**（`PatrolDaemon(` 调用，当前 359-374 行）加一个参数：

   ```python
   measure_fn=measure_fn,
   ```

   其中 `measure_fn` 在 `PatrolDaemon` 之前构造一次（**不要**每轮重建）：

   ```python
   measure_fn, measure_reason = build_measure(args, room_id=room_id, log=log)
   log(measure_reason)
   ```

3. **`room_id` 的来源**：与巡检察门禁同源（`room.yaml` / `load_room_state`）。
   测量师的 `make_measure_fn(room_id=…)` 里 `room_id` 必填且为空时抛
   `ValueError`——这是刻意的：缺库房的测量行会污染整张区间表（票 11 §1）。
   如果 `room_id` 取不到，**不要启动常驻进程**（fail-closed），并在日志里说明原因。

## 新建 `deploy/src/deploy/measure_wire.py`（照 `framing_wire.py` 的形状写）

`framing_wire.build()` 已经把三件事做对了，直接复用它的模式与实现：

- **取图**：`framing_wire._fetch(cloud_url)`（httpx，内网回环）；
- **解码**：`framing_wire._imread(bytes)`（Pillow → numpy RGB，缺 Pillow 明确拒绝）；
- **拒绝开关**：缺 `--mm-per-px` 时**不接测量**并打印缺什么，而不是静默不生效。

`measure_wire.build(args, *, room_id, log) -> (measure_fn | None, reason)` 的判定顺序：

| 条件 | 结果 |
| --- | --- |
| `--measure off` | `(None, "测量：关闭")` |
| 缺 `--mm-per-px` | `(None, "测量：开了 --measure 但缺 --mm-per-px 标定换算，拒绝开启")` |
| `room_id` 为空 | `(None, "测量：拿不到 room_id（room.yaml），拒绝开启")` |
| 缺 numpy/Pillow | `(None, "测量：解不了图（{err}）")` |
| 否则 | `(make_measure_fn(...), "测量：开启（{mm_per_px} mm/px，房间 {room_id}）")` |

参数（`deploy/src/deploy/m1.py` 的 argparse）：

```python
p.add_argument("--measure", choices=["on", "off"], default="off",
               help="测量接线：on=每轮对每帧跑 baseline 检出并写 measurements 行")
p.add_argument("--measure-blur-threshold", type=float, default=100.0,
               help="模糊门阈值（真机帧 S306 实测 lapvar=70.8 < 100 ⇒ 会拒真机帧；"
                    "阈值标定前的临时旋钮，见票 11 Comments）")
p.add_argument("--measure-emit-empty", action="store_true",
               help="失败帧也产一行 n=0 + no_detection（默认不产行）")
```

**默认 `off`**：这是改生产行为，默认不开启；现场确认标定与阈值后显式打开。

## `patrol/**` 侧需要动的地方（巡检手领地，测量师不改）

**不需要改 `daemon.py`**：`measure_fn(report) -> list[ToRow]` 的缝已经在
`daemon.py:251-255`，行会带 `kind="measurement"`，与 `image_index` 行同路径进 outbox。
`RoundReport`（`patrol/src/patrol/round.py:46`）**没有 room_id 字段**，所以 `room_id`
必须在装配时固定（这也是 `make_measure_fn` 要求必传的原因）。

## 验收

- [ ] `--measure on --mm-per-px <值>` 跑一轮（真机或回放），outbox 出现
      `kind="measurement"` 行，字段与 `measure.record.RECORD_FIELDS` 逐字一致
- [ ] 缺 `--mm-per-px` 时日志明确拒绝，且巡检本身照常跑完（测量起不来不拦巡检）
- [ ] `GET /measurements`（analysis）出现非零行，每行带 `quality`（票 11 §7 验收）
- [ ] 失败帧不产行；`--measure-emit-empty` 时才产 `n=0` 行

## Comments

- 2026-10-13 写票（测量师）。生产者已就绪：`measure/src/measure/production.py` 的
  `make_measure_fn(room_id, mm_per_px, detector=None, fetch, decode, quality_gate)`；
  真机帧 S306 走完整链路产出的行样例与环境发现（`lapvar=70.8 < 100` 默认门拒真机帧）
  已贴在 `env-analysis/issues/11-measurements-contract.md` 的 Comments。
  单测证据：`measure/tests/` 75 passed（`test_production.py` 覆盖报告对象调用、
  失败帧不产行、两库房不撞键、缺测 NULL）。

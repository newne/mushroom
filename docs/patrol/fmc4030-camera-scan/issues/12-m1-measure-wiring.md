# 11 — M1 装配：把 measure_fn 接进 PatrolDaemon（生产者接线落地）

**Spec:** ../spec.md §5；`env-analysis/issues/11-measurements-contract.md`（线格式）；
ADR-0024（标定与四量口径）

**归属：** `deploy/**` 由巡检手实现（`deploy/src/deploy/m1.py` 是他的领地）。
生产者的库（`measure/**`）已由测量师落好并有 75 项单测。

**Blocked by:** 无（测量师侧已可被调用）；建议在环境官 `measurements` 加列迁移之后上线，
否则 `room_id`/`station_id`/`angle_profile` 会在 `/ingest` 被静默丢。

**Status:** implemented（2026-10-20：prod 巡检已开 `--measure on`，`GET /measurements` 48 行/轮；
值仍为 `n=0/no_detection`，见 Comments 末条与票 15 §17.2）

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

## 开启清单（**开启 `--measure` 的那次装配必须整组显式给出**）

Chief 2026-10-13 裁：代码默认保持**保守**（`--measure off`、`--measure-emit-empty` 关、
`--measure-blur-threshold` 缺省走标定默认），**留痕不靠默认值**，靠装配时显式给：

| 参数 | 缺省行为 | 开启时必须 |
| --- | --- | --- |
| `--measure` | `off`（不产任何测量行） | 显式 `on` |
| `--mm-per-px` | `None` ⇒ **不拒绝**：用菌包标准尺寸当帧内标尺（`bag_standard`，像素/估算档） | 一期**不传**（2026-10-20 改，票 15 §17.2）；有站位标定值再传 |
| `--room` | `DEFAULT_ROOM_PATH`（room.yaml 路径） | 确认指向本库房；`room_id` 由它**派生** |
| `--measure-emit-empty` | 关（失败帧不产行） | **显式带上**，否则被拒帧在库里毫无痕迹 |
| `--measure-blur-threshold` | `None` ⇒ 走 `measure/quality.py` 的标定默认 | 一般不传；要覆盖才传 |

⚠️ **参数名以代码为准**：`m1.py` 里是 **`--room`（room.yaml 路径）**，**没有 `--room-id`**；
`room_id` 从该文件派生（与巡检察门禁同源）。谁在票里写 `--room-id`，以本节的实际参数名为准。

**开启日复核（归发版官）**：从**镜像/运行态核 argv**（不是看票），确认实际进程带齐上表；
`--help` 文案与代码保持一致。

## 验收

- [ ] `--measure on --mm-per-px <值>` 跑一轮（真机或回放），outbox 出现
      `kind="measurement"` 行，字段与 `measure.record.RECORD_FIELDS` 逐字一致
- [ ] 缺 `--mm-per-px` 时日志明确拒绝，且巡检本身照常跑完（测量起不来不拦巡检）
- [ ] `GET /measurements`（analysis）出现非零行，每行带 `quality`（票 11 §7 验收）
- [ ] **不给 `--measure-emit-empty` ⇒ 失败帧不产空行**；
      **显式给了 ⇒ 失败帧产一行 `n=0` + 原因标记**（菌簇四列仍 NULL）
- [ ] `--measure-blur-threshold`:**不给 ⇒ 走标定默认**；**给了 ⇒ 覆盖生效**
- [ ] 开启日由发版官从镜像/运行态核 argv，确认上表五条实际生效

## Comments

- 2026-10-13 巡检手：**装配接线已落地**（commit 见仓库日志，`feat(deploy): 测量接线落装配…`）。
  - 新增 `deploy/src/deploy/measure_wire.py`：`build(args, *, room_id, fetch, decode, log)
    -> MeasureWire(fn, reason)`，判定顺序与 §表一致；取图/解码复用 `framing_wire._fetch` /
    `_imread`（同一条已验证的路）；`room_id` 为空或 `"unset"`（`patrol.room` 的占位值）
    一并视为"没有库房号"。
  - `m1.py`：新增三个参数（`--measure` 默认 **off**、`--measure-blur-threshold` 默认 100.0、
    `--measure-emit-empty`）；装配处 `measure_fn=measure_wire.fn`（**装配时造一次**，
    不每轮重建；`room_id` 与准入门禁同源）；`--check` 也能看到"测量：开启/拒绝"的那行日志。
  - **fail-closed**：`--measure on` 但 room.yaml 里没有可用 `room_id` ⇒ **拒绝启动（返回 2）**
    并打印原因（缺库房的行会拿 `unset` 当幂等键，污染区间表）；缺 `--mm-per-px` 或解码器
    只拒绝**测量**，巡检照常跑完。
  - 测试：新增 `deploy/tests/test_measure_wire.py` 11 项（默认关、缺换算/缺房间/缺解码器各自
    的拒绝文案、开启后产出 `kind=measurement` 行、模糊门旋钮真的进质量门、**真 JPEG 走默认
    解码器**产出行、CLI 默认与参数）；`deploy/tests` 全绿 **276 passed**。
- 2026-10-13 巡检手：**未完成的验收**（需要真机/线上，不是本机可证）：
  - §验收 1「跑一轮，outbox 出现 `kind=measurement` 行」——本机只到"接线 + 回放帧"这一层
    （daemon 侧行路径已有 `patrol/tests/test_daemon_sync.py::test_daemon_measure_fn_rows_synced`），
    真机一轮请现场/发版官按 `--measure on --mm-per-px <值>` 跑；
  - §验收 3「`GET /measurements` 非零行」——线上；
  - 阈值：真机帧 S306 实测 `lapvar=70.8 < 100` ⇒ 默认门会拒真机帧，现场必须先定这个值
    （临时旋钮，不静默改默认）。
- 另：step code 独占文件里的两条口径残留（`patrol/src/patrol/framing.py:67`、
  `capture_client.py:70`）**不归我改**，已在票 10 §2.4 分派给他。

- 2026-10-13 写票（测量师）。生产者已就绪：`measure/src/measure/production.py` 的
  `make_measure_fn(room_id, mm_per_px, detector=None, fetch, decode, quality_gate)`；
  真机帧 S306 走完整链路产出的行样例与环境发现（`lapvar=70.8 < 100` 默认门拒真机帧）
  已贴在 `env-analysis/issues/11-measurements-contract.md` 的 Comments。
  单测证据：`measure/tests/` 75 passed（`test_production.py` 覆盖报告对象调用、
  失败帧不产行、两库房不撞键、缺测 NULL）。

- 2026-10-13 补「开启清单」与验收改写（Chief）：发版官在运行态抓到"票里建议开 `emit_empty`
  而镜像里 `--measure-emit-empty` 是 `store_true` 无 `default=True` ⇒ 实际关着"。
  裁定：**不改代码默认**（保守），留痕由**装配侧显式传参**实现；开启那次装配必须整组给出
  `--measure on` / `--mm-per-px` / `--room` / `--measure-emit-empty`，并由发版官**从镜像/运行态核 argv**。
  同条记下：票里常见写法 `--room-id` 在 `m1.py` **不存在**（实际是 `--room` 指向 room.yaml，`room_id` 派生）。

- 2026-10-20 巡检手：**prod 巡检已开测量**（本轮任务：measure 接入 prod）。
  - 运行态 argv（`/proc/1/cmdline` 实读）：
    `python3 -m deploy.patrol_serve … --measure on --measure-emit-empty`
    （镜像 `mushroom_patrol:0.1.0-20261020194913-measurewire`，`room_id=611`）。
  - 一轮证据（20:01:29）：`测量：48 帧 共 26.32 s（均 0.55 s/帧）`；outbox 同步 **146 条**
    （含 19:35 失败轮遗留 49 条 + 本轮 97 条）；`analysis.measurements` **48 行**，
    ts `19:53:25–20:00:40`，`GET /measurements?room_id=611` 非零。
  - **阻塞项 1（镜像）**：`pillow` 只在 workspace 的 dev 组，`uv sync --no-dev` 的巡检镜像里没有 PIL
    ⇒ 容器里 `--measure on` 只会以"解不了图"拒绝开启。已把 `pillow>=12.2` 落进
    `deploy` 的运行时依赖（解码实现 `deploy.framing_wire._imread` 本就在 deploy 侧），
    `uv.lock` 只多两行。
  - **阻塞项 2（缝形状）**：`measure.production.make_measure_fn` 吐**dict**，而
    `patrol.daemon` 的缝是 `ToRow`（只调 `to_row()`）⇒ 第一次开启时轮末抛
    `AttributeError: 'dict' object has no attribute 'to_row'`，整轮不落库、不 flush
    （19:35 那轮）。已在装配处加 `WireRow` 包装做形状转换（不改任何一侧契约），
    并加 `test_rows_satisfy_the_daemon_to_row_seam` 钉住；19:53 轮起正常。
  - `--mm-per-px` 改为**可选**（不给走 `bag_standard`），开启日志打清标尺来源，见票 15 §17.2。
  - ⚠️ **行有、值没有**：48/48 行 `n=0`、`scale_source=none`、菌簇四列 NULL，
    quality 全为 `no_detection+no_scale+no_theta+height_uncorrected+…`。
    本地用两帧真机图跑 `L3Detector` 复核（10-11 样本 / 10-20 帧）：袋 27/2、果体候选 51/35、
    `filter_above_bag_top` **kept=0**（51/51 判为 `inside_bag_body`）⇒ 不是接线丢行，
    是 L3 的袋顶过滤/当前取景下没有"袋面以上的簇"。归算法侧（票 15 §17、票 18 真机对照），
    修好后无需再动装配。

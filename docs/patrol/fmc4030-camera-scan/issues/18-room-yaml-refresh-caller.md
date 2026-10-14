# 18 — 给 `room.yaml` 一个调用者：每轮开始前刷新准入台账（**先规划，不写码**）

**用户红线（2026-10-14）:** 「不要直接实现，首先规划实现方案」⇒ 本票只出方案。
**Chief 派单（2026-10-14）:** 最小形态五条（每轮入口拉一次 / 批次变了原子更新+打印旧→新 /
端点不可用或 `stale` ⇒ 保留旧文件继续跑但每轮告警+计数 / 文件缺失不可读 ⇒ 不动 / 不新增 service、
不依赖宿主 timer 与 cron），验收＝两条假路径用例 + 真机一轮日志。
**前置票:** 票 17（`fetch_room` 改走 `GET {PATROL_ANALYSIS}/batches`，已实现 `f93133a`）；
端点要在 **analysis 下次重建**后生产生效并验通，**实现排在那之后**。

**Status:** **已实现**（Chief 2026-10-14 15:45 批"开始写码"，属取数/调度线，不占量测线"先规划"红线）。
落地见 Comments；部署由运维执行（代码先落地 → 重建 patrol → 重建 console）。

---

## 1. 缺口与根因（运维只读事实 + 我读代码复核）

| 事实 | 证据 |
| --- | --- |
| **没有任何调度器**在跑 `deploy-fetch-room` | 宿主无 fetch 相关 systemd timer；`crontab -l` = `no crontab for root`；`/etc/cron.d|daily|hourly` 无相关文件 |
| patrol 容器日志里 `fetch-room\|room.yaml` **零命中**（10-13 13:02 启动至今） | `docker logs mushroom_patrol` |
| `room.yaml` 停在 **2026-10-06 21:01:10**（683 B）：`room_id 611` / `entry_date 2026-09-30` / `packages 9792` / `batch_no mogu-107` / `source: … mo_gu_batch id=107 …`（业务库，已禁用） | `stat` + `cat` |
| **根因：`patrol` role 不调用 fetch-room**，只有显式 `fetch-room` role 才跑，而**没有任何东西用那个 role** | `docker/patrol-entrypoint.sh:18-28`（patrol 只起 `deploy.m1`）、`:29-31`（fetch-room role） |

⇒ 不是"跑了但失败"，是**没人调**。所以准入依据一直是 10-06 手写的那份，**跨了两个批次都没人发现**。

## 2. 方案（最小形态）

**挂在"每轮开始前"，挂在常驻执行方 `patrol-serve` 的轮次入口**（`deploy/patrol_serve.py:242`
`run_once=lambda: m1_main([*m1_args, "--once"])`）：把 `run_once` 包一层——
**先刷新 `room.yaml`，再起这一轮**。理由：

* `patrol-serve` 是**每轮都走一遍启动流程**的那一个进程（站位表也是每轮重读），且**单实例**（持有控制器）；
* 复用票 17 已有的取数与渲染函数（`fetch_ledger_rows` / `select_batch` / `render_room_yaml` /
  `write_room_yaml`）⇒ **不新写第二套取数**，也不新增进程；
* **不新增 service、不用宿主 timer、不用 cron**（Chief 第 5 条）。

**每轮的顺序**：`refresh_room_file()` → `m1_main --once`（m1/daemon 每轮现读 `room.yaml` 重新判准入，
`m1.py:285`/`:424` 已经是这个语义，不用改）。

### 2.1 一个必须先解决的部署前提：**现在没有可写路径**

巡检容器把配置目录**只读**挂进去：`../mushroom_patrol/configs:/app/configs:ro`
（`docker/mushroom_solution.yml:196`）；两个角色读的都是 `PATROL_ROOM` 默认 `/app/configs/room.yaml`
（`docker/patrol-entrypoint.sh:21`、`:45`）。**容器里写不了那个路径** ⇒ 刷新必然失败。

**建议（要运维改 compose，属部署变更）**：
* `room.yaml` 搬到**巡检容器可写、console 可读**的位置：`/app/data/room.yaml`
  （宿主 `mushroom_patrol/data/room.yaml`；巡检容器 `data:rw`、console 容器 `data:ro`）；
* ⚠️ 运维 2026-10-14 只读核实：**`data/room.yaml` 已经存在，且与 `configs/room.yaml` 逐字节相同**
  （sha256 `7f1641e8…`、683 B、10-06 21:01）⇒ **不需要 `cp` 迁移**，只设环境变量；
* 两个角色都设 `PATROL_ROOM=/app/data/room.yaml`（compose `environment`，**不靠代码默认值**），
  并给巡检容器设 `PATROL_ROOM_ID=611`（库房号；不设则退回读 room.yaml 里的 `room_id`，`unset` 视为没有）；
* console 侧（界面）同样指到新路径，否则页面显示的还是旧文件；
* **顺序**：代码先落地 → 重建 `mushroom_patrol` → 重建 `mushroom_console`。

### 2.2 每条路径的行为（Chief 五条 + 落地细节）

| # | 情形 | 动作 | 日志 | 写文件？ | 继续跑这一轮？ |
| --- | --- | --- | --- | --- | --- |
| 1 | 正常、`entry_date` **未变** | 无操作 | 一行 `room-refresh ok（entry_date 未变）` | 否（幂等，不产生 mtime 抖动） | 是 |
| 2 | 正常、`entry_date` **变了** | **原子更新**（既有 `os.replace`）+ 旧→新 | `room-refresh 批次变更：2026-09-30 → 2026-10-08` | **是** | 是 |
| 3 | 端点不可用（超时/5xx/结构错） | **保留旧文件**，用最后已知批次继续 | **醒目告警 + 累计计数**：`！room-refresh 失败第 N 次（原因…）；沿用最后已知批次 entry_date=…` | 否 | **是** |
| 4 | `stale=true`（台账陈旧） | 同上（保留旧文件、继续） | 同上，原因写"台账陈旧" | 否 | **是** |
| 5 | `entry_date` 在未来（数据可疑） | **不写**（既有 fail-closed），保留旧文件 | 告警 + 计数 | 否 | **是**（沿用旧值） |
| 6 | 文件**缺失/不可读** | 见 §5 待裁（默认：端点可用 ⇒ 写一份；端点不可用 ⇒ 不写） | 按情形 | 视情形 | 是（准入判定仍归 `patrol.room`） |

**为什么"失败也继续跑"**（Chief 的理由，照录）：停轮会丢掉用户要的**连续影像数据**；
而"批次刚换 + 端点恰好不可用"这个窗口，会被**下一次成功轮询在 ≤1 个轮间隔内**抓到。
⇒ 失败是**告警 + 计数**，不是停轮；**但绝不静默**（"没日志"不许被读成"没差异"）。

### 2.3 计数与告警形态

* `patrol-serve` **进程内**计数：`刷新失败累计 N 次` + `最后一次成功刷新 <时间>`，每轮打一行；
* 成功轮打一行（含 `entry_date` 与是否变更）；
* **不新增持久化字段**（要落库/上页面属环境官/界面师，另议）。

## 3. 变更清单（实现时）

| # | 位置 | 改什么 |
| --- | --- | --- |
| 1 | `deploy/src/deploy/fetch_room.py` | 新增 `refresh_room_file(room_id, path, *, url=None, runner=None, log=print) -> dict`：复用现有取数/判定/渲染/落盘，**返回结构化结果**（`{"changed": bool, "entry_date": …, "error": str|None}`）供调用方计数与告警；`main()` 行为不变 |
| 2 | `deploy/src/deploy/patrol_serve.py` | `run_once` 包一层：先刷新（失败只告警不抛）、再 `m1_main([... "--once"])`；`--room`/`PATROL_ANALYSIS` 透传；计数与"最后一次成功刷新"打进日志 |
| 3 | `deploy/tests/` | 两条假路径用例（批次变更 / 端点不可用+stale）+ 计数与告警断言 + "失败不停轮"断言 |
| 4 | 部署（运维） | `PATROL_ROOM=/app/data/room.yaml`（两个角色）+ 文件迁移（§2.1）；`PATROL_ANALYSIS` 显式设置 |

**不动**：`patrol/**`（准入判定与 daemon 语义不变）、`analysis/**`、`console.py`（跨界）。

## 4. 验收（Chief 给的两条 + 真机）

* **单测（已实现）**：① **伪造批次变更** ⇒ 文件被原子更新、`previous_entry_date`/`entry_date` 与
  "旧→新"都在返回值里；② **伪造端点不可用 / `stale=true` / 未来日期** ⇒ 文件**字节不变**，
  返回 `error`；③ **未变** ⇒ **不写**（连 `st_mtime_ns` 都不动）；④ **缺文件 + 端点可用 ⇒ 写一份**；
  ⑤ **缺文件 + 端点不可用 ⇒ 不写**（daemon 读不到 ⇒ 按既有契约不动机构）；
  ⑥ 轮次入口：**刷新失败本轮照起**（`m1_main` 一定被调用）+ 累计计数 + 成功清零；
  ⑦ **反向自证**：把"失败即抛错/提前 return"改回去 ⇒ ⑥ 立刻红（用例里钉住的就是"照起"）。
* 命令：`pytest -q ../patrol/tests ../deploy/tests` → **778 passed**；`ruff check` 干净。
* 真机（运维，重建后）：跑一轮 → 日志里有 `room-refresh` 行；`stat` 显示 `room.yaml` mtime 更新；
  端点不可用时看到告警行与累计计数。

## 5. 待裁（Chief）

1. **文件缺失/不可读时怎么办**——你的第 4 条写"不动（既有契约不变）"。我读成两种可能，请点一个：
   (a) **完全不写**（缺文件就继续不巡检，直到人工放一份）；(b) 端点可用 ⇒ 写一份（首次接入就是这样来的），
   端点不可用 ⇒ 不写、按既有契约不巡检。**我建议 (b)**：刷新只是把权威值落到本地，
   不改变"准入判定归 `patrol.room`"这条契约；但**坏文件**（不可解析）沿用现有行为
   （`write_room_yaml` 会打印"旧文件不可用"再覆盖），这条要不要改也请一并定。
2. **`room.yaml` 搬家**（§2.1）要不要做、走 `/app/data/room.yaml` 是否可接受（它同时影响 console 读取路径）。
3. **`packages` 从 `9792` 变 NULL**：新来源没有这个量 ⇒ 刷新后页面要**如实显示"未知"**，
   **不许回 0**（属前端/后端显示口径，见 §6）。

## 6. 下游影响

| 影响 | 谁 |
| --- | --- |
| 刷新后 `packages: 9792 → null`：页面若显示它，必须显示"未知"，**不许回 0** | 前端 / 后端 |
| `batch_no` 从 `mogu-107` 变派生名 `611_20260930`，业务名移到 `display_name` | 后端（列语义）、前端（展示与筛选）、运维（线上复验） |
| 批次切换（`entry_date` 变）＝数据口径分段点，分析侧本来就按 `(room_id, entry_date)` 分段 | 后端/算法（无需新动作，仅确认） |

## 7. 不做

* 不新增 service / 不依赖宿主 timer / 不依赖 cron（Chief 第 5 条）；
* 不在容器启动时"顺便"实现正确性（可选加一次调用，但**不靠它**）；
* 不新写取数（一律走票 17 的只读端点）；
* 不在刷新里改准入判定（`patrol.room` 是唯一判据来源）。

## Comments

- 2026-10-14 测试（原巡检手）：**已实现**（Chief 批示后）。落点：
  `deploy/fetch_room.py::refresh_room_file`（取数→需要才写；**永不抛**；返回结构化结果）、
  `deploy/patrol_serve.py`：`--room-id`/`PATROL_ROOM_ID` + `--no-room-refresh`（排障旁路）+
  `RefreshStats` + `refresh_before_round` + `make_run_once`（轮次入口先刷新再起轮）、
  `patrol/room.py`：`RoomState.display_name` + `batch_label`（业务名优先、缺则派生名）+
  `ensure_room_file` 模板补两行、`patrol/daemon.py::_room_fields` 改用 `batch_label`。
  **踩坑记录**：加 `--room-id` 后 `--room` 被 argparse 前缀缩写吃掉 ⇒ 两条既有透传用例当场红；
  已用 `allow_abbrev=False` 关掉并加用例钉死（这是"新开关别抢老参数"的教科书坑）。
  验收：`pytest -q ../patrol/tests ../deploy/tests` → **778 passed**；ruff 干净。
- 2026-10-14 测试（原巡检手）：立项（**只写方案、零代码**）。根因复核：`patrol` role 不调用 fetch-room
  （`docker/patrol-entrypoint.sh:18-28`），fetch-room role 无人使用（`:29-31`）。
  另发现一条必须先解决的部署前提：**`/app/configs` 在容器里是只读挂载**，
  刷新要落到可写路径（建议 `/app/data/room.yaml`，§2.1）——这条要运维改 compose，属部署变更。

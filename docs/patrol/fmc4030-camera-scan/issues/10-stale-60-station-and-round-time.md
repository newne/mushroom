# 10 — 清掉"60 站时代"残留与轮时长口径（48 站 / 实测 ≈7 min 40 s）

**依据:** 发版官 2026-10-13 生产只读盘实测（`docs/agents/bot-roles.md:78-79`）：
现役 **48 站**（`configs/stations.yaml` 48 条、`/api/grid` 12×4、最近三轮 48/48、0 失败）；
单轮实测 **470 / 462 / 457 s（≈7 min 40 s）**。

**Status:** ready-for-agent（**巡检手牵头**；跨域按归属分派，不代改）

---

## 1. 两条口径（钉死）

| 项 | 正确值 | 依据 |
| --- | --- | --- |
| 现役站位数 | **48**（12 列 × 4 层） | 生产 `stations.yaml` 48 条 + `/api/grid` 12×4 + 最近三轮 48/48 |
| 单轮时长 | **≈7 min 40 s**（470/462/457 s） | 同上三次轮次实测，0 失败 |

- 凡**口径性陈述**里的 "60 站 / 60 张 / 6–7 min / 11 min" 一律改成上表值。
- **例外：不动历史证据**——当时的实测日志摘录、ADR 正文、历史样例图像名保持原样（改了就是篡改记录）。

## 2. 待改清单（按归属，**只列不动**）

### 2.1 巡检手（`patrol/**` + `deploy/**` 除 `console.py` / `framing_wire.py` / `preview.py`）

| 文件:行 | 现状 | 动作 |
| --- | --- | --- |
| `deploy/src/deploy/m1.py:16` | "一轮实测约 6–7 min（单次采图 5.2 s × 60 站位…）" | 6–7 min → ≈7 min 40 s；60 → 48 |
| `m1.py:232` | "60 站各多拍一张 ≈ 每轮多约 10 分钟" | 60 → 48；按 5.2 s/张重算 ≈4.2 min |
| `m1.py:403` | 日志 "单轮约 6–7 min" | → ≈7 min 40 s |
| `deploy/src/deploy/patrol_serve.py:7` | "那一轮 60 张图就废了" | → 48 张 |
| `patrol_serve.py:170` | "拍了 60 张但一张都没进 outbox" | → 48 张 |
| `deploy/src/deploy/manual.py:7` | "踢掉，60 张图作废" | → 48 张 |
| `deploy/src/deploy/fetch_room.py:6` | "60 张错照片进测量表" | → 48 |
| `deploy/README.md:119` | "下限 15 min（单轮约 6–7 min）" | → ≈7 min 40 s |
| `deploy/README.md:222/261/429/443` | "60 张错误照片" / "60 站各多拍一张 ≈10 分钟" / "抓 60 张" / "60 站共用" | → 48（第 261 行 ≈4.2 min） |
| `deploy/tests/test_console.py:414` | 注释 "60 站一次问完" | → 48 |
| `patrol/src/patrol/round.py:141` | 注释 "60 站都会同样失败" | → 48 |
| `patrol/src/patrol/room.py:16-17` | "拍 60 张图" / "产出 60 张错误照片" | → 48 |
| `patrol/tests/test_room.py:11` | 注释/docstring | → 48 |
| `patrol/tests/test_capture.py:250` | docstring "整轮 60 站位巡检" | → 48（**改前确认无断言依赖该数**） |

判据：`grep -rn "60 站\|60 张" patrol deploy src` 在我列的行上清零，只剩 §2.5 白名单。

### 2.2 界面师（`deploy/src/deploy/console.py`、`docs/patrol/console-ui/**`）

| 文件:行 | 现状 | 动作 |
| --- | --- | --- |
| `console.py:9` | "毁掉 60 张图" | → 48 |
| `console.py:62-65` | "60 站 × 多天 × 多角度" | → 48 |
| **`console.py:280`** | `ROUND_TYPICAL_S = 11*60` ⇒ 页面"预计时间"偏悲观约 40% | → ≈460 s，注释写实测出处（470/462/457 s，2026-10-13 发版官生产只读） |
| `docs/patrol/console-ui/spec.md:741` | "60 站就是每轮多 5 分钟" | → 48 站 ≈4.2 min |
| `docs/patrol/console-ui/review-20260918.md:165` | "60 站全量刷新太贵" | 历史评审记录 → **建议不改** |

### 2.3 测量师

| 文件:行 | 现状 | 动作 |
| --- | --- | --- |
| `src/scheduling/tasks/register_patrol_jobs.py:5` | "60"（模块说明） | → 48 |

### 2.4 step code（外部协作者，**独家文件，我不碰**）

| 文件:行 | 现状 | 动作 |
| --- | --- | --- |
| `patrol/src/patrol/framing.py:67` | `DEFAULT_MAX_TAPS = 2  # 60 站 × 2 张 ≈ 每轮多 10 分钟` | → 48 站 |
| `patrol/src/patrol/capture_client.py:70` | "网络抖动把整轮 60 站位巡检打断" | → 48 |

→ 走票据给 step code（文件边界见 `docs/patrol/imaging-quality/issues/01`「文件边界」:22-28）。

### 2.5 发版官（`docs/patrol/prod-deploy/**`、ADR）

**改（口径性陈述）**：`station-retarget.md:38,57,75,78,174`、`shortstop-rate.py:80`、
`motor-command-review.md:131`、`container-cutover.md:15`。
**不改（历史证据/当时记录）**：`z-frame-cutover.md:157`、`gap-list.md:4,8,428,435,436,854,884,889`、
`container-cutover.md:1381`、`docs/adr/0011:38`、`0007:127`、`0017:32`。
ADR 若要修，**另加"后续修订"注，不改正文数字**。

## 3. 验收（命令 + 判据）

1. `grep -rn "60 站\|60 张" patrol deploy src docs/patrol` → 只剩 §2.5 白名单的历史行；
2. `cd D:\code\mushroom\patrol-workspace && .\.venv\Scripts\python.exe -m pytest -q` → 全绿
   （基线见 Comments：`uv run` 本机被 uv 缓存目录权限挡；直接用 workspace venv 可行）；
3. 页面"预计时间"与实测 7 min 40 s 偏差 < 10%（界面师改 `ROUND_TYPICAL_S` 后，线上由发版官核）。

## 4. 不做的事

- 不改 `GRID_LAYERS=4` / `GRID_COLS=12`（48 是现役事实，不是要改的常量）；
- 不动 `docs/patrol/fmc4030-camera-scan/spec.md`（角度相关改动见票 09 §4，且**动 spec 前先报 Chief**）；
- 不改历史 log 摘录与 ADR 正文数字。

## Comments

- 2026-10-13 巡检手：**已落地（Chief 放行后）**。口径统一为 **48 站 / 单轮 ≈7 min 40 s**。

**我改的（全部在 `patrol/**` + `deploy/**` 除 console.py / framing_wire.py / preview.py）**
`deploy/README.md`（:95,:119,:131,:222,:246,:261,:355,:398,:429,:443）、
`deploy/src/deploy/m1.py`（:16,:232,:403）、`patrol_serve.py`（:7,:11,:170）、`manual.py:7`、
`fetch_room.py:6`、`patrol_trigger.py`（:11,:15,:20）、`patrol/src/patrol/round.py`（:102-103,:141,:165-166）、
`room.py`（:16-17）、`journal.py:29`、`deploy/tests/test_console.py:414`、
`test_patrol_serve.py:6`、`test_patrol_trigger.py:4,8`、`patrol/tests/test_room.py:11`、`test_capture.py:250`。

**界面师已自行改完（我复核过）**：`console.py:280` `ROUND_TYPICAL_S = 460`（注释带 470/462/457 s 出处）、
`console.py:282`、`deploy/tests/test_console.py:718`。
**仍留在界面师处**：`console.py:45`、`:69`、`:1051` 的"11 分钟"（我不碰 `console.py`）；
`test_console.py:708-711` 是合成场景数字，与 `ROUND_TYPICAL_S` 配对，交给界面师一并处置。

**未动（step code 独家文件，已按票分派）**：`patrol/src/patrol/framing.py:67`、`capture_client.py:70`。

**grep 证据（2026-10-13，改完后）**：`patrol/**` 与 `deploy/**`（除 console.py）只剩上面那两个
step code 的文件命中 `60 站|60 张|60 次|60 条|6–7 min|11 分钟`；`_top45_` 类历史样例名与
`gap-list.md` 等历史日志按 §2.5 白名单未改。

**测试**：`deploy/tests` + `patrol/tests` → **657 passed**；全量 `pytest`（patrol-workspace）
→ **844 passed, 0 failed**（测量师那 4 条红已修绿）。**我引入的失败：0。**
新增用例：`test_capture_no_lamp_shoots_a_lamp_off_control_frame`、
`test_capture_rejects_a_non_boolean_no_lamp`（"抓拍不开灯"对照帧，见下方）。

**顺带落地的新能力（Chief 批准）**：`deploy/src/deploy/manual_exec.py` 的 `capture` 支持
`args={"no_lamp": true}` —— 先灭灯再拍，链路/分辨率/站位全同，只差灯。现场按
"同站位、同时间窗，开灯/关灯各一帧"判补光灯有没有进画面；**不许拿实时预览当对照**
（预览与抓拍成像不能逐像素互推）。补光灯那条记录仍是**未证实、当前主假设**。

- 2026-10-13 巡检手：立项。数据来源：发版官生产只读实测（经 `docs/agents/bot-roles.md:78-79` 转述）。
  本票**只登记与分派，未改任何文件**——等 Chief 放行后我改 §2.1 那批；§2.2/§2.3/§2.4/§2.5 已分别告知归属方。
- 测试基线（2026-10-13，巡检手）：`patrol-workspace\.venv\Scripts\python.exe -m pytest -q`
  第一次在**沙箱只写会话工作区**下跑出 `467 passed, 323 errors`，errors **全是 `PermissionError`
  （Windows 写权限）**，集中在 `analysis/tests/test_growth_api.py`、`test_ingest_room.py` 等写库用例
  ——属环境权限，不是产品缺陷；放宽文件权限后复跑结果见下条。
- 测试基线（2026-10-13，巡检手）**放宽权限后复跑**：
  `786 passed, 4 failed in 111 s`。4 个失败**全在 `measure/tests/test_record.py`**（测量师的领地）：
  `test_record_fields_match_spec_contract`（字段/顺序与契约不符）+ 三个
  `MeasurementRecord.from_box_stats() missing 1 required keyword-only argument: 'room_id'`
  （`test_from_box_stats_translates_names_once` / `test_to_row_keys_exactly_contract` /
  `test_roundtrip_via_json_safe_types`）。**判读：与本次清理无关，也不是我改的**（我本轮未改任何代码），
  更像是 `measure` 侧在制品——已单独告知测量师。`patrol/**` 与 `deploy/**` 的用例全绿。

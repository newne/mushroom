# 17 — 轮内容完整性：复制帧/可疑轮不进聚合统计（票 13 的 analysis 侧落地）

**Spec:** `fmc4030-camera-scan/issues/13-round-content-integrity.md`；../spec.md §5（区间表）、§2.7（长势）

**归属：** 环境官（`analysis/**`）；帧/轮侧的标记由巡检手产生（`6a232e4`）。

**Status:** resolved（排除规则 + 列已落地；`/ranges`、`/growth` 的**计数暴露**待随各自改造一起上）

**What to build:** 让"复制帧"进不了任何聚合统计 —— 它们会让长势曲线看起来**正常**
（2026-10-09 18:20–18:27 那轮 48 帧里 35 帧字节相同，轮行却写 `ok=1 / 0 失败`），
这种错误比缺数据难发现得多。

## 1. 列（先落列，再由巡检手发字段）

| 表 | 列 | 由谁产生 |
| --- | --- | --- |
| `images` | `frame_dup`、`dup_group`、`content_sha256` | 巡检手 |
| `rounds` | `suspect_duplicate`、`dup_groups`、`content_check_error` | 巡检手 |

`ADDED_COLUMNS` 就地迁移、老行 NULL（NULL = "那时还没查"，**不是"已确认无重复"**）。
`IMAGE_FIELDS`/`ROUND_FIELDS` 已带上，`/ingest` 原样透传（提交见 Comments）。

## 2. 排除规则（`analysis/src/analysis/integrity.py`）

| 标记 | 处置 |
| --- | --- |
| `images.frame_dup = 1` | 该帧不进统计 |
| `rounds.suspect_duplicate = 1` | **整轮**的帧不进统计 |
| `rounds.content_check_error` 非空 | **"没查成"≠"已确认无重复"** ⇒ 同样排除，并记原因 |

**为什么按"轮窗口"排**：测量行只有 `(ts, room_id, box_id)`，**没有 `round_id`**
（票 11 的线格式里没有），逐帧对不上。于是取
`[round.ts, 同库房下一条 round.ts)`；没有后继轮次时上界封顶
`round.ts + SUSPECT_MAX_SPAN_H`（1 小时）——**不让一个可疑轮把该库房之后的数据永久排除**。

**不静默丢**：`partition()` 返回 `Dropped` 明细（ts/zone/reason/window），
调用方要把它记进响应或日志。

## 3. 接线状态

- ✅ **区间表路径**：`align.run_from_db` 先过滤再算（并打印排除条数）。
- ⏳ `/growth`（`api.py` 的 measurements 读取）与 `/ranges` 响应里的
  `dropped`/`suspect_windows` **计数暴露**：随 `refresh_ranges` 接调度那次改造一起做
  （票 05），届时界面能看到"这一段因为可疑轮被排除"，而不是只看到"数据少"。

## 4. 验收

- [x] `test_integrity.py`：窗口=到下一轮为止、没查成也排除、干净轮不排、最后一条可疑轮不变永久窗口、老数据（无列）不误排、别的库房不受影响
- [x] `test_ingest_room.py`：迁移把 6 列补上（老行 NULL）
- [x] 全量 analysis **140 passed**

## Comments

- 2026-10-18 开票并落地（同日）。列由环境官先落（提交见仓库 log），巡检手随后按"先落列再发字段"的顺序发。
- 与票 11 §5 的关系：那是"空值 ≠ 0"，本票是"复制帧 ≠ 长势"，同一条保守原则的不同面。

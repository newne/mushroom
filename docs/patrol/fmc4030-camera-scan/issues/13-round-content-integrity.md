# 13 — 采集可信度：`ok=1` 不证明"内容属于这一站"（同轮字节重复的取证与标记）

**归属：** 巡检手牵头（`ok` 语义属 `round`/`daemon`/`journal` 的地界）；采图侧取证 = **step code**
（`capture_client.py`/`third_party/capture/**`）；统计排除 = **测量师 + 环境官**；
全池只读重扫 = **发版官**。

**Status:** 取证完成 + 标记已落地；等 step code 的采图侧结论与发版官的全池 sha 重扫。

**依据：** `docs/patrol/prod-deploy/issues/05-frame-pool-tail-scan.md` §5（发版官 2026-10-13 只读实测）；
`.scratch/frame-quality-samples/tail/all-metrics.csv`（3147 帧度量，只读）。

---

## 1. 事件

- 2026-10-09 **18:20:05–18:27:16** 一轮 48 帧里，**35 帧 sha256 完全相同**
  （92364 B、2880×1616、`mean` 2.31、`p99` 2.0），横跨 S211→S401，历时约 5 分 11 秒；
- 而该轮 **round 行写着 `ok=1` / 48 帧 / 0 失败 / 0 中止**；
- 对比 2026-10-08 的近黑帧**各站字节互不相同**（89837 / 90493 / 92060 / 112448）
  ⇒ 那是各站独立的**真欠曝**；10-09 是**同一份字节被复制**。**两者性质不同，不混为一类。**

## 2. 本票新增的取证（全池同轮重复扫描，只读）

方法：读发版官的 3147 帧度量 CSV，按 ts 间隙 15 分钟切轮窗口，用
`(bytes, lapvar, p99, dark_frac, mean_luma)` 当**指纹代理**（CSV 没有 sha256），
统计每个窗口内"同一指纹"的最大组。

| 结论 | 值 |
| --- | --- |
| 切出轮窗口 | **76 个** |
| 可疑轮（同指纹 ≥5 帧） | **只有 1 轮**：`2026-10-09 18:20:05–18:27:16` |
| 该窗口 | 48 帧，最大同指纹组 **35 帧**（bytes 92364、lapvar 46.84、p99 2.0、mean 2.3） |
| 其余 75 个窗口 | 最大同指纹组 **= 1 帧**（无同型轮次） |

**修正一处既有表述（重要）**：不是"整轮同一张"。逐帧看这 48 帧——

- **前 13 帧是正常帧**：S101…S112 + S212，bytes 548 K–792 K、`lapvar` 60–85、
  `p99` 210–247、`mean` 73–119；
- **从第 14 帧 S211 起**（`S211→S201`、`S301→S312`、`S412→S401`）**全部是同一份陈旧近黑帧**。

⇒ 失效的**起始点在中轮的 S211**；"从头到尾一张"不成立。这条对采图侧定位很关键：
不是"整轮没连上相机"，而是**中途开始重复交付同一张**（连接池/等帧/帧复用嫌疑）。

复跑（只读、不连生产）：`python .scratch/dup-scan.py`（读上面那份 CSV）；脚本落在 `.scratch/`。

## 3. `ok` 语义收紧（设计 + 已实现）

**现状缺陷**：`ok=1` 只证明"这一站取到了文件"，**证不了"取到的是它自己那份"**。

**做法：留帧 + 标记 + 原因，绝不静默丢。**

| 层 | 字段 | 含义 |
| --- | --- | --- |
| 帧行 | `content_sha256` | 复核到就写（部署侧取回 `cloud_url` 算 sha256） |
| 帧行 | `frame_dup: true` + `dup_group: <sha12>` | 同一指纹出现在 **≥2 个不同站位**（同站位重试同一张不算） |
| 轮行 | `suspect_duplicate: true` + `dup_groups: [{sha256_12, n, stations}]` | 本轮有跨站复制 |
| 轮行 | `content_check_error` | 复核**没查成**（取图失败等）——**不谎报"没有重复"** |
| journal | `{"event": "content_duplicate", "groups": …}` | 取证文件里也留一笔 |

实现落点：

- `patrol/src/patrol/daemon.py`：`PatrolDaemon(content_hash=…)` + `_check_content()`
  —— 在**写索引之前**跑，标记才跟着帧走；`content_hash=None`（默认）= 不查、不加字段，
  行为与从前完全一致。
- `deploy/src/deploy/content_verify.py`：`make_content_hash(fetch=…)` —— 取回 `cloud_url` 算 sha256；
  **取图失败上抛**（由 daemon 记 `content_check_error`），不把失败伪装成"没有重复"。
- `deploy/src/deploy/m1.py`：`--verify-content`（**默认关**）。代价 ≈34 MB/轮
  （48 × ≈700 KB），所以是显式开关。

**排除规则（跨域，必须落地）**：带 `frame_dup` 的帧与 `suspect_duplicate` 的轮
**不得进入长势与质量统计**（区间表、生长曲线、阈值标定样本）。归属：测量师（`measure/**`）
与环境官（analysis 聚合）各开一张票；我这边只负责把标记打准、把帧留住。

## 4. 证据（命令 + 结果）

```bash
cd D:\code\mushroom\patrol-workspace
.\.venv\Scripts\python.exe -m pytest -q ../patrol/tests/test_daemon_sync.py \
  ../deploy/tests/test_content_verify.py ../deploy/tests/test_m1.py
# → 43 passed
```

- `patrol/tests/test_daemon_sync.py`：三例 —— 同指纹跨站 ⇒ 帧 `frame_dup` + 轮
  `suspect_duplicate` 且**帧数不减少**；复核抛错 ⇒ 轮 `content_check_error` 且不假装无重复；
  不查 ⇒ 行里没有多余字段。
- `deploy/tests/test_content_verify.py`：sha256 取自真实取回字节；无 `cloud_url` ⇒ 不取图；
  取图失败上抛；同一份字节 ⇒ 同一指纹。

## 5. 需要别人做（跨域，已按归属分派）

1. **发版官（只读）**：① 把该轮 journal / round 原文取回，核"48 站是不是都记了 ok、
   `n_results` 到底多少、有无失败站"；② 全池用**真 sha256** 重扫同轮重复（他下过全池，
   加一列 sha 即可）——我这份用 `bytes` 当代理，只能抓"整簇同字节"。
2. **step code**：什么条件下会返回**同一帧**（连接池复用 / `MakeKeyFrame` / 等帧逻辑 /
   子码流回退），给票据。
3. **测量师 + 环境官**：按 §3 的标记排除；标定样本也要排除（那轮 35 帧全在同一指纹下）。
4. **我**：标记已落；等 1/2 回来再定策略（当场重拍、重建连接、或把可疑轮直接判 `partial`）。

## Comments

- 2026-10-13 巡检手：立项。取证来自发版官只读实测（票 05 §5）+ 我对 3147 帧 CSV 的只读重扫；
  代码与测试随本票一起提交。**未连生产、未改历史数据**。
- 本票**修正**了"整轮抓到同一张"这个读法：正确说法是"**从第 14 帧（S211）起**开始复制"，
  前 13 帧（S101–S112、S212）是正常帧。

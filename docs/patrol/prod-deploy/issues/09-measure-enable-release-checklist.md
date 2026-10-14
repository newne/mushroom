# 09 — 生产开启 `--measure` 的发布前置与动作清单（等测量师）

**归属：** 发布动作 = 发版官；参数清单 = 测量师；裁决 = Chief
**状态：** **准备中，未执行**（Chief 2026-10-14 裁定：单主线先完善量测链路，采集链路用现版；**我的动作排在测量师之后，现在只做准备、不动生产**）

## 0. 一句话

`--measure` 从 off → on 是一次**行为开启**，不是一次普通重建：判据 = **运行态 argv** + **开启前后 `measurements` 行数** + **回滚路径**；
**采集侧（patrol 的取景/采图参数、console、preview、capture）一律不动**。

## 1. 前置（缺一不开启）

1. **测量师的开启清单**：`--measure on` + **尺度来源** + `--room` + `emit_empty` 等**整组显式参数** —— 不接受"默认值差不多"，要逐项写死；
2. **门禁全绿**在该 commit 点（按既定口径：锁派生干净环境 + 默认 editable）；
3. **本地 `/measurements` 非零行证据**（测量师在本地跑通、有行，而不是"应该能出"）。

## 2. 我开启时的动作（逐条留证）

1. **开前**：记 `measurements` 行数、`GET /api/measurements?room_id=611` 的 `rows/n`、**patrol 容器运行态 argv**（`/proc/1/cmdline`，不看 compose 猜）；
2. 按清单改**生效参数**（按清单指定落在 `.env` / compose / 命令行），改动文件带时间戳备份；
3. **只重建 `mushroom_patrol`**（`docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_patrol`）；**console / analysis 不动**；
4. **开后**：**从运行态**核 argv、记 `measurements` 行数变化、`/api/measurements` 的 `rows/n`、页面 200、六容器 healthy；
5. **回滚路径**：恢复备份文件 + 只重建 patrol（与开启对称，一步可退）。

## 3. 不做的事

- **不动采集链路**：不重建 console、不动 preview/capture 参数、不碰取景（`--framing` 仍 off、`--verify-content` 仍 off）；
- **不借这次顺手改别的**：一次只开一个开关，单写者制。

## 4. 当前状态

- **2026-10-14 13:36：未开启。** 等测量师清单 + Chief 的"可以开"。
- 另记：`2b24ead` 已在 HEAD、构建点干净（13:36 实测），但"重建 analysis 到该构建点 + 跑 ④ batch_ingest"这件事**也一并按住不动**——本轮 Chief 明确"不动生产"，等主线允许再排。

## Comments

- 2026-10-14 发版官写票。依据 Chief 当日裁定（单主线、动作排在测量师之后、只做准备）。

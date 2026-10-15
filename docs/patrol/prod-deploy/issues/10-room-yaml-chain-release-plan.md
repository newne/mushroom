# 10. `room.yaml` 刷新链路的发布清单（解除"容器不动"后的**第一批**，硬边界 2026-10-20）

- **归属：** 部署动作 = 发版官（本票）；调用者代码 = **测试**（票 18 `6f00e01`）；`/batches` 端点与定时刷新 = analysis；裁决 = Chief
- **状态：** **就绪、未执行**。Chief 2026-10-14 15:3x 裁定：排期 = **"容器不动"解除后的第一批，且必须早于 2026-10-20**
- **关联：** `docs/patrol/fmc4030-camera-scan/issues/17-fetch-room-via-pgsql.md`（端点）、票 18（调用者）、`prod-deploy/issues/09` §5.1（analysis 重建与 `BATCH_AT`）

---

## 0. 一句话

链 = **`BATCH_AT` 定时刷 `batches_state` → analysis `/batches`（带 `stale`/`stale_after_h`）→ 轮次入口每轮拉一次 → 原子更新 `room.yaml`**。
今天这条链**断在三处**：端点没上线（404）、定时刷新停着（宿主 timer 已停、容器无 `BATCH_AT`）、**没有任何调度或调用在跑 `fetch_room`**。

## 1. 现状（2026-10-14 15:3x–15:4x 只读实测，逐条可复验）

| # | 环节 | 现状 | 证据 |
| --- | --- | --- | --- |
| 1 | `room.yaml` 内容 | 停在 **2026-10-06 21:01:08**、`entry_date=2026-09-30`、`batch_no=mogu-107`、`packages=9792` | `cat` 原文（准入文件、非凭据） |
| 2 | **两份副本** | `configs/room.yaml` 与 `data/room.yaml` **逐字节相同**，sha256 都是 `7f1641e879c7c73a2e57c2d6d5f584737e1a990eaf3844bbb529067133dd4aaf` | `sha256sum` 两份 |
| 3 | 容器在读哪份 | 现役容器走默认值 ⇒ 读 `/app/configs/room.yaml`（683 B，`2026-10-06 21:01:10`） | 容器内 `stat` |
| 4 | `/batches` 端点 | **404**（线上 analysis = `c3dcbc5` 不含该端点；`stale`/`stale_after_h` 只存在于仓库） | `curl 127.0.0.1:8000/batches?room_id=611` |
| 5 | 定时刷新 | **停着**：宿主 `mushroom-batch-ingest.timer` 2026-10-14 10:28:35 停用；容器无 `BATCH_AT` | 票 09 §5.1 |
| 6 | 调用者 | **没有**：`systemctl list-timers --all \| grep -iE 'mushroom\|fetch'` → none；`crontab -l` → no crontab for root；patrol 日志 19 h 内 `fetch-room\|room.yaml` 命中 **0** | 15:3x 实测 |
| 7 | 调用者代码 | 票 18 = **纯文档零代码**（`git show --stat 6f00e01` 只加了 `issues/18-…md`） | `git show --stat` |
| 8 | 现役 patrol 镜像 | `89e4a54`（10-13 构建）⇒ 里面的 `fetch_room` 仍是 **mysql 通路** | `docker ps` + 票 13/17 |

## 2. 执行顺序（一批做完，**不许跳**）

| 步 | 动作 | 归属 | 关键点 |
| --- | --- | --- | --- |
| **0** | **等票 18 的调用者代码落地**（不是文档） | 测试 | `6f00e01` 零代码 ⇒ **现在重建 patrol 毫无意义**（镜像里仍没人调）。这是整批的**唯一前置** |
| 1 | **重建 analysis**：compose 加 `BATCH_AT=05:20` + 显式 `PATROL_ANALYSIS` | 发版官 | 判据与清单见票 09 §5.1；完成后 `/batches` 必须 **200**、`stale` 字段上线 |
| 2 | **compose 加 env**：patrol 与 console **都设** `PATROL_ROOM=/app/data/room.yaml` | 发版官 | 显式写，不靠 `Dockerfile.patrol:73` 的默认值 |
| 3 | **迁移**：~~`cp` `configs/room.yaml` → `data/room.yaml`~~ ⇒ **无需执行** | 发版官 | **两份已逐字节相同（§1-2）**；只保留 `configs/` 那份做历史，**不动 `/app/configs` 的 `:ro`** |
| 4 | **重建 patrol 镜像**（含票 18 的调用者 + 新 `fetch_room`） | 发版官 | 现役 10-13 镜像里是 mysql 通路；重建后轮次入口才会调 `/batches` |
| 5 | **重建 console** | 发版官 | `deploy/src/deploy/console.py:921` 也读 `PATROL_ROOM` |
| 6 | **验收**（§4） | 发版官 | 跑至少一个完整轮次窗口 |

**顺序理由**：先有端点（1）才有可拉的 `/batches`；先落新路径再改 env（2→3 已合并，因文件已在位）；先有调用者代码（0）才有重建价值。

## 3. 现役 compose 的挂载事实（已核实，改动最小化）

| 事实 | 位置 |
| --- | --- |
| patrol：`../mushroom_patrol/configs:/app/configs:ro`、`../mushroom_patrol/data:/app/data:rw` | `docker/mushroom_solution.yml:196`、`:197` |
| console：同 configs `:ro`、`../mushroom_patrol/data:/app/data:ro`（+ `:249/:250` trigger/cmd 子挂 rw） | `:242`、`:243` |
| `PATROL_ROOM` 默认 `/app/configs/room.yaml`；patrol 入口三处引用 | `docker/Dockerfile.patrol:73`、`docker/patrol-entrypoint.sh:21/30/45` |
| console 也读 `PATROL_ROOM` | `deploy/src/deploy/console.py:921` |

⇒ 两容器**本来就共挂同一个宿主 data 目录**，因此**只加两行 env + 重建两个镜像**，**不新增挂载、不新增 service**。

## 4. 验收判据（逐条落证据）

1. 容器内 **`PATROL_ROOM=/app/data/room.yaml`**（`/proc/1/environ` **只报这一个键**，不 dump 全环境）；
2. 容器内 `/app/data/room.yaml` 存在、sha256 与宿主一致；`/app/configs/room.yaml` 仍在（历史）；
3. **跑完一个完整轮次**后：patrol 日志出现"拉 `/batches`"的记录；**批次未变 ⇒ 文件 mtime 不变**（不空写）；
4. **批次变化时**：日志打印**旧→新**（`batch_id`/`entry_date` 前后值），文件**原子更新**（同盘 `cp+mv`，不留半截文件）；
5. **端点不可用 或 `stale=true`** ⇒ **保留旧文件**、沿用最后已知批次继续跑、**每轮醒目告警 + 计数**（不静默、不空表顶替）；
6. `GET {PATROL_ANALYSIS}/batches?room_id=611` **200** 且响应含 `stale`/`stale_after_h`/`ledger_refreshed_at`；
7. 七容器 healthy、页面 200、`rounds`/`images` 行数不变；**不出现连带重启**（部署前后 tag/uptime 对比）。

## 5. 风险与红线

- **`/app/configs` 里有 `.secrets.toml` ⇒ 绝不放宽它的 `:ro`**；把可变文件从只读配置目录挪到可写数据目录，方向是对的；
- **顺序不能反**：文件不在位就改 env ⇒ 容器读到不存在或读到旧值（`IsADirectoryError` 类 fail-closed 或静默用旧文件）；
- **不新增 service/timer/cron**（Chief 裁定：调用挂在**轮次入口**）；
- **硬边界**：`entry_date` 固定 `2026-09-30` ⇒ day 25 ≈ **2026-10-25** 之后准入窗口关闭；**最迟 10-20 放行本批**；
- **别把"重建"当"救活"**：本批只保证"端点可用 + 台账新鲜 + 有人调"；**`room.yaml` 真正被刷新**还要第 0 步的调用者代码真的落地并被轮次入口调到。

## Comments

- 2026-10-14 15:4x 发版官写票。依据 Chief 当日裁定（归属：调用者=测试、部署=发版官；排期=解除冻结后第一批且早于 10-20）。
- 修正 Chief 步骤②：`data/room.yaml` **已存在且与 `configs/room.yaml` 逐字节相同**，故 **`cp` 一步省掉**。

- **2026-10-15 13:01 执行（本票的 analysis 侧，第一批）**：镜像 `registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0-20261015125805-b61d970`（digest `sha256:7957ce30…`，**从 `main`@`b61d970` 构建**、`-f docker/Dockerfile.patrol`、未走混淆——ADR-0012 明确混淆构建不覆盖 patrol）。prod `.env` 的 `PATROL_IMAGE` 改到该 tag；`mushroom_analysis.environment` 增 `BATCH_AT=05:20`；`docker compose up -d --force-recreate --no-deps mushroom_analysis`。
  - **为什么必须重新构建**：线上原有镜像最新只到 `c3dcbc5`（10-14 13:07），而 `/batches` 是提交 `3c30feb` 引入的（**在 `c3dcbc5` 之后**）⇒ 只改 env 再 recreate 修不了 404。
  - **判据实测（全过）**：`/batches?room_id=611` **200**；`stale=false`、`ledger_refreshed_at=2026-10-15T13:01:14`、`ledger_n_rows=27`、`ledger_n_pruned=11`、`room_alias` 27/27 resolved、`duplicates` 27 kept / 0 dupe；`/ranges` 200；`rounds`/`images` 响应长度与前置快照**逐字节相同**（23718 / 2246947）；七容器 healthy，**patrol 未被触碰**（Up 40h）。
  - **备份与回滚**：`.env.bak-20261015-130020`、`mushroom_solution.yml.bak-20261015-130020`；回滚 = 还原这两个文件后同一条 `up -d --force-recreate --no-deps mushroom_analysis`。
  - **第 0 步仍未做**：调用者代码（票 18 的 `fetch_room`）是**纯文档零代码** ⇒ `room.yaml` **不会被刷新**。本批只交付"**端点可用 + 台账新鲜**"。
  - **一处更正**：`PATROL_ANALYSIS` **不是** analysis 容器读的变量（`analysis/jobs.py` 只认 `BATCH_AT`/`RANGES_AT`/`ENV_INGEST_INTERVAL`/`AUDIT_INTERVAL`），所以只加了 `BATCH_AT`。
  - **顺带确证**：`/batches` 给出的 611 批次链是 **5 条**，含此前只有旧文档提过、当时"**不采信**"的 `2025-11-27`（→ until 2025-12-24）⇒ 票 13 的台账判据与 A3「批次清单对齐」由此闭环。


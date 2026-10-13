# 13 — 批次台账改直连 MySQL 只读账号（迁移目标；过渡期暂停）

**Spec:** ../spec.md §2.6、ADR-0020（批次身份 = 库房号 + 入库日期）

**归属：** 代码（`analysis/**`）= 环境官；只读账号与凭据文件 = 发版官 / DBA；
compose 改动 = 发版官。**本票不碰生产、不连生产**。

**Blocked by:** 票 12（容器化启动契约）—— 本票是它的过渡期欠账；换来的东西见 §1。
**Status:** ready-for-agent（先做代码，账号与可达性待现场核对，见 §5）

## 1. 为什么必须改

今天 `analysis.batches.fetch_batches` 走 **`docker exec tools_mysql mysql …`**
（`analysis/src/analysis/batches.py:56-59`），口令只存在于 `tools_mysql` 容器的 env。
迁进容器后这条跑不通，除非给 API 容器挂 `/var/run/docker.sock` ——
那等于给**对外的 :8000 服务**配 root 等价权限。**Chief 2026-10-13 明确不批。**

⇒ 迁移当天**不装配**批次作业（compose 不设 `BATCH_AT`），落地本票后补跑一次补齐窗口。
全量覆盖 upsert（PK `(room_id, entry_date)`，`db.py:305-312`）⇒ 缺几天都能回补，**不会丢**。

## 2. 目标形态

- `analysis.batches` 改成**直连 MySQL 只读账号**取数：仍然只读 `mogu.mo_gu_batch`
  的 `id` / `code_num` / `in_time` 三列，SQL 不变（`batches.py` 的 `parse_batches` 不动，
  解析层已有单测 `test_batches.py`）。
- 凭据走挂载的 env 文件（**不入仓库、不进 compose 明文**）：新增键
  `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE`，
  与 `EMS_*` 同一份 `/etc/mushroom/ems.env` 或同一目录的另一份。
- **只读**账号：`SELECT` 权限足够；不做任何写。
- `docker exec` 那条通路**保留**为本地开发/演练用（`runner=` 注入点已经在），
  但生产装配不许用它 —— 代码里写清"容器里只允许直连"。

## 3. 代码改动（我）

1. `analysis/batches.py`：新增 `fetch_batches_mysql(host, port, user, password, database)`
   走驱动直连；`fetch_batches(runner=...)` 保留（现有测试与本地演练依赖它）。
2. 驱动选择：**未知**现役镜像里有没有 MySQL 驱动（见 §5）；优先用标准库之外的既有依赖，
   否则新增一个纯 Python 驱动（`pymysql`）并在 `analysis/pyproject.toml` 里声明。
   ⇒ 这条要在动手前先测：`docker run --rm <PATROL_IMAGE> python -c "import pymysql"`。
3. 单测：把直连取数抽成 `runner` 同形的注入点（`fetch_batches(fetch_sql=...)`），
   用假连接测解析与列序；另外测"连接失败要抛 `BatchFetchError`，不许退回空表"
   （空表会让"当前批次"静默变成"没有批次"，比报错坏得多）。
4. 补跑入口：`python -m analysis.batch_ingest --db <db> --once`，落地当天跑一次把窗口补齐。

## 4. 验收

- [ ] 容器里 `batches` 作业**不再需要 `docker.sock`**（挂载里没有它）
- [ ] 只读账号实测：`SELECT` 通过；`UPDATE`/`INSERT` 被拒（拿证据）
- [ ] `batches` 行数与生产库 `mogu.mo_gu_batch` 一致；`/environment/batches` 的"当前批次"正确
- [ ] 连接失败 = 抛错 + 日志，**不写空表、不清空台账**
- [ ] 过渡期缺口回补后：`GET /environment/batches` 的批次窗口与 spec §2.6 一致

## 5. 未知 / 待现场核对（不许猜）

1. `tools_mysql` 的 **3306 是否发布到宿主 / 容器网络里的别名**（现在只确认口令在它 env 里）。
2. **只读账号由谁建、建在哪**（生产 `mogu` 库的授权流程）——发版官 / DBA 定。
3. 现役 `PATROL_IMAGE` 镜像里**是否已有 MySQL 驱动**（决定加不加依赖）。
4. 生产是否走 TLS / 是否需要 `charset=utf8mb4` 之类连接参数。
5. 凭据放 `ems.env` 还是新文件（`ems.env` 现在 101 B、0600 root:root）。

## Comments

- 2026-10-13 开票（Chief 裁）。原方案里"过渡期挂 docker.sock"作废：那是给对外服务配
  root 等价权限。过渡期按 (c) **暂不迁**，落地本票后补跑一次补齐窗口。
- 与票 12 的关系：票 12 §1.1 已经写死"迁移当天不装配 batch 作业、compose 不设 `BATCH_AT`"，
  代码侧的常驻 loop（`--interval` / `--daily-at`）**已经实现**，本票只需要换取数通路。

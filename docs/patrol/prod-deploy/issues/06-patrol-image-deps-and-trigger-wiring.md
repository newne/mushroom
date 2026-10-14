# 06 — patrol 镜像带 A′ 依赖的代价评估 + `--trigger-dir` 路径对账

**归属：** 发版官（镜像代价评估 + 只读运行态对账）
**日期：** 2026-10-14 10:2x–10:5x
**结论一句话：** A′（把 `src/**` 带进 patrol 镜像、直接 `import utils.batch_yield_service`）**不可接受**。
体积不是问题（源码 4.01 MB），**代价是 24 MB 的 7 个新依赖 + 610 MB 的 mlflow 闭包**，
后者直接破 `Dockerfile.patrol` 头注释那条"**不背 torch/opencv/mlflow**"的底线；
另外 `import` 期会装配算法侧 pgsql 引擎 + Redis 连接池，且 `cache_client.py` 里有**硬编码 Redis 口令**会随镜像推上 registry。

## 1. 能力测试（Chief 指定，一次）

```
docker run --rm --entrypoint /srv/patrol-workspace/.venv/bin/python \
  registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0-20261013204311-89e4a54 \
  -c "import utils.batch_yield_service as m; print(m.get_batch_ranges)"
```

**结果（2026-10-14 10:47，实测原文）：**

```
ModuleNotFoundError: No module named 'utils'
exit=1
```

⇒ 现役镜像里**没有 `src/**`**（`docker exec … ls /app/src` → No such file），
`utils.batch_yield_service` **根本不在**。失败即结论：A′ 不是"换个 import 就行"，是**要给镜像加一整摊东西**。

## 2. 代价数字（全部实测，2026-10-14 10:4x–10:5x）

### 2.1 体积：源码不是问题

| 项 | 值 |
| --- | --- |
| 现役镜像 | **747,653,307 B**（≈713 MiB；`docker images` 显示 748 MB），image ID `sha256:29abb2ed326e…` |
| `src/` 全量 | 378 文件 / **4.01 MB** |
| 其中可去掉的 | `__pycache__` 132 个 `.pyc` **1.23 MB** + `reports/**/*.csv` 17 个 0.46 MB |
| `src/` 真正源码 | **≈ 2.32 MB** |
| `batch_yield_service.py` 仓库内闭包 | **11 个 `.py` / 0.20 MB** |

⇒ **只算源码，+2.3～4.0 MB / 748 MB，可以忽略。**

### 2.2 依赖：这才是代价

`import utils.batch_yield_service` 的第三方闭包（AST 实测）：

```
loguru  sqlalchemy  sqlalchemy_utils  pgvector  redis  dynaconf  tenacity  pandas  requests  mlflow
```

patrol 镜像里现在有什么（容器内 `importlib.util.find_spec` 实测）：
pandas ✅ / numpy ✅，**sqlalchemy ❌ loguru ❌ dynaconf ❌ redis ❌ pgvector ❌ sqlalchemy_utils ❌ tenacity ❌ mlflow ❌ psycopg ❌**。

**真装一遍量一量**（`docker run --rm` 一次性容器，`uv pip install --target`，装完即销毁）：

| 组 | 装完体积 |
| --- | --- |
| 基础 7 个（sqlalchemy / loguru / sqlalchemy-utils / pgvector / redis / dynaconf / tenacity，含传递的 typing_extensions） | **24 MB**（sqlalchemy 18 MB、redis 2.8 MB、dynaconf 1.2 MB、其余 <0.5 MB） |
| **mlflow**（`environment/processor.py` 惰性 import 的那一支） | **610 MB** |

mlflow 610 MB 的构成（`du -sh` 实测，前几大）：
`pyarrow 152 MB`、`scipy 81 MB`、`mlflow 49 MB`、`pandas 41 MB`、`sklearn 32 MB`、`numpy 30 MB`、`matplotlib 25 MB`、`cryptography 15 MB`、`pillow.libs 14 MB`、`databricks 7.4 MB`、`aiohttp 7 MB`。

### 2.3 会不会拖进重依赖？

分三层说，**都要看**：

1. **这个闭包里没有 torch / opencv**（AST 实测，闭包 11 个文件、第三方只有上面那 10 个）⇒ `src/vision/**` 之类不会被 import。
2. 但 **mlflow 在闭包里**：`batch_yield_service._get_iot_batch_ranges()` 会 `from environment.processor import get_room_mushroom_info`，而 `environment/processor.py` 要用 mlflow。
   该 import 包在 `try/except` 里（`batch_yield_service.py:29-33`）⇒ **不装 mlflow 不会崩，而是静默 `return []`**，
   也就是"IoT 那一路取数悄悄变空"。要么装 610 MB，要么接受静默降级——两条都不是好选项。
3. 若 `COPY src/` 是**整棵树**，那 `src/vision/**`（torch）、`src/web_app/**` 这些**源码也会进镜像**（即使不被 import）。对"镜像是最小可复现单元"这条纪律是倒退。

### 2.4 构建时间增量

**未实测**（没有为一次评估去做完整构建）。可给的量：
`uv.lock` 一旦变（要么加这 7+n 个包，要么加 mlflow），`uv sync --frozen` 那一层**缓存必失效、整层重跑**；
现在整次构建约 14 min（apt+ffmpeg 段 + uv sync 段）。**新增包的下载/解包本身是秒级到十几秒级**（24 MB / 610 MB），
真正的增量来自"整层不再命中缓存"。**这一项我给不出硬数字，不编。**

### 2.5 import 期副作用（比体积重）

`src/global_const/global_const.py` 在**模块 import 时**就 `import_module` 了：

| 被拉的模块 | 顶层副作用 |
| --- | --- |
| `global_const.db_engines` | `sqlalchemy.create_engine(算法侧 pgsql DSN)` |
| `global_const.cache_client` | `redis.ConnectionPool(...)` + `redis.Redis(...)` |
| `global_const.config_loader` | `dynaconf.Dynaconf`（算法侧 settings） |
| `utils.create_table` | `sqlalchemy_utils`、`pgvector.sqlalchemy.Vector`、`storage.models.base.Base` |

⇒ 巡检容器要能跑，就得有算法侧 DSN、Redis 主机与口令、dynaconf 配置文件。

### 2.6 安全（单列一条）

`src/global_const/cache_client.py:31`：Redis 口令**硬编码明文**（`REDIS_HOST` 缺配置时的兜底分支赋值）。
⇒ `src/**` 进 patrol 镜像 = 口令随镜像推到 registry。
（本票**不抄录口令原文**；文件与行号足够定位。建议另开票把该兜底分支改成 fail-fast。）

### 2.7 判定

**不可接受。破的底线有两条：**
① `Dockerfile.patrol:1` 明写 ADR-0012"**不背 torch/opencv/mlflow**"——A′ 要么带 mlflow（+610 MB、镜像 → ~1.36 GB），
② 要么让 IoT 取数**静默降级为空**（`try/except` 吞掉），两者都不可接受；
③ 外加"算法侧引擎/连接池在 import 期装配"与 2.6 的口令。

**建议退路**（Chief 已有方案：算法侧把实时并集物化 + 刷新可见，不新建 HTTP 服务/端口）——
巡检手在 `01f74eb` 已把替换点收成一个函数 `fetch_ledger_rows(room_id, *, runner=…)`，
换退路**只改这一处**（票 `fmc4030-camera-scan/issues/17-fetch-room-via-pgsql.md`）。

## 3. `--trigger-dir` 路径对账（只读，2026-10-14 10:2x）

Chief 裁定不做受控 hold 实测，只做路径对账。结论：**接线成立**。

| 端 | 运行态证据 | 目录 |
| --- | --- | --- |
| console（写 `hold.json`） | `deploy/src/deploy/console.py:149` default = `/app/data/trigger`；容器 `Config.Env` 里**没有** `PATROL_TRIGGER_DIR` ⇒ 用 default | `/app/data/trigger` |
| patrol（读 `hold.json`） | **进程真命令行**（`/proc/1/cmdline`）：`--trigger-dir /app/data/trigger` | `/app/data/trigger` |

两端指向宿主**同一个目录**：

```
/home/sysadmin/algorithm/mushroom_patrol/data/trigger
  -> mushroom_console:/app/data/trigger   (bind, rw=true)
  -> mushroom_patrol:/app/data/trigger    (经 .../data -> /app/data, bind, rw=true)
```

文件名也对得上：`TriggerStore.hold_name = "hold.json"`、`_hold_path() = Path(dir_path) / hold_name`
（`deploy/src/deploy/patrol_trigger.py:93,105-106`）。

目录当前内容：`run.json`、`runs.jsonl`（console 的触发与历史落盘）——
**没有 `hold.json`**（当前无保持生效，符合预期，不是问题）。

⇒ **路径 / 文件名 / 挂载三方一致**；动作路径（就地停、安全回原位）已有单测覆盖。

## Comments

- 2026-10-14 发版官写票。数字来源：仓库本地 `src/` 体积统计 + AST import 闭包（`import-closure.py`）+
  **生产容器内** `find_spec` / `du` 实测 + **一次性容器真装依赖量体积**（`docker run --rm`，装完即销毁，
  生产零残留）+ 生产 `docker images` / `docker inspect` / `/proc/1/cmdline`。
  §3 全程只读。**唯一未实测项 = 构建时间增量（§2.4），已如实标注。**
- 依据：Chief 2026-10-14 派单"patrol 镜像能不能带 `src/**`？要只读取数 + 可接受/不可接受结论"。

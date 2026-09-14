# ADR-0012：合并算法工程与巡检工程为一个仓库

日期：2026-09-14
状态：已接受

## 背景

现场是两台机器、一个目标：导轨要**每 3 小时**自动巡检一轮，图片进算法管线，页面能看状态与历史。
今天这些分散在两个仓库：

- `mushroom_solution`（算法工程）：APScheduler 调度器（`src/scheduling/`，5 个 registrar）、
  视觉/决策/环境分析；现场以**一个容器**跑在 compose 项目 `mushroom_service` 里
  （`main.py` 同时拉起 FastAPI :5001、调度器、Streamlit :7005），发布走
  `build.sh` → `dist/src/` 混淆 → 推阿里云 registry → `deploy_server.sh`。
- `mushrooms`（巡检工程）：`patrol`（运动控制，**库内零网络调用**的基线）+ `deploy`（装配入口）
  + `measure` + `analysis`（接收 API）。

调度要驱动巡检，就必须让两边在一个发布单元里对齐：接口、配置、版本、排障。保留两个工程意味着
两套 CI、两套文档、两次发布、以及"调度改了但巡检没跟上"这类只能靠人对齐的漂移。

## 决定

1. **合并为一个仓库，但保留两个 uv 项目**：根目录仍是算法工程的项目（`pyproject.toml` +
   `uv.lock` **一个字节都不改**），巡检工程的四个包在 `patrol-workspace/` 下有自己的一份
   `pyproject.toml` + `uv.lock`（成员是上一级的 `../patrol`、`../deploy`、`../measure`、
   `../analysis`）。各留 `src/<pkg>` 布局。
   `mushroom` 作宿主仓库（历史更长、体积更大、且是现场正在跑的那个），
   `mushrooms` 的跟踪文件并入后**归档不删**（合并出问题时是回退点）。

   > **为什么不合成一个 workspace（实测否决，见下）**：把我们的包挂成根项目的 member 后，
   > `uv lock` 会**重算他们那份锁**——一次就动了 **84 个包**（`fastapi 0.133→0.136`、
   > `transformers 5.2→5.9`、`mlflow 3.10→3.12`、`redis 7.2→**8.0**`、`rich 14→15`…）
   > 并删掉 3 个。那是要进生产镜像的依赖集，把这种变化夹在"合并仓库"里发出去，出问题时
   > 没人能归因。两条补救路都不通：`uv lock` 默认的"沿用已有版本"在这种变更下不生效；
   > `--exclude-newer <他们构建日>` 也失败，因为他们硬钉的 `requests==2.28.1` 在镜像里
   > **没有上传时间**，解析器直接判定不可满足。**两份锁是这里唯一能证明"他们没被动过"的做法。**
   >
   > **补充（别误读成"只要修好锁就能合并成一个 workspace"）**：他们那份 `uv.lock` 在合并
   > **之前**就与 `pyproject.toml` 不自洽——在他们自己的提交（`a615909`）上、用 uv 0.12.10
   > 跑 `uv lock --check` 即失败（已用纯净 worktree 复现，与本次合并无关）。所以 uv 手里
   > 没有可用的"沿用偏好"，只能全量重解，这才是 84 个包一起动的直接原因。
   > 两份锁因此还多一层好处：**根项目得先自己自洽**（那是一次独立的、要单独验收的升级），
   > 在此之前任何触碰它解析的动作都会引发大面积升版。这条留给算法侧决定何时处理。

2. **包边界保留，`patrol` 的"零网络调用"基线不因合并而放宽**。这条是刻意的：
   平铺进 `src/` 之后，任何人一个 `from global_const import ...` 就能把 DB/网络拉进
   运动控制路径，而**没有任何机制会报警**。合并只改仓库归属，不改 import 边界。

3. **混淆构建不覆盖 `patrol`/`deploy`/`measure`**。这条由构建本身保证：`build.sh` 只
   `codeenigma obfuscate src`，我们的包在 `src/` 之外，天然不进混淆产物。两条硬理由：
   **取证**——出事故时要能拿 traceback 对着源码逐行看（`runs/*.jsonl` 记的就是行号与异常），
   混淆后现场那份就是废纸；**审计**——这台机器会自己走 4.5 米导轨，"它到底发了什么指令"
   必须能被人读懂。算法侧代码照旧混淆。

4. **两个镜像，同一个仓库，各带各的锁**。算法镜像不变；巡检用 `mushroom_patrol`，
   在 `patrol-workspace/` 里 `uv sync --frozen`（只装我们的包，不背 torch/opencv/mlflow）。
   避免"改算法要重建导轨控制镜像"，也避免两个依赖集互相牵动。

5. **巡检容器作为新服务加进 `mushroom_service` 那个 compose yml**（共用
   `mushroom_service_plant_backend` 网络、服务名互访）；配置目录与 `mushroom_service`
   **同级**：`/home/sysadmin/algorithm/mushroom_patrol/{configs,data,Logs}`。

6. **调度与巡检之间的接口是 HTTP，且必须快进快出**。调度侧加一个 registrar，
   `cron_kwargs={"hour": "*/3", "minute": M}`（该包只支持 `CronTrigger`，没有 interval 助手），
   job 只做一次 POST + 记一行日志；巡检侧收到请求**立刻回 202 + job id**，绝不阻塞——
   调度器的 `max_instances=1`、`misfire_grace_time=300s`，而一轮巡检要 **11 分钟**，
   job 里等结果必然导致下一次触发被判 misfire 丢掉。
   地址用算法工程既有的宿主惯例 `http://172.17.0.1:<port>`（与其 scada/redis/MinIO 一致）。

7. **发布链路暂各走各的**：算法侧的 `build.sh` / `deploy_server.sh` 不动；
   巡检镜像自行构建（WSL 构建后推 registry，或上传到目标机构建）并改 compose 发版。
   等巡检在现场跑稳再谈统一发布。

## 权衡与否决的选项

- **否决"平铺进一个 `src/` 大包"**：省事，但会把上面第 2 条的安全围栏拆掉。
- **否决"新建空仓库两边并入"**：两边 git 历史都断，且要把 torch/视觉那一大坨搬一次。
- **否决"一个镜像"**：导轨控制容器背上 torch/opencv/mlflow —— 镜像大、攻击面大、
  且每次算法迭代都要重建它。
- **否决"合成一个 uv workspace / 一份锁"**：实测会把算法侧 84 个包升版本（见第 1 条）。
  这个否决是这次合并里最重要的一条——它挡住的是"一次看起来无害的结构调整顺手把生产依赖
  全换了一遍"。
- **接受的代价**：仓库里有两份 `uv.lock`（根 + `patrol-workspace/`）。
  代价换来的是：算法侧的 `pyproject.toml` 与 `uv.lock` 逐字节不变，
  因此"合并没有改变生产依赖"这件事是**可验证的**，不是靠人保证的。

## 后果

- ADR-0011 的第 1 条（"新增 `deploy` 包"）与"后果"里关于 venv 部署的表述**由本 ADR 修订**：
  边界现在的守法是"包边界 + 不混淆"，部署形态改为容器。
- 一次结构整理，不是一次生产变更：算法侧的运行方式在巡检跑稳之前不变。
- 日常命令从仓库根挪到了两个地方：算法侧照旧；巡检侧的测试/lint/加锁都在
  `patrol-workspace/`（见该目录的 `README.md`）。

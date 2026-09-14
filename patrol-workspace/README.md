# 巡检工程（原 `mushrooms` 仓库）

这里是 `patrol` / `deploy` / `measure` / `analysis` 四个包，以及它们的聚合项目。
算法工程（`src/`、调度器、视觉、决策）在**仓库根**，是另一个独立的 uv 项目。

## 为什么是两个项目而不是一个 workspace

ADR-0012 记了完整原因，一句话版本：**把我们的包挂成根项目的 workspace 成员，`uv lock`
会重算算法工程那份锁——实测一次动了 84 个包**（`fastapi 0.133→0.136`、`redis 7.2→8.0`…），
而那是进生产镜像的依赖集。拆开之后，根目录的 `pyproject.toml` 与 `uv.lock` 逐字节不变，
"合并没有改变生产依赖"这件事因此**可验证**。

## 常用命令（都在本目录下执行）

```bash
uv sync                     # 建/更新本项目的环境（含四个包）
uv run pytest -q            # 巡检的测试（515 项）
uv run ruff check ../patrol ../deploy ../measure ../analysis
uv lock                     # 只在巡检依赖变化时；不要碰根目录那份锁
```

⚠️ **别把 `../capture` 传给 ruff**：那是 vendored 的原样组件（`extend-exclude` 里排除了），
显式传路径会绕过排除、报出 50 多条与本工程无关的历史告警。

## 目录约定

| 路径 | 内容 |
| --- | --- |
| `../patrol/` | 运动控制与巡检调度。**库内零网络调用**（ADR-0011 的安全基线），依赖注入靠 `links.Transport` |
| `../deploy/` | 部署胶水：真实 HTTP transport、`patrol-m1` 入口、systemd 单元、`deploy-fetch-room`、`deploy-flush-outbox` |
| `../measure/` | 图像 → 测量（numpy；含亮区质心微调依据） |
| `../analysis/` | prod 侧接收 API 与分析（FastAPI + SQLite） |
| `../docs/adr/` | 架构决策（0001–0013） |
| `../CONTEXT.md` | 领域词汇表（术语以它为准） |
| `../.scratch/` | 设计草稿与现场记录：`console-ui/`（巡检台规格与原型）、`prod-deploy/gap-list.md`（现场欠账清单） |

## 两条不能破的线

1. **`patrol` 里不许发网络请求**。这条有测试钉着，也是它能在现场被逐行审计的原因。
2. **`patrol`/`deploy`/`measure` 不进混淆**（`build.sh` 只混淆 `src/`，我们的包在 `src/` 之外，
   天然不受影响）。出事故时 traceback 必须能对着源码看。

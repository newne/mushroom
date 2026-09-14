# 文档索引

| 目录 | 内容 |
| --- | --- |
| `adr/` | 架构决策记录（0001–0016）。跨两个子系统的决策也放这里 |
| `agents/` | 给协作代理看的流程约定：issue 追踪、triage 标签、领域文档规范 |
| `patrol/` | **巡检工程**的文档与现场记录 |
| `patrol/console-ui/` | 巡检台规格（`spec.md`）、单页原型（`prototype.html`）、jsdom 回归（`verify.js`）、五张实施票 |
| `patrol/fmc4030-camera-scan/` | 一期扫描功能的规格与计划（`spec.md` / `plan.md` / 八张票） |
| `patrol/prod-deploy/` | 现场上机记录：`gap-list.md`（欠账清单）、`container-cutover.md`（容器上机切换手册）、Y 轴排查清单、采图服务修复、各次实测脚本与产物 |
| `technical/` `algorithms/` `business/` | **算法工程**的文档（既有，未改动） |

## 代码在哪

两个子系统各有自己的目录，文档不混进去：

| 子系统 | 代码 | 构建 | 文档 |
| --- | --- | --- | --- |
| 算法工程（调度 / 视觉 / 决策） | `../src/`、`../tests/` | `../pyproject.toml`、`../uv.lock`、`../docker/` | `technical/`、`algorithms/`、`business/` |
| 巡检工程（导轨 / 采图 / 接收 API） | `../patrol/`、`../deploy/`、`../measure/`、`../analysis/` | `../patrol-workspace/`（独立 uv 项目）、`../docker/Dockerfile.patrol` | `patrol/` |
| 巡检台前端（页面，独立产物） | `../web/console/` | nginx，见 `../docker/mushroom_solution.yml` 的 `mushroom_console_web` | `patrol/console-ui/`（规格与原型） |

其它顶层目录：`third_party/`（vendored：相机截图服务 `capture/`）、`archive/`（冻结归档 `legacy/`）、
`models/`、`data/`、`examples/`、`notebooks/`。

> 2026-09-14 前后端分离（ADR-0015）：巡检台页面从 `deploy/src/deploy/static/` 移到 `web/console/`，
> 由 nginx 服务（`/api/` 反代到后端），后端镜像不再打包静态文件。

> 2026-09-14 整理：原先散在仓库根的 `.scratch/`（设计草稿与现场记录）并入 `docs/patrol/`，
> vendored 的 `capture/` 进 `third_party/`，冻结的 `legacy/` 进 `archive/`。
> 算法工程的 `src/`、`tests/`、`docker/` **一律未动**——它们的路径写进了生产构建脚本
> （`docker/build.sh` 混淆 `src/`、Dockerfile 拷 `dist/src/`），挪动会直接打断现场发版。

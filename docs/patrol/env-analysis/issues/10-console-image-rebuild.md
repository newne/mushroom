# 10 — 把 console 的改动固化进镜像（当前是热替换）

**Spec:** ../spec.md §3、§4

**What to build:** prod 上 `mushroom_console` 容器里的 `deploy/console.py` 是**热替换进去的**（2026-10-12，`docker cp` + restart），镜像里还是旧版本。下一次任何人重建或重拉镜像，`/api/environment*` 就会消失，环境模式页随之退化成"查不到环境数据"。

**Blocked by:** 无

**Status:** resolved（2026-10-12 晚，WSL 构建并发布）

- [ ] 在 WSL 里按 `web/console/README.md`「上机（容器）」的配方构建并推送巡检镜像（构建上下文是仓库根）
- [ ] 服务器上 `docker compose --profile patrol up -d mushroom_console`，确认容器内的 `deploy/console.py` 带 `/api/environment`
- [ ] 复验：`curl -s localhost:8002/api/environment?zone=611&granularity=hour` 返回 rows
- [ ] 顺手核对 `mushroom_console_web` 的 `nginx.conf` 是否需要一起重建（它打在镜像里）

## Comments

- 2026-10-12 晚 **已解决**：WSL（Ubuntu-24.04，docker 29.1.3）里
  `docker build -f docker/Dockerfile.patrol` → 镜像 `mushroom_patrol:0.1.0-20261012181507-cde46ce`
  （digest `sha256:0347a171…`），同时打 `:0.1.0` 并推送；
  服务器 `.env` 的 `PATROL_IMAGE` 按仓库约定改指该 tag（改前备份 `.env.bak-*`）。
- 部署命令要说清：那目录里的编排文件叫 `mushroom_solution.yml`，**不是**默认名——
  `docker compose` 不带 `-f` 会回 `no configuration file provided: not found`。正确写法：
  `docker compose -f mushroom_solution.yml --profile patrol up -d mushroom_console`。
- 复验：容器 `Config.Image` 已是新 tag；容器内 `deploy/console.py` 自带
  `/api/environment`（2 处）与 `/api/operations`（1 处），**不再依赖 `docker cp` 热替换**；
  页面 origin 8002 上两个接口都 200。


- 2026-10-12 写票。**为什么当时没做**：本机没有 Docker（`docker` 命令不存在），镜像只能从 WSL 构建；页面与后端是两条发布路径（页面是挂载的、后端在镜像里），当时为了"今天就能看到页面"先热替换了后端。
- 热替换**不会**在容器重启时丢失（改的是容器可写层），但会在**镜像重建**时丢失。

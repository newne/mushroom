# 巡检台前端（`web/console/`）

巡检台**页面**，一个纯静态产物：没有构建步骤、没有 npm 依赖，改完直接生效。
后端（控制与查询 API）在 `deploy/src/deploy/console.py`，两者分开部署、分开回滚（ADR-0015）。

## 文件

| 文件 | 作用 |
| --- | --- |
| `index.html` | 页面本体（HTML + CSS + 原生 JS 全在一个文件里） |
| `nginx.conf` | 上机时用的 nginx 配置：发静态页 + 把 `/api/` 反代到后端 |
| `dev.py` | **本机开发用**的等价服务器（Python 标准库，不起容器） |

## 接口约定

页面里所有取数都写**绝对路径** `/api/...`，因此换了主机名、换了端口都不用改前端：

| 路径 | 用途 |
| --- | --- |
| `GET /api/status`、`/api/room`、`/api/stations`、`/api/grid` | 实时状态 |
| `GET /api/images?station_id=`、`/api/events` | 历史图像与日志 |
| `POST/DELETE /api/session`、`GET/POST /api/cmd`、`POST/DELETE /api/stop` | 手动控制（受 ADR-0013 约束：巡检中拒绝） |

**前端不持有任何控制逻辑**：它不认识控制器、SDK、库房主机路径，只发 HTTP。控制面接口
只有后端一处实现，页面永远不是第二条通往控制器的路（ADR-0011/0015）。

## 本机开发

```bash
# 1) 后端（另开一个终端）
cd patrol-workspace && uv run --frozen uvicorn deploy.console:create_app --factory --port 8001

# 2) 前端
python web/console/dev.py                                  # → http://127.0.0.1:8080/
python web/console/dev.py --api http://10.77.77.39:8001    # 指到真机的后端
python web/console/dev.py --host 0.0.0.0                   # 给平板/手机看
```

`dev.py` 与 `nginx.conf` 做同样两件事：未知路径回落到 `index.html`、`/api/` 原样转发并
**透传状态码**（403/409 是正常业务回答——"巡检中，预计 x 分钟后可用"——不能被糊成 500）。

## 上机（容器）

由 `docker/mushroom_solution.yml` 的 `mushroom_console_web`（nginx:alpine）服务，与后端
`mushroom_console` 同 compose 项目：

```bash
# 在仓库里：把前端产物拷到巡检的部署目录（与 configs/data/Logs 同级）
scp -r web/console sysadmin@<服务器>:/home/sysadmin/algorithm/mushroom_patrol/web
# 在服务器上
cd /home/sysadmin/algorithm/mushroom_service
docker compose --profile patrol up -d mushroom_console mushroom_console_web
```

上位机访问 `http://<服务器IP>:8002/`。**对外只暴露这一个端口**：后端 8001 只给前端容器
与算法侧调度器用（`/api/patrol/run`），不需要开放到现场局域网。

> 排障：页面空白 vs 接口 500 现在是**两件事**——`curl -s localhost:8002/` 看页面、
> `curl -s localhost:8002/healthz` 看后端；后者通、前者空说明产物没拷过去。

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
| `GET /api/growth?box_id=` | 生长曲线（console 代理 prod 的 `/growth`，保持单一 origin） |
| `GET /api/preview`（MJPEG 流）、`/api/preview/status`、`/api/preview/frame.jpg` | 实时画面（console 反代预览容器；巡检中拒绝，ADR-0017） |
| `POST/DELETE /api/session`、`GET/POST /api/cmd`、`POST/DELETE /api/stop` | 手动控制（受 ADR-0013/0016 约束：巡检中拒绝、急停是闩锁）。`GET /api/cmd` 额外带 `progress`：执行方在运动过程中写回的实时位置/速度（只认属于在飞指令且 ≤5s 新鲜的） |

**前端不持有任何控制逻辑**：它不认识控制器、SDK、库房主机路径，只发 HTTP。控制面接口
只有后端一处实现，页面永远不是第二条通往控制器的路（ADR-0011/0015）。

## 页面里有什么（现状）

顶栏有「实时 / 历史」两模式（左栏站位表两模式共用）：

| 模式 | 位置 | 内容 |
| --- | --- | --- |
| 公共 | 顶栏 | 状态药丸（巡检中 / 手动 / 已急停 / 待机）+ 位置 + 当前站进度 |
| 公共 | 门禁带 | 库房 · 入库日期 · 第几天 · 今天能不能巡检（现场第一疑问） |
| 公共 | 左栏 | 按层分组的站位列表（导轨图已移去中栏，见下） |
| 实时 | 中栏 | **实时画面**（接管自动开、放开自动关；点动按钮在右栏，两者同屏）· **抓拍**与「看画面」同卡 · **导轨平面图**（全宽放大：点击任意位置填坐标设目标，点站点 = 选中站位，悬停显坐标，Z 方向放大显示）· 上一轮 / 本轮状态 · 选中站位的历史图像（本地待同步 + prod 双源） |
| 实时 | 右栏 | **急停（常驻）** · 接管/放开 + 会话倒计时 · 定位（二次确认）/回零 · 点动（0.5/1/5/10）· 机器信息 · 事件日志 |
| 历史 | 中栏 | 时间轴（按日期分组、失败帧标灰）· 大图（1×/2×/4× + 滚轮 + 拖拽）· 生长曲线（SVG，长度/伞盖两条线 + 最近测量值表） |
| 历史 | 右栏 | 筛选（日期范围 / 角度档，只列数据里真实存在的档）· 机器信息 · 事件日志 |

页面是浅色固定工作台：1920×1080 为主目标，1280×800 下三栏仍保持核心控制可见且不产生横向滚动。
当前位置、目标站位、距离和当前动作是可信读数：**运动中**以执行方写回的实时位置/速度为准
（`progress.json`，标「控制器实时」，到位即清、id 对不上或 5 秒没更新都不显示），其次是控制器实读、
巡检站位推定、最后一次成功目标；无法确认时明确显示未知，不把未知位置当成 0 mm。坐标一律两位小数。
页面上的补光灯开关已于 2026-09-17 删除（现场无实际作用）；后端 `lamp` 指令保留——抓拍链路内部
仍在用（拍前开、拍完关），急停闩锁下「关灯」依旧放行。

异步行为有两层保护：状态和命令查询单飞；切站、历史图像、生长曲线和预览模式切换会丢弃过期
响应。请求超时或接口离线会在操作区显示可读错误。动态接口字段在进入 HTML/SVG 前转义，图像
URL 只接受 `http`/`https`。

手动面的行为边界（越界在浏览器侧拦、403/409 话术原样显示、`Esc` 只中断在飞的那条、
放开会话 = 回零 + 撤权）见 `docs/patrol/console-ui/spec.md` §10.4/§10.5 与 ADR-0016；
历史模式的现状与缺口见 §10.6；实时画面见 §10.7 与 ADR-0017
（页面里**只有 `/api/preview`**——相机地址与口令不出现在任何浏览器可见的地方）。
回归脚本：`docs/patrol/console-ui/verify-page.js`（jsdom，**101 项断言**）。

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

前端由我们自建的镜像 `mushroom_console_web` 服务（`docker/Dockerfile.console-web`：
nginx + 打进去的 `nginx.conf`），与后端 `mushroom_console` 同 compose 项目：

```bash
# 1) 构建并推送（在 WSL 里，构建上下文是仓库根）
REG=registry.cn-beijing.aliyuncs.com/ncgnewne
docker build -f docker/Dockerfile.console-web -t $REG/mushroom_console_web:0.1.0 .
docker push $REG/mushroom_console_web:0.1.0

# 2) 把前端产物拷到巡检的部署目录（与 configs/data/Logs 同级）
scp -r web/console sysadmin@<服务器>:/home/sysadmin/algorithm/mushroom_patrol/web

# 3) 在服务器上起两个容器
cd /home/sysadmin/algorithm/mushroom_service
docker compose --profile patrol up -d mushroom_console mushroom_console_web
```

实时画面还要多一个容器（同一个巡检镜像的 `preview` 角色，无宿主端口；页面通过 console
的 `/api/preview` 拿流）：

```bash
docker compose --profile patrol up -d mushroom_preview
```

上位机访问 `http://<服务器IP>:8002/`。**对外只暴露这一个端口**：后端 8001 只给前端容器
与算法侧调度器用（`/api/patrol/run`），不需要开放到现场局域网。

> **改页面 vs 改配置**：页面是挂进去的 ⇒ 改 `index.html` 只要重拷一次（`docker cp`
> 或 scp 都行，不必重建镜像）；`nginx.conf` 打在镜像里 ⇒ 改它要重建并推送镜像。
> 这条分界是刻意的（配置即代码、页面是数据），别把两边都做成挂载。
>
> 排障：页面空白 vs 接口 500 现在是**两件事**——`curl -s localhost:8002/` 看页面、
> `curl -s localhost:8002/healthz` 看后端；后者通、前者空说明产物没拷过去。

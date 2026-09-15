# ADR-0017：实时预览用 RTSP→MJPEG 转码 + 同源代理（修订 spec §1 的"不做连续视频预览"）

日期：2026-09-15
状态：已接受
背景：`docs/patrol/console-ui/spec.md` §1 当初明确写了"不做连续视频预览（ADR 未立项，
`capture` 只有单次抓帧）"。现场用过之后提出：**手动接管时看不见画面**，点动/定位只能靠
坐标猜——而"把相机挪到那一框前面"这件事，眼睛比数字有用得多。

现场实测（2026-09-15，库房主机）：

| 事实 | 证据 |
| --- | --- |
| 采图服务**不能**当预览用 | 单次抓图 ≈5.2 s，且必然落一个文件（`storage=local/cloud`，没有"只回图不落盘"） |
| 相机**没有** HTTP MJPEG | `/ISAPI/Streaming/...` 全 404；服务自称 `H264DVR 1.0` |
| 相机**有** RTSP，且凭据可用 | 554 开着；用 `stations.yaml` 的 admin 凭据 Digest 认证，13 条常见路径全部 `200+SDP`（这台 DVR 不挑路径） |
| 相机**允许并发**会话 | 同时保持 3 路 DESCRIBE 全 200（预览占一路不会挤掉别人） |
| 页面**能**直接显示 MinIO 图 | `cloud_url` → 200、2880×1616 JPEG；但这与"实时"无关 |
| 宿主**没有** ffmpeg | 所以转码要放进我们自己的容器 |

## 决定

1. **预览 = RTSP 拉流 → ffmpeg 转 MJPEG → 同源代理 → 浏览器 `<img>`**。
   - 转码由**新的容器角色 `preview`** 承担（同一个巡检镜像、不同角色，沿用 ADR-0012 §4
     的"一个镜像多个角色"）：`ffmpeg -rtsp_transport tcp -i <rtsp> -vf scale=640:-2 -r 5
     -f image2pipe -c:v mjpeg -`，再把帧用 `multipart/x-mixed-replace` 广播给浏览器。
   - 分辨率/帧率默认 **640 宽 @ 5 fps**（够看清"对着哪儿"，CPU 与带宽都可控；这台 DVR 不挑
     路径、选不了子码流，所以只能解主码流再缩放）。
2. **浏览器只连 console**：页面用 `<img src="/api/preview">`，console 反代到预览容器。
   相机地址与凭据**不出现在任何浏览器可见的配置里**（与 ADR-0003 的单一 origin 一致）。
3. **只在接管时开**：预览不是常驻服务。页面在"接管"后自动打开、放开时关掉；一次只服务
   少量观看者（服务端广播，慢客户端丢帧而不是拖住所有人）。
4. **巡检进行中拒绝预览**（409 + 预计可用时间，复用 ADR-0013 的话术）：那一轮要用相机抓 60 张，
   尽管实测并发够用，也不去赌"两路同时拉流时抓图不受影响"。
5. **凭据只有一个来源**：`stations.yaml` 的 `camera_user`/`camera_pwd`（预览容器只读挂载它），
   与抓拍用的是同一份；URL 里的口令在任何日志里都要打码。

## 决定理由

- 现场提出的需求是"接管时要能看见画面"，而采图服务 5 秒一张的节奏对"边挪边看"没有意义；
  RTSP 是相机本来就有的能力，不需要动厂商 SDK 会话（那一路归采图服务）。
- 用 MJPEG 而不是 HLS/WebRTC：`<img>` 原生支持 `multipart/x-mixed-replace`，页面**零 JS 播放器、
  零 vendor 依赖**（spec §8 的"零构建"约束仍然成立），延迟 <1 s；代价是带宽比 H.264 高，
  但 640×360@5fps 在内网约 1–2 Mbps，可接受。
- 放在**同一个镜像的另一个容器**而不是塞进 `mushroom_patrol`：ffmpeg 崩了只重启预览，
  控制器那边不受影响；也不给控制面镜像增加转码这种无关负担（代价是镜像多约 150 MB）。

## 后果

- 巡检镜像多一个 `preview` 角色与 ffmpeg 依赖（`docker/Dockerfile.patrol` 里显式注明）；
  compose 多一个 `mushroom_preview` 服务（同镜像、`profiles: ["patrol"]`）。
- **相机并发**：预览占一路 RTSP。实测这台 DVR 允许 ≥3 路，但现场若换相机/换固件需重测
  （`docs/patrol/prod-deploy/probe-rtsp.py` 与并发探测脚本可直接复跑）。
- 老系统整点抓 6 台走的是 8000 端口的 SDK，与本方案不冲突；但**同一时刻**预览 + 抓图 + 老系统
  三方并发没有做过长时间实测，属于一期已知边界。
- 预览画面**不留存、不进历史**：它是"看一眼"，照片仍然只有一次性的「抓拍」会落索引与 MinIO。

## 落地形态（2026-09-15 实现，供对照）

| 位置 | 内容 |
| --- | --- |
| `deploy/preview.py` | 容器角色：帧切分 / 广播（队列 3，慢客户端丢帧）/ ffmpeg 监督（1·2·5·10 s 退避）/ `create_app`（`/healthz`、`/stream.mjpg`、`/frame.jpg`）/ `main()`（`--rtsp/--stations/--port/--scale/--fps`） |
| `deploy/console.py` | `GET /api/preview`（MJPEG 反代）、`/api/preview/status`（能不能看 + 上游健康，**永不 500**）、`/api/preview/frame.jpg`（单帧） |
| `web/console/index.html` | 中栏第一张「实时画面」卡：接管开 / 放开关 / 轮内关、本轮结束自动恢复、不可用时每 2 秒重试并原样显示后端话术、帧龄 >3 秒自动重连 |
| `docker/` | `Dockerfile.patrol` 装 ffmpeg；`patrol-entrypoint.sh` 的 `preview` 角色；compose 的 `mushroom_preview`（健康判据用容器内 `/healthz`——503 = 还没有帧）；`web/console/nginx.conf` 对 `/api/` 关掉 `proxy_buffering`（否则 MJPEG 会被攒成"延迟十几秒"） |

两处**实现时**才明确的决定：

1. `/api/preview` 会**先打开上游、看到状态码再返回**：`StreamingResponse` 一旦返回状态码就
   定死了，上游 503（正在连相机）会变成"200 + 空流"，页面只看到一张永远不出现的图。
2. §4 的"轮内拒绝"由**服务端**兜底：console 每转发 8 块（5 fps 下约 1.5 秒）回头检查一次，
   轮次一开始就把**已经开着**的流也断掉——不能指望页面自觉（标签页卡住、页面是老版本时，
   规则就没人执行了）。页面那一侧同时会主动关掉并把理由显示出来。

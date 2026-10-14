# 04 — preview 的 tag 漂移：代码一致，只是镜像标签不同步

**归属：** `deploy/src/deploy/preview.py` 在 step code 的独立文件线上，**并回 tag 的动作归发版官**；
本票由发版官开。
**开票：** 发版官（2026-10-13 现场只读实测）
**Status:** resolved（结论已定：并回只是重建时改用主线 tag，不需要改代码）
**优先级:** 低（止血已完成，见 §3）

## 1. 背景

2026-10-13 对账时发现：一个 `PATROL_IMAGE` 管三个容器，而现场三个容器的 tag **不同步**——

| 容器 | 修复前的 tag |
| --- | --- |
| `mushroom_patrol` / `mushroom_console` | `0.1.0-20261013144515-b64027b` |
| `mushroom_preview` | `station-calibration-20261008-l3z132-l4z155-c08y40`（2026-10-08 17:50 构建） |

成因是历史做法：`mushroom_preview` 多次用 `--force-recreate --no-deps` 单独重建（ADR-0017 补记、
`container-cutover.md` §14/§17），而 `PATROL_IMAGE` 是三个服务共用的 —— 于是"谁发版谁顺手把
preview 也换掉"，而 preview 那次换与不换从来不是同一个决定。

## 2. 关键证据：**这不是代码漂移**

现场实测（LF 归一化 sha256）：

| 来源 | `deploy/src/deploy/preview.py` |
| --- | --- |
| `mushroom_preview` 容器内（`station-calibration-…` 镜像） | `8eab17e0d90e658c36c2daa298bcdb60a47c5b63d39dd4fffb6043c86880015d` |
| `mushroom_patrol` 容器内（`b64027b` 镜像） | **同上 `8eab17e0…`** |
| 仓库工作区 | **同上 `8eab17e0…`** |
| 仓库 HEAD | **同上 `8eab17e0…`** |

`grep -c _watchdog` 两边都是 **2**。⇒ 预览容器跑的就是主线那一份代码（含 §17 的看门狗修复），
差别只在**镜像里的别的模块**（`console.py` / `m1.py` 等），而 preview 角色只用 `preview.py`。

## 3. 已做的止血（2026-10-13，发版官）

在 `docker/mushroom_solution.yml` 里给 preview **拆出独立变量**：

```yaml
image: "${PATROL_PREVIEW_IMAGE:-registry.cn-beijing.aliyuncs.com/ncgnewne/mushroom_patrol:0.1.0}"
```

现场 `.env` 新增一行钉住它当前实跑的 tag；`.env` 的 `PATROL_IMAGE` 同时回写为当前实跑值。
已用 `docker compose -f mushroom_solution.yml --profile patrol config` 验证解析（exit 0，
解析出的镜像清单里 preview 与 patrol/console 已分开），**未 pull、未重建**。

这样"改 patrol 顺手把 preview 换掉"这条路径被切断；回滚就是删掉那一行（回落 `PATROL_IMAGE`）。

## 4. 并回同一个 tag 的做法（下次重建 preview 时执行）

代码一致 ⇒ **不需要任何代码改动**，只需让 preview 也用主线 tag：

```bash
cd /home/sysadmin/algorithm/mushroom_service
cp -a .env .env.bak-$(date +%Y%m%d-%H%M%S)
# 方式一（推荐：直接删掉那一行，回落 PATROL_IMAGE）
sed -i '/^PATROL_PREVIEW_IMAGE=/d' .env
# 方式二（等价：把它写成与 PATROL_IMAGE 相同的值）
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_preview
# 验收：/api/preview/status available:true、frames 递增、restarts 不因重建以外原因变化；
#       页面 /api/preview/frame.jpg 200 image/jpeg
```

**做这件事的时机**：下次因为别的原因需要重建 preview 时顺手做，不为它单独停机一次。
（若要单独做，注意 `mushroom_preview` 重启会中断正在看实时画面的人，约 10 秒。）

## 5. 为什么不再新增机制

- 不新增 service（用户红线：非必要不加 compose service）；
- 不给 preview 单开一个 registry 仓或新镜像名 —— 拆变量已经足够表达"这两个 tag 可以不同步"。

## Comments

- 2026-10-13 发版官现场只读取证开票。Chief 原话要求："这是止血，随后你要跟巡检手/step code
  确认 `preview.py` 是否已被主线覆盖，确认后把它并回同一个 tag（开一张小票，别忘）"。
  本票即那张小票；§2 的 sha 比对就是"已被主线覆盖"的答案——**是，逐字节相同**。
- 待巡检手/step code 确认的是**归属**而不是内容：`preview.py` 在 step code 的文件线上，
  并回 tag 这个动作（§4）由发版官执行，动它之前知会一声即可。
- 2026-10-13 21:0x（发版官，Chief 批 (a′)）：`PATROL_IMAGE` 已改指新 tag
  `mushroom_patrol:0.1.0-20261013204311-89e4a54`，并**只重建了 `mushroom_analysis` 与
  `mushroom_patrol`**；**`mushroom_console` 刻意留在旧 tag `b64027b`**（本次只需要 analysis 的
  DDL 与 patrol 的 meta 字段，动 console 只会无谓扩大停机面）。
  ⚠️ **这就是本票 §1 那个"一个变量管多个容器"的隐式耦合**：`PATROL_IMAGE` 同时喂
  patrol / console / analysis，所以"谁重建谁换 tag"。**下次有人对 console 执行
  `docker compose up -d` 会顺手把它带到新 tag**——那是预期漂移，不是故障；要三者一致需单独批准一次
  console 重建（preview 已有独立变量，不受影响）。详见票 06 §H。

## 6. 第二条同形状的交付通道：**页面靠 scp、后端靠镜像**（2026-10-14）

`docker/mushroom_solution.yml:266-277`：`mushroom_console_web` 的页面**不打进镜像**，是宿主
bind mount `../mushroom_patrol/web:/usr/share/nginx/html:ro`（注释写着"改页面只需 scp 一次"）。
⇒ **页面源码在仓库、运行的字节在宿主**；而**后端 `console.py` 在镜像里**。两条通道生效时机不同
⇒ 这是今天**第四条**"仓库有 ≠ 生产有"（前三条：`capture.py` bind-mount、measure/framing、analysis 加列）。

**实测（2026-10-14 10:0x，只读）**：

| 项 | 值 |
| --- | --- |
| 宿主页面 `.../mushroom_patrol/web/index.html` | 172 811 B / 3174 行 / sha256 `793c023c…` / mtime **10-13 14:48** |
| 仓库 HEAD blob `web/console/index.html` | **219 286 B / 3922 行** / sha256 `acdefd52…`（工作区对该文件**无未提交改动**，已是 HEAD 内容） |
| 票 12 的「**未核查**」文案 | 宿主页面 **0 命中** ⇒ **界面师那批 UI 与票 12 都还没进站点** |
| 现役 console 路由 | 旧镜像 `b64027b` 的 `/api/measurements` = **404**（路由不存在） |
| 字段透传 | `/api/rounds` **原样透传** `suspect_duplicate` / `dup_groups` / `content_check_error` / `n_duplicate` / `n_frames_recorded` ✓（不滤） |

### 6.1 发布顺序（**不可颠倒**；Chief 2026-10-14 预批）

**为什么不能先同步页面**：新页面要调 `/api/measurements`，而旧后端没有这条路由 ⇒
**只同步页面会把"曲线"变成"查不到测量数据"**（假故障）。所以：

1. 备份宿主页面（带时间戳）+ 记下 console 当前 tag；
2. **先重建 `mushroom_console`**（同一 `PATROL_IMAGE` = `89e4a54`）；验收：`/api/measurements` **200 且响应带 `rows`**；
3. **再同步页面**：只传 `web/console/index.html`（`verify-page.js` 是测试脚本、不进站点）；
   上传后**核远端 sha256 = 本地**；
4. 前后健康检查（六容器）、挑**无轮次在飞**的窗口；
5. 任一步红即**停手回报**。

本轮**不做 `CONSOLE_IMAGE` 解耦**（另一次改动）；只把这条交付通道记在这里，防下次又踩。

### 6.2 回滚路径（本次发布）

```bash
# 页面（宿主机）：
cp -a /home/sysadmin/algorithm/mushroom_patrol/web/index.html.bak-<ts> \
      /home/sysadmin/algorithm/mushroom_patrol/web/index.html
# 后端 console（单容器；PATROL_IMAGE 是共享变量，走完必须复原）：
cd /home/sysadmin/algorithm/mushroom_service
cp -a .env.bak-<ts> /tmp/env.now          # 记下发版前那份（PATROL_IMAGE=89e4a54）
#   把 .env 的 PATROL_IMAGE 临时改成 b64027b，然后：
docker compose -f mushroom_solution.yml --profile patrol up -d --force-recreate --no-deps mushroom_console
cp -a /tmp/env.now .env                   # ★ 立刻复原新 tag，否则 patrol/analysis 下次重建会漂回旧版
```

停机面：**只有 console 后端 :8001 约 10–20 s**；页面 8002 由 `console_web` 供应、**不重建、不受影响**。
但 `PATROL_RUN_URL=http://mushroom_console:8001/api/patrol/run` 是**算法侧触发巡检的入口**
⇒ **别压在轮次边界**（轮次约每 3 小时、在 :27）。

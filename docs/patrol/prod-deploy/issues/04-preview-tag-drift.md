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

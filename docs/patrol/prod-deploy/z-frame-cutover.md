# Z 框架迁移：上机清单（2026-10-10，ADR-0019）

**这一步会动机构**（要重新回零），必须有人在机器旁。目标：把 Z 的坐标框架从
"**底端为 0、向上为正、回零向下**"改成"**顶端为 0、向上为正、回零向上**"
（回零方向反过来，`Z=0` 随之从底端移到顶端；行程由 `0…212` 改为 `−212…0`）。

前置阅读：`docs/adr/0019-z-homes-up-origin-at-top.md`（为什么回零方向与行程必须一起改）。

> ⚠️ **顺序不能换**：控制器里现在还是**旧框架的零点**（底端）。在重新回零之前，新代码
> 算出来的 Z 目标全部在反方向——所以"部署代码 + 迁移站位表"与"回零"之间**不许夹任何
> 绝对定位动作**（点动也一样，点动也是按当前坐标算落点的）。

---

## 0. 先看机器，确认方向（决定性一步）

上新代码**之前**，先在**当前**部署上按一次「回零」，**眼睛盯着 Z 滑台**：

- 若 Z **往下**走到底：方向确实反了，按本清单改（`homeDir` 2→1）。
- 若 Z **往上**走：**停**——说明当前已经是"向上回零"，别改，先回来对口径。

> 这一步是 `container-cutover.md` §20 教训的复现：方向只能由**现场看轴往哪走**定论，
> 不能从"撞到哪个限位"反推原点。

## 1. 先备份

```bash
BASE=/home/sysadmin/algorithm
cd $BASE/mushroom_patrol
cp configs/stations.yaml configs/stations.yaml.bak-zframe-up-$(date +%Y%m%d)
cp configs/room.yaml     configs/room.yaml.bak-zframe-up-$(date +%Y%m%d)
```

## 2. 开发机：改代码、推镜像与页面

```bash
cd /mnt/d/code/mushroom
REG=registry.cn-beijing.aliyuncs.com/ncgnewne
TAG=0.1.0-<YYYYMMDDHHMMSS>-<sha>
docker build -f docker/Dockerfile.patrol -t $REG/mushroom_patrol:$TAG .
docker push  $REG/mushroom_patrol:$TAG
scp -r web/console/* sysadmin@10.77.77.39:$BASE/mushroom_patrol/web/    # 平面图第 1 层在最上
```

## 3. 库房主机：迁移站位表的 Z 坐标

```bash
cd $BASE/mushroom_service
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol

# 3a. 先看换算结果（不落盘）：第 1 层应当由 10.0 变成 −57.0、第 4 层由 155.0 变成 −202.0
docker run --rm -v $BASE/mushroom_patrol/configs:/app/configs \
  $REG/mushroom_patrol:$TAG patrol-migrate z-frame \
  --stations /app/configs/stations.yaml --dry-run

# 3b. 确认无误后落盘（自动备份到 stations.yaml.bak-z-frame-<时间戳>）
docker run --rm -v $BASE/mushroom_patrol/configs:/app/configs \
  $REG/mushroom_patrol:$TAG patrol-migrate z-frame \
  --stations /app/configs/stations.yaml
```

**判据**：第 1 层（S1xx）`z ≈ −57`、第 2 层 ≈ −80、第 3 层 ≈ −152、第 4 层 ≈ −202。
若第 1 层落在 −202，说明**层序没翻**（见 ADR-0019 §决定.4），**停下来别继续**。

## 4. 库房主机：把控制器里的 Z 软限位改成 `−212…0`

控制器自带一层软限位（ADR-0008）。旧框架写的是 `0…212`，新框架必须改成 `−212…0`——
否则控制器会**截断**目标却返回成功（长行程静默走短），或直接拒绝。

用 `patrol-debug` 交互式做（`para` 看现值、写回后再 `para` 确认）：

```bash
docker run --rm -it --entrypoint patrol-debug \
  -v $BASE/mushroom_patrol/lib:/opt/fmc-lib:ro \
  -e FMC4030_LIB_PATH=/opt/fmc-lib/libFMC4030_2009_1.so \
  $REG/mushroom_patrol:$TAG
# 交互：para → 核对轴 2 软限位；写回后再 para 确认 −212…0
```

也可用 `docs/patrol/prod-deploy/set-z-soft-limits.py`——它按 `motion_profile.M1.z` 写回
**有效范围**，自动得到 `−212…0`（负值一侧以"取反后的原始值"写入，说明书里 `-1` 表示取消）。

> 若这一步暂时做不了：`patrol-serve --dry-run` 与巡检启动前的 `check_soft_limits()`
> 会**拒绝启动**（fail-closed），比"带着旧软限位跑"安全得多——但也就跑不了巡检。

## 5. 回零（会动机构：Z 向上到顶、Y 向左到底）

```bash
docker run --rm -it --entrypoint patrol-debug \
  -v $BASE/mushroom_patrol/lib:/opt/fmc-lib:ro \
  -e FMC4030_LIB_PATH=/opt/fmc-lib/libFMC4030_2009_1.so \
  $REG/mushroom_patrol:$TAG
# 交互里：home   → 两轴回零（Y 往左、Z 往**上**）
#         status → 两轴都应当是 0.000
```

**盯这三件事**（这是整条迁移的验收判据）：

1. **Z 是往上升的**——若它往下走，立刻按急停：说明 `homeDir` 改错了方向。
2. 回零结束后 `status` 里 **Y = 0.000、Z = 0.000**（两轴落点都是原点）。
3. `para` 里 Z 的软限位是 `−212…0`。

然后做一次**方向核对**（小步、慢速、人在旁边）：

```
jog Z -5     → 机构应当**往下**走 5mm（Z 减小 = 向下）
jog Z 5      → 回到 0
jog Y 5      → 往右 5mm
jog Y -5     → 回到 0
```

## 6. 起服务并验收

```bash
cd $BASE/mushroom_service
docker compose -f mushroom_solution.yml --profile patrol up -d \
  mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol ps
```

- 页面 `http://10.77.77.39:8002/`：平面图**第 1 层在最上**、点动按钮仍写着 `Z +（上）`。
- `GET /api/grid` 应当是 `z_min: -212, z_max: 0`。
- 接管后点一次 `Z −（下）` 0.5mm：机构往下、页面坐标读数**变小**（更负）。
- 真正跑一轮（需要门禁允许或临时用 `room.test.yaml`）：核对 **S101 去的是最上面那一层**、
  **S401 去的是最下面那一层**。这一条是"层序"唯一的端到端判据。

## 7. 回滚

```bash
cd $BASE/mushroom_patrol/configs
cp stations.yaml.bak-z-frame-<时间戳> stations.yaml     # 数据回滚
# 代码回滚：把镜像 tag 换回迁移前的构建，并把控制器 Z 软限位改回 0…212
```

> 回滚代码**必须同时**回滚站位表与控制器软限位，四者是同一套框架的四份表达
> （`motion_profile` / 站位表 / 控制器软限位 / 页面）。

---

## 8. 历史记录（供排障，勿照做）

### 8.1 2026-09-15（ADR-0018，已被推翻）

> 那次把框架改成"顶端为 0、**向下为正**、回零向上"（`homeDir=2`）。结论在 2026-10-09 被
> `7169156` 推翻，2026-10-10 又被 `3537d00` / `40c8434` 翻了两次，最终以 **ADR-0019** 定稿。
> 下面原文保留，只作排障参考。

按 §1–§5 一次走完，**没有出现意外动作**。沿途拿到的证据比预想的更有说服力：

| 步骤 | 实测 |
| --- | --- |
| 迁移前读位置 | `Y=0.00 Z=-210.10` —— Z 在旧框架里是 −210.1，正好是"距底部 210 mm"。旧框架**把底部当 0、越往上越是负值**，与现场判断完全一致（码放在顶部附近） |
| 站位表迁移 | `patrol-migrate z-frame --dry-run` 显示 S101 `-21.2 → +21.2`；落盘后 60 站、自动备份 `stations.yaml.bak-z-frame-20260915-154143` |
| 回零 | `home` 成功，两轴落点 `Y=0.00 Z=0.00`；Z 只走了约 2 mm（从距底 210.1 到顶 212）——与"它本来就在顶部附近"吻合，也说明新方向确实是**往上**找开关 |
| 控制器软限位 | `set-z-soft-limits.py --go`：轴2 由"已取消（原值 (220, -1)）"写成 `0…212`，**读回复核通过** |
| 方向核对 | `jog Z 40` 实测机构**往下**走 40 mm（用户现场确认），`jog Z -40` 回到顶 |
| 反向余量 | 40 mm 往返后落点是 **0.32 mm**（不是 0.000）：相对运动往返的机械回差。不影响层距（42.4 mm），但**绝对定位前应当回零**——这也是"放开接管=回零"这条设计的现实理由 |
| 迁移后接口 | `/api/grid` → `z 0…212`；`/api/stations` → S101 `z=21.2`；页面 200，平面图第 1 层在最上、点动按钮写着 `Z +（下）` |

### 8.2 2026-10-10（本次，ADR-0019）

（上机实做后补：回零方向、软限位读回、迁移后层 z、`/api/grid`、一轮层序核对。）

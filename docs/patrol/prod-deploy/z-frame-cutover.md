# Z 框架迁移：上机清单（ADR-0018）

**这一步会动机构**（要重新回零），必须有人在机器旁。目标：把 Z 的坐标框架从
"底部为 0、向上为正"改成"**顶端为 0、向下为正**"（与 Y 同构：原点都在靠近电机端）。

前置阅读：`docs/adr/0018-z-axis-origin-at-motor-end.md`（为什么必须一起改方向与范围）。

> ⚠️ **顺序不能换**：控制器里现在还是**旧框架的零点**（底部）。在重新回零之前，
> 新代码算出来的 Z 目标全部会在反方向——所以"部署代码 + 迁移站位表"与"回零"之间
> **不许夹任何绝对定位动作**（点动也一样，点动也是按当前坐标算落点的）。

---

## 0. 先备份

```bash
BASE=/home/sysadmin/algorithm
cd $BASE/mushroom_patrol
cp configs/stations.yaml configs/stations.yaml.bak-zframe-$(date +%Y%m%d)
cp configs/room.yaml     configs/room.yaml.bak-zframe-$(date +%Y%m%d)
```

## 1. 开发机：改代码、推镜像与页面

```bash
cd /mnt/d/code/mushroom
REG=registry.cn-beijing.aliyuncs.com/ncgnewne
docker build -f docker/Dockerfile.patrol -t $REG/mushroom_patrol:0.1.0 .
docker push $REG/mushroom_patrol:0.1.0
scp -r web/console/* sysadmin@10.77.77.39:$BASE/mushroom_patrol/web/    # 页面：平面图与点动标注
```

## 2. 库房主机：迁移站位表的 Z 坐标

```bash
cd $BASE/mushroom_service
docker compose -f mushroom_solution.yml --profile patrol pull mushroom_patrol

# 2a. 先看换算结果（不落盘）：第 1 层应当从 −21.2 变成 +21.2
docker run --rm -v $BASE/mushroom_patrol/configs:/app/configs \
  $REG/mushroom_patrol:0.1.0 patrol-migrate z-frame \
  --stations /app/configs/stations.yaml --dry-run

# 2b. 确认无误后落盘（自动备份到 stations.yaml.bak-z-frame-<时间戳>）
docker run --rm -v $BASE/mushroom_patrol/configs:/app/configs \
  $REG/mushroom_patrol:0.1.0 patrol-migrate z-frame \
  --stations /app/configs/stations.yaml
```

**判据**：第 1 层（S1xx）`z` 在 **21.2** 附近、第 5 层（S5xx）在 **190.8** 附近。
若第 1 层落在 190.8，说明换算用错了（见 ADR-0018 的对照表），**停下来别继续**。

## 3. 库房主机：把控制器里的 Z 软限位改成 `0…212`

控制器自带一层软限位（ADR-0008）。旧框架下它写的是 `-212…0`（Z 的 `softLimitMax`
读回是 `-1`，即取消），新框架必须改成 `0…212`——否则会出现两种坏情况：
控制器**截断**目标却返回成功（长行程静默走短），或直接拒绝。

用 `patrol-debug` 交互式做（`para` 看现值、按说明书 §三.4 写回）：

```bash
docker run --rm -it --entrypoint patrol-debug \
  -v $BASE/mushroom_patrol/lib:/opt/fmc-lib:ro \
  -e FMC4030_LIB_PATH=/opt/fmc-lib/libFMC4030_2009_1.so \
  $REG/mushroom_patrol:0.1.0
# 交互里：para  → 核对轴 2 的软限位；写回后 `para` 再读一次确认 0…212
```

> 若这一步暂时做不了：`patrol-serve --dry-run` 与巡检启动前的 `check_soft_limits()`
> 会**拒绝启动**（fail-closed），这比"带着旧软限位跑"安全得多——但也就跑不了巡检。

## 4. 回零（会动机构：Z 向上到顶、Y 向左到底）

```bash
docker run --rm -it --entrypoint patrol-debug \
  -v $BASE/mushroom_patrol/lib:/opt/fmc-lib:ro \
  -e FMC4030_LIB_PATH=/opt/fmc-lib/libFMC4030_2009_1.so \
  $REG/mushroom_patrol:0.1.0
# 交互里：home   → 两轴回零（Y 往左、Z 往上）
#         status → 两轴都应当是 0.000
```

**盯这三件事**（这是整条迁移的验收判据）：

1. **Z 是往上升的**——若它往下走，立刻按急停：说明 `homeDir` 改错了方向。
2. 回零结束后 `status` 里 **Y = 0.000、Z = 0.000**（两轴落点都是原点）。
3. `para` 里 Z 的软限位是 `0…212`。

然后做一次**方向核对**（小步、慢速、人在旁边）：

```
jog Z 5      → 机构应当**往下**走 5mm（Z 增大 = 向下）
jog Z -5     → 回到 0
jog Y 5      → 往右 5mm
jog Y -5     → 回到 0
```

## 5. 起服务并验收

```bash
cd $BASE/mushroom_service
docker compose -f mushroom_solution.yml --profile patrol up -d \
  mushroom_patrol mushroom_console mushroom_console_web mushroom_preview
docker compose -f mushroom_solution.yml --profile patrol ps
```

- 页面 `http://10.77.77.39:8002/`：平面图**第 1 层在最上**、点动按钮写着 `Z +（下）`。
- `GET /api/grid` 应当是 `z_min: 0, z_max: 212`。
- 接管后点一次 `Z +（下）` 0.5mm：机构往下、页面坐标读数**变大**；点动按钮旁的读数是
  真实行程度数（不是推导值）。
- 真正跑一轮（需要门禁允许或临时用 `room.test.yaml`）：核对 **S101 去的是最上面那一层**、
  S501 去的是最下面那一层。这一条是"层序"唯一的端到端判据。

## 6. 回滚

```bash
cd $BASE/mushroom_patrol/configs
cp stations.yaml.bak-z-frame-<时间戳> stations.yaml     # 数据回滚
# 代码回滚：把镜像 tag 换回迁移前的构建，并把控制器 Z 软限位改回 -212…0
```

> 回滚代码**必须同时**回滚站位表与控制器软限位，三者是同一套框架的三份表达。

---

## 7. 2026-09-15 实做记录（库房主机）

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

**顺带修掉的一个坑**：`patrol-debug` 的回零成功提示原本写死成"Y 负限位 / Z 正限位"——
方向改了以后它会**说谎**（这次实做就骗了一次，日志里写着"Z 正限位"）。现在文案从
`motion_profile` 派生，写死不了。


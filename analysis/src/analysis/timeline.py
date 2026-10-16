"""覆盖轨迹（票 02）：`rounds` 的逐轮 × `images` 的帧数，摊成「本批每一轮一格」。

**这里只有纯函数**（没有数据访问、没有 SQL）：取数在 `analysis.store`，装配在 `analysis.api`。
放这一层的理由与 `analysis.growth` / `analysis.environment` 一样——"格点长什么样"是可单测的
形状逻辑，混进 API 就只能对着 HTTP 测。

规格（docs/patrol/growth-timeline/spec.md）的三条硬规则里，落在**数据形状**上的是这两条：

1. **x 永远是真实时间戳**：格点带 `ts`，`day`（生长天数）由 `entry_date` 派生为第二刻度；
   端点**不产出**任何"第几个点"的序号——页面想按数组下标画都无从下手。
2. **`null ≠ 0`**：这一轮这个站位没有帧 ⇒ `has_frame=False` 且 `n_frames=None`（**不是 0**）。
   "没拍到"与"拍到 0 张"不是一件事：前者是缺口（页面断开、画灰），后者根本不存在。
"""

from __future__ import annotations

from datetime import date


def day_of(entry_date, ts) -> int | None:
    """生长天数：**入库当天 = 0**（与 `/setpoints/daily` 的 `growth_day` 同一口径）。

    认不出来的日期（缺 `entry_date`、`ts` 不是 ISO 日期前缀）⇒ `None`：
    **不猜**一个第几天摆上去，页面对 `None` 不画天数刻度。
    """
    start = _as_date(entry_date)
    when = _as_date(ts)
    if start is None or when is None:
        return None
    return (when - start).days


def _as_date(value) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def coverage_points(rows, *, entry_date) -> list[dict]:
    """逐轮格点，按真实时间升序。

    只收**能归属到轮次**的行（`round_id` 非空）：没有 `round_id` 的旧轮次，
    帧根本挂不到它头上，"这一轮没有帧"会是句假话——那种轮次由调用方计入 `notes`。
    """
    points: list[dict] = []
    for row in rows or []:
        round_id = row.get("round_id")
        ts = row.get("ts")
        if not round_id or not ts:
            continue
        n_frames = row.get("n_frames")
        has_frame = bool(n_frames)
        points.append({
            "ts": ts,
            "round_id": round_id,
            "day": day_of(entry_date, ts),
            "has_frame": has_frame,
            # 缺即缺：没有帧就是 None，**不许**落成 0（spec 硬规则 2）
            "n_frames": int(n_frames) if has_frame else None,
        })
    points.sort(key=lambda p: str(p["ts"]))
    return points


def axis_of(points) -> dict:
    """时间轴 = 网格里第一轮 → 最后一轮的**真实时间戳**（页面按它把 `ts` 映射成 x）。

    空网格 ⇒ `t0`/`t1` 都是 `None`：没有轮次就没有窗口，页面据此说"这批还没有轮次"，
    而不是拿今天凑一个窗口出来。
    """
    stamps = [str(p["ts"]) for p in points or [] if p.get("ts")]
    return {
        "t0": min(stamps) if stamps else None,
        "t1": max(stamps) if stamps else None,
        "n_rounds": len(stamps),
    }


def cell_of(station_id, box_id=None) -> tuple[int | None, int | None]:
    """站位/框 id → **(层, 框)**；编码见 `patrol.stations.build_grid`：`S{层}{框:02d}`。

    框固定两位、层是前面剩下的位数（`S412` = 第 4 层第 12 框、`S102` = 第 1 层第 2 框），
    双档写法的角度后缀（`S412-top45`）不属于格位。站位 id 解析不出来时退到 `box_id`，
    两个都解析不出来 ⇒ `(None, None)`：**不猜**。宁可让页面把这格画成"不知道在哪"，也不塞进
    一个看着像真的、其实是猜出来的格子里——照着错的格子去找，比找不到更费现场的时间。
    """
    for text in (station_id, box_id):
        cell = _parse_cell(text)
        if cell is not None:
            return cell
    return (None, None)


def _parse_cell(value) -> tuple[int, int] | None:
    text = str(value or "").strip()
    if len(text) < 4 or text[0] not in ("S", "B"):
        return None
    digits = text[1:].split("-", 1)[0]
    if len(digits) < 3 or not digits.isdigit():
        return None
    layer, col = int(digits[:-2]), int(digits[-2:])
    if layer < 1 or col < 1:
        return None
    return (layer, col)


def floor_plan_order(node) -> tuple:
    """与平面图同一套顺序：**层升序、层内框升序**（第 1 层在最上，框 1 在最左）。

    解析不出格位的站位排在最后（`layer`/`col` 是 null）：它们进不了矩阵的格子，
    但也不能从应答里消失——消失的站位看起来就像"这一批没这个站位"。
    """
    layer, col = node.get("layer"), node.get("col")
    if layer is None or col is None:
        return (1, 0, 0, str(node.get("node_id") or ""))
    return (0, int(layer), int(col), str(node.get("node_id") or ""))


def legacy_round_count(rows) -> int:
    """本批里**没有 `round_id`** 的旧轮次数（按 `ts` 去重）。

    帧挂不到没有 `round_id` 的轮次头上（`coverage_points` 与 `matrix_of` 都不收），
    但这件事要能数出来写进 `notes`：悄悄消失的轮次看起来就像"那几轮没巡检"。
    去重是因为矩阵是「轮 × 站」——一行一轮，不去的数会把一轮数成 48 轮。
    """
    return len({str(row.get("ts")) for row in rows or []
                if row.get("ts") and not row.get("round_id")})


def matrix_of(rows, *, entry_date) -> dict:
    """「每轮 × 每站位」的行 → `{"rounds": 共享时间轴, "nodes": 站位矩阵}`（票 03）。

    行形状：`ts` / `round_id` / `station_id` / `box_id` / `n_frames`（取数在 `analysis.store`）。

    **共享时间轴**：`ts` 与 `round_id` 只写一次（`rounds[]`），节点里的 `points[i]` 与
    `rounds[i]` 一一对应。**x 仍然取自 `rounds[i].ts`（真实时间戳），下标只是对齐键**；
    逐点重复一遍的话，48 站 × 16 天 × 8 轮就是 6144 份 `ts`+`round_id` —— 单次响应会从
    约 240 KB 抬到约 690 KB，越不过票 03 验收 4 的 500 KB。

    `null ≠ 0`（spec 硬规则 2）：这一轮这一站没有帧 ⇒ `has_frame=False` 且 `n_frames=None`。
    站位集合 = **本批帧里出现过的站位**：一张帧都没有的站位不进矩阵（页面按"无数据"画），
    而不是拿一行"0 帧"的格子凑数——那会把"没记录"说成"拍了 0 张"。
    """
    axis: dict[tuple, dict] = {}
    cells: dict[str, dict] = {}
    for row in rows or []:
        ts, round_id = row.get("ts"), row.get("round_id")
        if not ts or not round_id:
            continue            # 旧轮次：帧挂不到它头上（与单站形态同口径）
        key = (str(ts), str(round_id))
        axis.setdefault(key, {"ts": ts, "round_id": round_id,
                              "day": day_of(entry_date, ts)})
        station_id = row.get("station_id")
        if not station_id:
            continue            # 没有站位行（本批一张帧都没有）：只留时间轴
        node = cells.setdefault(str(station_id), {"box_id": None, "points": {}})
        box_id = row.get("box_id")
        if box_id and not node["box_id"]:
            node["box_id"] = box_id
        n_frames = row.get("n_frames")
        has_frame = bool(n_frames)
        node["points"][key] = {
            "has_frame": has_frame,
            # 缺即缺：没有帧就是 None，**不许**落成 0（spec 硬规则 2）
            "n_frames": int(n_frames) if has_frame else None,
        }

    rounds = [axis[key] for key in sorted(axis, key=lambda item: item[0])]
    nodes = []
    for station_id, node in cells.items():
        layer, col = cell_of(station_id, node.get("box_id"))
        nodes.append({
            "node_id": station_id,
            "layer": layer,
            "col": col,
            "box_id": node.get("box_id"),
            "points": [node["points"].get((str(r["ts"]), str(r["round_id"])),
                                          {"has_frame": False, "n_frames": None})
                       for r in rounds],
        })
    nodes.sort(key=floor_plan_order)
    return {"rounds": rounds, "nodes": nodes}

"""操作会话与「相邻巡检轮次三联」（票 29 / ADR-0028）。

**只做纯计算**：进 rows、出 rows，不碰存储、不发请求。取数在 `analysis.api` 的
`/operations/sessions` 闭包里（与其它端点同一套做法）。

两个口径，写死在这里免得各处再解释一遍：

1. **分析单元 = 操作会话**。同库房内**相邻**审计记录间隔 ≤ `window_min`（默认 10 分钟）
   即并入同一次调节，`n=1` 也成立。会话内部**逐条保留**（ADR-0023 的"逐条留"不变），
   会话只是外面的一层分组外壳，不是聚合。
2. **相邻 = 相邻的巡检轮次**，不是 ±10 分钟窗口。巡检约 3 小时一轮，拿 10 分钟去找图
   必然落空。选轮次时**强制跨越**会话时刻：至少 1 轮在会话前、至少 1 轮在会话后。

**同框是硬约束**：三联的 3 帧必须来自同一个框——不同框的菌簇绝对高度不可比，
跨框比较会把"层间差异"读成长势变化。
"""

from __future__ import annotations

from datetime import datetime

#: 三联的帧数（用户口径：相邻巡检时间的 top-3）。
DEFAULT_FRAMES = 3

#: 一次调节的时间窗（分钟）。**约定值，不是物理量**；现场若出现"拖了 15 分钟的连改"，
#: 会被切成两次会话。接口上留了 `window_min`，改值不改口径。
DEFAULT_WINDOW_MIN = 10

#: 会话内部的明细列（页面直接显示，无需再拼）。
_CHANGE_KEYS = (
    "ts", "device_code", "device_name", "point_code", "point_name",
    "value", "operator", "source", "content",
)


def _parse(ts) -> datetime | None:
    """时刻解析。审计入库前已由 `analysis.audit.norm_ts` 归一成 `YYYY-MM-DDTHH:MM:SS`，
    这里仍兼容空格分隔——解析失败会让整条会话**凭空少一段**，不值得为省一次 replace 冒险。"""
    text = str(ts or "").strip().replace(" ", "T")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def sessions(rows: list[dict], *, window_min: float = DEFAULT_WINDOW_MIN) -> list[dict]:
    """审计流 → 操作会话（按开始时刻升序）。

    切分只看**相邻两条的间隔**：`> window_min` 就开一个新会话。跨操作人不切——
    一次调节里换个人接着点，仍然是同一次调节（`operators` 会把人都列出来）。
    """
    ordered = sorted(
        (r for r in rows if _parse(r.get("ts")) is not None),
        key=lambda r: _parse(r.get("ts")),
    )
    limit = float(window_min) * 60.0
    out: list[dict] = []
    current: dict | None = None
    last: datetime | None = None
    for row in ordered:
        moment = _parse(row.get("ts"))
        gap = (moment - last).total_seconds() if (moment and last) else None
        if current is None or (gap is not None and gap > limit):
            current = {
                "ts_start": row.get("ts"),
                "ts_end": row.get("ts"),
                "operators": [],
                "changes": [],
            }
            out.append(current)
        current["ts_end"] = row.get("ts")
        operator = str(row.get("operator") or "").strip()
        if operator and operator not in current["operators"]:
            current["operators"].append(operator)
        current["changes"].append({k: row.get(k) for k in _CHANGE_KEYS})
        last = moment

    for item in out:
        changes = item["changes"]
        item["n_changes"] = len(changes)
        item["operator"] = "、".join(item["operators"]) or None
        # 设备/点位去重后列出：一张会话卡上"改了哪几处"要一眼看全
        item["devices"] = _unique(c.get("device_name") or c.get("device_code") for c in changes)
        item["points"] = _unique(c.get("point_name") or c.get("point_code") for c in changes)
    return out


def _unique(values) -> list[str]:
    seen: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def pick_rounds(
    target_ts: str, rounds: list[dict], *, frames: int = DEFAULT_FRAMES,
    end_ts: str | None = None,
) -> dict:
    """挑出与某个时刻**相邻**的巡检轮次，并**强制跨越**。

    `rounds` 是升序的轮次列表（每项至少带 `ts` / `round_id`）。返回：

    - `picked`：最多 `frames` 项，升序，每项带 `side`（`before`/`after`）与 `gap_h`
      （与目标时刻的小时差，**带符号**：负 = 调节之前）；
    - `crossed`：是否同时包含前、后两段；
    - `missing`：`"before"` / `"after"` / `None`——哪一侧没有轮次可用；
    - `during`：落在**会话窗口内**因而被丢掉的轮次数。

    `end_ts` = 会话结束时刻。`(target_ts, end_ts]` 之间的轮次**两边都不算**：
    那是调节进行中拍的，既不是调前也不是调后。拿它充当"调后"，等于把调节过程
    本身当成了结果——一次 10 分钟的连改里正好撞上一轮巡检，概率并不低。

    名额分配：**每侧先各取最近一轮**（少了任何一侧就没有"调前/调后"的对照），
    余下名额优先给**后**侧（调节之后才看得出变化），后侧不够才回填前侧。
    """
    moment = _parse(target_ts)
    if moment is None or not rounds:
        return {"picked": [], "crossed": False,
                "missing": "before" if not rounds else None, "during": 0}

    end = _parse(end_ts) or moment
    before: list[dict] = []
    after: list[dict] = []
    during = 0
    for item in rounds:
        at = _parse(item.get("ts"))
        if at is None:
            continue
        if at <= moment:
            before.append(item)
        elif at <= end:
            during += 1
        else:
            after.append(item)
    before.sort(key=lambda r: _parse(r.get("ts")))
    after.sort(key=lambda r: _parse(r.get("ts")))

    picked: list[dict] = []
    if before:
        picked.append({**before[-1], "side": "before"})
    if after:
        picked.append({**after[0], "side": "after"})
    need = max(0, int(frames) - len(picked))
    for item in after[1:]:
        if need <= 0:
            break
        picked.append({**item, "side": "after"})
        need -= 1
    for item in reversed(before[:-1]):
        if need <= 0:
            break
        picked.append({**item, "side": "before"})
        need -= 1

    for item in picked:
        at = _parse(item.get("ts"))
        item["gap_h"] = round((at - moment).total_seconds() / 3600, 2) if at else None
    picked.sort(key=lambda r: str(r.get("ts") or ""))
    crossed = any(p["side"] == "before" for p in picked) and any(
        p["side"] == "after" for p in picked)
    missing = None if crossed else ("after" if before else "before")
    return {"picked": picked, "crossed": crossed, "missing": missing, "during": during}


def _is_probe(frame: dict) -> bool:
    """重试帧（`-probe1` 之类）。同一轮里有多帧时优先非 probe（与页面同一口径）。"""
    text = str(frame.get("object_name") or "").lower()
    return "-probe" in text


def _ok(frame: dict) -> bool:
    return frame.get("ok") not in (0, False, "0", "false") and bool(
        str(frame.get("object_name") or "").strip())


def pick_frame(frames: list[dict], target_ts: str) -> dict | None:
    """一轮里为该框挑一帧：**只要成功帧**，优先非 probe，其次离目标时刻最近。

    失败帧（`ok=0` 或没有 `object_name`）不入选——它不是"长势不变"，是"这次没拍到"。
    """
    usable = [f for f in frames or () if _ok(f)]
    if not usable:
        return None
    moment = _parse(target_ts)

    def rank(frame: dict):
        at = _parse(frame.get("ts"))
        gap = abs((at - moment).total_seconds()) if (at and moment) else 0.0
        return (1 if _is_probe(frame) else 0, gap)

    return min(usable, key=rank)


def attach_frames(
    picked: list[dict], frames_by_round: dict[str, list[dict]], *, target_ts: str
) -> list[dict]:
    """把轮次列表接上该框的帧；没帧的轮次**保留在列表里**并标 `has_frame=False`。

    保留比删掉重要：`rounds` 里有、`images` 里没有 = "这一轮巡检到了、这个框没拍到"，
    那是一个缺口，不是"这一轮不存在"。
    """
    out: list[dict] = []
    for item in picked:
        round_id = str(item.get("round_id") or "")
        frames = frames_by_round.get(round_id, []) if round_id else []
        frame = pick_frame(frames, target_ts)
        out.append({
            "side": item["side"],
            "round_id": round_id or None,
            "ts": item.get("ts"),
            "gap_h": item.get("gap_h"),
            "has_frame": frame is not None,
            "image": frame,
        })
    return out

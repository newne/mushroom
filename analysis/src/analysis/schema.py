"""表结构的**单源常量**：列清单、JSON 编解码、状态字面量（票 28 contract）。

**这里只有数据形状，没有数据访问** —— 建表 DDL 与全部读写 SQL 在 `analysis.store`。
票 28 的 contract 步删掉了 SQLite 实现（原 `analysis/db.py`），但它的这批常量不是
"旧实现的一部分"：它们是**存储层对外的契约**（谁按什么顺序写、哪几列是列表型、
读回来该是什么形状），API、jobs、measure 侧都按它对齐。所以搬家而不是删除，
搬到一个名实相符的地方 —— 留着 `db.py` 这个名字、"里面其实是 PG"是禁止的。

两位一体的东西必须挨着写：

- `*_FIELDS` 与 `analysis.store` 的建表 DDL：列与顺序两边都不许漂（有测试逐列比对）；
- `as_text` / `as_list`：列表型字段存 JSON 文本、读回来解回 list，写入方与读取方成对。
"""

from __future__ import annotations

import json

#: 环境表的列（顺序即写入顺序）。**存小时聚合**，不是原始分钟序列（2026-10-12 定）。
ENVIRONMENT_FIELDS = (
    "ts", "zone",
    "temp_c", "temp_min", "temp_max",
    "rh_pct", "rh_min", "rh_max",
    "co2_ppm", "co2_min", "co2_max",
    "lamp_state", "lamp_on_s", "lamp_off_s", "lamp_on_m", "lamp_off_m",
    "lamp_on_min", "n",
)

#: 操作日志的列（票 05）。`lamp_state` 那类"设备状态"在 environment，这里是**人对设备的操作**。
OPERATION_FIELDS = (
    "ts", "zone", "device_code", "device_name", "point_code", "point_name",
    "value", "operator", "source", "content",
)

#: 批次台账物化缓存：`(room_id, entry_date)` 唯一，`batch_no` 由映射表反查。
BATCH_FIELDS = ("room_id", "entry_date", "batch_no", "batch_id")

#: 测量线的字段清单（票 11）。与 measure 侧 `RECORD_FIELDS` **一字不差（含顺序）**，
#: 禁第四套命名；`room_id` 在上游是必填，存储层用 NOT NULL 兜底（缺了就插不进去，
#: 而不是静默丢字段——"写入方多给、存储方少要"这个坑踩过一次）。
#: 票 20（2026-10-14）追加五个估算证据列。**顺序照测量侧的线格式**（2026-10-14 对齐）：
#: 以前这边把菌簇四列排在 `quality` 之后，两侧顺序其实一直不同 —— 只有集合相同，
#: "一字不差"名不副实。现在按生产者的顺序排，测试用严格相等钉住。
MEASUREMENT_FIELDS = (
    "ts", "room_id", "box_id", "station_id", "angle_profile",
    "n", "mean_len_mm", "mean_cap_mm", "mean_cluster_w_mm", "mean_cluster_h_mm",
    "mean_area_mm2", "mean_projected_d_mm", "p10_len", "p90_len", "quality",
    "estimated_fields", "scale_source", "mm_per_px", "scale_ref_mm", "scale_ref_px",
)

#: 巡检轮次的列。内容完整性三列（票 13）决定"这一轮的帧要不要排除"。
ROUND_FIELDS = (
    "ts",
    "round_id",
    "ok",
    "n_results",
    "n_failures",
    "aborted",
    "room_id",
    "entry_date",
    "batch_no",
    "suspect_duplicate",
    "dup_groups",
    "content_check_error",
    "n_duplicate",
    "n_frames_recorded",
)

#: 图像索引的列（ADR-0005：一帧一行）。库房三列与 daemon 写入的行一一对应。
IMAGE_FIELDS = (
    "ts",
    "round_id",
    "station_id",
    "box_id",
    "angle_profile",
    "imaging_profile",
    "camera_ip",
    "y",
    "z",
    "object_name",
    "object_name_source",
    "frame_dup",
    "dup_group",
    "content_sha256",
    "same_as_previous",
    "duplicate_streak",
    "cloud_url",
    "elapsed_s",
    "ok",
    "error",
    "room_id",
    "entry_date",
    "batch_no",
)


def flatten_image(row: dict) -> dict:
    """patrol 的索引记录把坐标放在 ``yz`` 列表里，这里摊平成 ``y`` / ``z`` 两列。"""
    yz = row.get("yz") or ()
    flat = {f: row.get(f) for f in IMAGE_FIELDS}
    flat["y"] = yz[0] if len(yz) > 0 else None
    flat["z"] = yz[1] if len(yz) > 1 else None
    return flat


def as_text(value):
    """列表/字典 → JSON 文本，其余原样。

    `rounds.dup_groups` 是 TEXT 列，而生产者发来的是**对象数组**
    （`[{"sha256_12":…, "n":…, "stations":[…]}, …]`，见 `patrol/daemon.py:344`）——
    直接绑给驱动会抛"不支持的类型"。修的是这一条，但**真正的教训**是它的反面：
    如果存储层默默接受、库里头变成别的形状，那才是查不出来的那种坏。
    """
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False)
    return value


def as_list(value):
    """JSON 文本 → Python 列表；不是 JSON 或解析失败就**原样返回**（不猜、不吞错）。"""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text.startswith(("[", "{")):
        return value
    try:
        return json.loads(text)
    except ValueError:
        return value


#: "区间表从来没人算过"—— **不要把"没算过"说成"样本不足"**：
#: 前者是接线问题（票 05 的 `refresh_ranges` 没排期），后者是数据问题。
RANGES_STATE_NEVER_COMPUTED = "never_computed"

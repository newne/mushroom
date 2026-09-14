"""站位表：库房每个拍照位的坐标与成像配置（spec §4.4 数据契约）。

坐标是**虚拟坐标系**的 (y, z) mm，与导轨实轴的对应关系见 ``patrol.motion_profile``：
Y 为水平长行程（0…4492，向右为正），Z 为竖直短行程（-212…0，向上为正）。

现场布局（2026-09-12 确认）：**横向 12 框 × 竖向 5 层 = 60 个站位，每框 1 个站位**。
两轴的机械行程刚好整除这个网格，因此站位坐标由 ``build_grid`` 从行程**推导**而不是
逐点手写——侧重点在于现场"框数"随时会调（用户：后面根据实际框数再调整），
调 ``GRID_COLS`` / ``GRID_LAYERS`` 即可，不用重标 60 行 YAML。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

import yaml

from patrol.motion_profile import M1, MotionProfile

# ---------- 现场布局 ----------

GRID_COLS = 12          # 横向框数（沿 Y 长行程 0…4492mm）
GRID_LAYERS = 5         # 竖向层数（沿 Z 短行程 -212…0mm，**第 1 层在最上**）
# 全场**唯一**相机：老系统与 M1 共用同一台（spec §2；现场 2026-09-12 确认 238 无密码）。
# 收敛成全局常量而不是每站位一个字段——"每框一个 IP"是改造前的假想，实际只有一台。
CAMERA_IP = "192.168.1.238"
# 角度档。一期每框 1 个站位 → 单档；Z 的行程已全部让给"选层"，没有余量再切角度。
# 单档取斜拍 45°（沿用改造前的整定值：一帧同时拿到菌盖直径与菇体长度两个量）。
# 要恢复双档就写成 ("top0", "top45")——站位 id 会自动加角度后缀。
GRID_ANGLES = ("top45",)
DEFAULT_ANGLE = GRID_ANGLES[0]


@dataclass
class Station:
    id: str
    box_id: str
    y: float
    z: float
    angle_profile: str = DEFAULT_ANGLE   # 见 GRID_ANGLES（top0 量盖径 | top45 量菇长）
    camera_ip: str = CAMERA_IP           # 全场同一台（见模块顶部的 CAMERA_IP）
    camera_user: str = "admin"
    camera_pwd: str = ""
    exposure_note: str = "manual"  # 手动对焦/固定曝光，标定时锁定
    layer: int = 0                 # 层号 1…GRID_LAYERS（自顶向下）；0 = 未标定
    col: int = 0                   # 横向框序号 1…GRID_COLS（沿 Y 递增）；0 = 未标定
    # 图像微调学到的偏移（mm，叠加在 y/z 上）。推导坐标只保证"目标进视野"，
    # `patrol.framing` 用拍到的图把这个偏移学出来并写回这里 —— 于是下一轮直接从
    # 好位置起步，常规轮次零额外耗时。默认 0 = 行为与没有微调时完全一致。
    trim_y: float = 0.0
    trim_z: float = 0.0
    trim_note: str = ""            # 谁/何时学到的（排障时最想知道的事）

    @property
    def cell(self) -> str:
        """网格单元标识，如 ``5-12``（第 5 层第 12 框）；未标定返回 ``-``。"""
        return f"{self.layer}-{self.col}" if self.layer and self.col else "-"

    @property
    def target(self) -> tuple[float, float]:
        """实际要去的坐标 = 推导/示教坐标 + 学到的 trim。"""
        return (self.y + self.trim_y, self.z + self.trim_z)

    def with_trim(self, trim: tuple[float, float], *, note: str = "") -> Station:
        """带 trim 的副本（`trim` 上限由 `patrol.framing.clamp_trim` 负责夹紧）。"""
        return replace(self, trim_y=trim[0], trim_z=trim[1], trim_note=note or self.trim_note)


def layer_z(layer: int, profile: MotionProfile = M1) -> float:
    """层号 → 该层中心的 Z 坐标（第 1 层在最上，即最靠近 Z 原点）。

    5 层均分 Z 的竖直行程：层距 = 212 / 5 = 42.4mm，层心落在各段中点，
    于是第 1 层 z = -21.2、第 5 层 z = -190.8，两端各留半个层距的余量。
    """
    if not 1 <= layer <= GRID_LAYERS:
        raise ValueError(f"层号越界 {layer}（1…{GRID_LAYERS}）")
    pitch = profile.z.travel_span / GRID_LAYERS
    return profile.z.travel_max - (layer - 0.5) * pitch


def col_y(col: int, profile: MotionProfile = M1) -> float:
    """横向框序号 → 该框中心的 Y 坐标（12 框均分 Y 的 4492mm 行程）。"""
    if not 1 <= col <= GRID_COLS:
        raise ValueError(f"框序号越界 {col}（1…{GRID_COLS}）")
    pitch = profile.y.travel_span / GRID_COLS
    return profile.y.travel_min + (col - 0.5) * pitch


def grid_geometry(profile: MotionProfile = M1) -> dict[str, float]:
    """网格几何（供 console / 前端画平面图，避免两处各推一套）。"""
    return {
        "cols": GRID_COLS,
        "layers": GRID_LAYERS,
        "y_pitch": profile.y.travel_span / GRID_COLS,
        "z_pitch": profile.z.travel_span / GRID_LAYERS,
        "y_min": profile.y.travel_min,
        "y_max": profile.y.travel_max,
        "z_min": profile.z.travel_min,
        "z_max": profile.z.travel_max,
    }


def build_grid(
    profile: MotionProfile = M1,
    *,
    angles: tuple[str, ...] = GRID_ANGLES,
    camera_ip_of: Callable[[int, int], str] | None = None,
) -> list[Station]:
    """按布局生成站位表，顺序为**蛇形遍历序**（层内走完一列再折返）。

    蛇形不是美观问题：按层号顺序直走的话，每换一层都要从轨道最右端折回最左端
    （11 × 374 ≈ 4.1m 空程），5 层要多跑约 16m；折返走法把这段空程降到 0
    （换层只动 Z 的 42mm）。60 个站位、每轮省 16m，是每轮都省。

    站位 id / 框 id 都用 ``{层}{框:02d}`` 编码（如 ``S512`` = 第 5 层第 12 框），
    一眼能读出网格位置——列表、平面图、图像对象名三处看到的都是同一个编号。
    """
    if not angles:
        raise ValueError("angles 不能为空：每框至少要有一个角度档")
    stations: list[Station] = []
    for layer in range(1, GRID_LAYERS + 1):
        cols = range(1, GRID_COLS + 1) if layer % 2 == 1 else range(GRID_COLS, 0, -1)
        for col in cols:
            box_id = f"B{layer}{col:02d}"
            for angle in angles:
                suffix = "" if len(angles) == 1 else f"-{angle}"
                stations.append(
                    Station(
                        id=f"S{layer}{col:02d}{suffix}",
                        box_id=box_id,
                        y=col_y(col, profile),
                        z=layer_z(layer, profile),
                        angle_profile=angle,
                        camera_ip=camera_ip_of(layer, col) if camera_ip_of else CAMERA_IP,
                        layer=layer,
                        col=col,
                    )
                )
    return stations


def retarget(stations: list[Station], profile: MotionProfile = M1) -> list[Station]:
    """按新 profile 重算坐标，保留 id/标定字段（现场整定后批量刷新用）。"""
    return [
        replace(s, y=col_y(s.col, profile), z=layer_z(s.layer, profile))
        if s.layer and s.col else s
        for s in stations
    ]


def fill_camera_ip(stations: list[Station], camera_ip: str = CAMERA_IP) -> tuple[list[Station], int]:
    """给 ``camera_ip`` 为空的站位补上全场默认相机，返回 ``(新表, 补了几个)``。

    本机**只有一台相机**（装在滑块上，60 个站位共用），所以 ``camera_ip`` 本质上是
    全局配置而不是逐站参数。手写的或早期的站位表常把它留空——那种表在巡检侧会因为
    "相机 IP 为空"被拒，而现场其实只需要一个默认值。补全只在**空**的时候发生，
    显式写了的 IP 一律保留（将来万一要分机位，站位表仍然说了算）。
    """
    filled = 0
    out: list[Station] = []
    for s in stations:
        if s.camera_ip:
            out.append(s)
        else:
            out.append(replace(s, camera_ip=camera_ip))
            filled += 1
    return out, filled


def load_stations(path: str) -> list[Station]:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    stations = [Station(**item) for item in data.get("stations", [])]
    ids = [s.id for s in stations]
    if len(ids) != len(set(ids)):
        raise ValueError(f"站位 id 重复: {ids}")
    return stations


def save_stations(stations: list[Station], path: str) -> None:
    payload = yaml.safe_dump({"stations": [asdict(s) for s in stations]},
                             allow_unicode=True, sort_keys=False)
    Path(path).write_text(payload, encoding="utf-8")


def image_object_name(station: Station, ts: datetime) -> str:
    """图片对象名（MinIO/本地共用，截图服务自动补 .jpg）。"""
    return (f"{ts.strftime('%Y%m%d')}/{station.box_id}_{station.id}"
            f"_{station.angle_profile}_{ts.strftime('%H%M%S')}")


# ---------- 标定字段 ----------

def cell_of(station_id: str) -> tuple[int, int] | None:
    """从站位 id 反解 ``(层, 框)``；不是 ``S{层}{框:02d}`` 形状则返回 None。

    ``build_grid`` 与示教都用同一套编码（如 ``S105`` = 第 1 层第 5 框），于是
    "这个站位属于哪个格子"不需要额外的旁表——但**也不强求**：现场若要用别的命名，
    反解失败只是拿不到格子，不影响记录与保存。
    """
    body = station_id.strip().upper().removeprefix("S")
    body = body.split("-")[0]        # 双角度档的后缀（S105-top0）
    if len(body) < 3:
        return None
    layer_s, col_s = body[0], body[1:]
    if not (layer_s.isdigit() and col_s.isdigit()):
        return None
    layer, col = int(layer_s), int(col_s)
    if not (1 <= layer <= GRID_LAYERS and 1 <= col <= GRID_COLS):
        return None
    return layer, col


# ---------- 示教（原 teach.py，评审 #2/#6 后并入；CLI 见 patrol.teach） ----------

def station_at(
    fmc,  # patrol.fmc.Fmc4030（鸭子类型：只需 current_yz）
    *,
    station_id: str,
    box_id: str,
    layer: int = 0,
    col: int = 0,
    angle_profile: str = DEFAULT_ANGLE,
    camera_ip: str = CAMERA_IP,
    camera_user: str = "admin",
    camera_pwd: str = "",
) -> Station:
    """**以控制器当前坐标**构造一个 Station（手动 Jog 到位后的记录动作）。

    ``layer`` / ``col`` 给了就校验范围并写入：这两个字段是"实际框位与均分不符"这件事
    的载体——它是示教相对 ``build_grid`` **推导坐标**的唯一增量（见 ``retarget``）。
    """
    for name, value, limit in (("层", layer, GRID_LAYERS), ("框", col, GRID_COLS)):
        if value and not 1 <= value <= limit:
            raise ValueError(f"{name}号越界 {value}（1…{limit}）")
    y, z = fmc.current_yz()
    return Station(
        id=station_id,
        box_id=box_id,
        y=y,
        z=z,
        angle_profile=angle_profile,
        camera_ip=camera_ip,
        camera_user=camera_user,
        camera_pwd=camera_pwd,
        layer=layer,
        col=col,
    )


def record_station(
    fmc,  # patrol.fmc.Fmc4030（鸭子类型：只需 current_yz）
    station_id: str,
    box_id: str,
    *,
    angle_profile: str = DEFAULT_ANGLE,
    camera_ip: str = CAMERA_IP,
    camera_user: str = "admin",
    camera_pwd: str = "",
) -> Station:
    """以控制器当前坐标记录一个拍照位（手动 Jog 到位后调用）。"""
    return station_at(
        fmc,
        station_id=station_id,
        box_id=box_id,
        angle_profile=angle_profile,
        camera_ip=camera_ip,
        camera_user=camera_user,
        camera_pwd=camera_pwd,
    )


def upsert_station(stations: list[Station], station: Station) -> list[Station]:
    """按 id 更新或追加，保持原有顺序。"""
    for i, s in enumerate(stations):
        if s.id == station.id:
            stations[i] = station
            return stations
    stations.append(station)
    return stations


def drop_station(stations: list[Station], station_id: str) -> list[Station]:
    """按 id 删除（原地，返回同一列表）；id 不存在则原样返回。

    **按 id 而不是按格子**：双角度档时一个格子对应多个站位（``S105-top0`` /
    ``S105-top45``），按 ``layer/col`` 删会一次删掉两个。
    """
    target = station_id.strip().upper()
    stations[:] = [s for s in stations if s.id.strip().upper() != target]
    return stations


def load_or_init(path: str) -> list[Station]:
    """读取站位表；文件不存在则返回空表（由调用方决定是否保存）。"""
    if Path(path).exists():
        return load_stations(path)
    return []

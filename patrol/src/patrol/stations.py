"""站位表：库房每个拍照位的坐标与成像配置（spec §4.4 数据契约）。

坐标是**虚拟坐标系**的 (y, z) mm，与导轨实轴的对应关系见 ``patrol.motion_profile``：
Y 为水平长行程（0…4492，向右为正），Z 为竖直短行程（−212…0，**向上为正**）。
Y 的原点在左端（靠近电机）；Z 现场要求**回零向上**，原点在**顶端**（远离电机），
`+Z` 仍向上，从顶端往下走 Z 为负。

现场布局：**横向 12 框 × 竖向 4 层 = 48 个站位，每框 1 个站位**。
Y 框心按行程间距推导，Z 层心是用户给定的**初始锚点**（第 1…4 层 z = −40/−90/−145/−195），
C08 横向位置叠加现场抓图标定偏移；``build_grid`` 为缺少站位表的新环境生成同一套坐标。
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
GRID_LAYERS = 4         # 竖向层数（沿 Z 短行程 −212…0mm，**第 1 层在最上**＝靠近 Z 原点＝顶端）
# ⚠️ 2026-10-07：由 5 改为 4。判据是**实测**不是估计——用实时预览沿 Z 扫全程（5mm 一档、43 帧），
# 43 帧两两做图像自相似度（灰度归一化后的 MSE）：
#   · 单周期最优拟合 52.0mm；2 周期 102.5mm、3 周期 157.0mm 交叉验证
#   · **z=0 与 z=210 两帧 MSE≈0.99（几乎逐像素相同）** ⇒ 212mm 行程正好 **4 个整周期**
#   · 旧值 5 层 ⇒ 均分 42.4mm，与实测 52mm 不齐，偏离逐层累积到第 5 层达 30.4mm（半个周期）
#     ⇒ 那些站位多数落在"袋身侧面/失焦近景"的相位上，照片对判长势无效。
#   2026-10-08 按层实拍微调后，层心（当时以"底端原点、向上为正"计）为 10 / 60 / 132 / 155mm。
#   2026-10-10 原点改到顶端、并把"第 1 层"重新定义成**最上面那层**后，层心到原点的距离
#   （即 `-(z)`）为 57 / 80 / 152 / 202mm。
#   完整证据见 docs/patrol/prod-deploy/station-retarget.md §6/§7。
# ⚠️ 2026-10-10（晚）用户重定**初始锚点**：第 1…4 层 `z = −40 / −90 / −145 / −195`，
#   即层心到顶端原点的距离 40 / 90 / 145 / 195mm（层距 50/55/50，**不等分**）。
#   这是**起点不是终值**：当前生长阶段（蘑菇未长出菌包）要的是"俯拍看菌包表面"，
#   长出菌包后要改成"平视看菌杆/菌盖/整体形态"——换角度＝重新固定相机支架，
#   之后必须**重扫 z**（station-retarget.md §10）。均分格心之上只剩逐层偏移。
GRID_LAYER_Z_OFFSETS = (13.5, 10.5, 12.5, 9.5)
# 第 8 列的横向偏移。2026-10-12 由 −80 → **−40**：这一列挨着一根货架立柱，
# 前两次各右移 40mm（§9）后仍挡住画面中右部。本次移动量是**量出来的**不是估的：
# 用同列四层站位图（y/z 都已知）做列亮度剖面互相关，对照组（同 y、只差 z）错位 ≈0
# 验证方法，实验组（y 差 40mm）错位 ≈658px ⇒ mm/px ≈ 0.061 ⇒ 整幅画宽 ≈175mm
# ⇒ 立柱要从画幅 65% 挪到 90% 需右移 ≈44mm，取 40mm（与现场既有的小步长一致）。
# 证据见 docs/patrol/prod-deploy/station-retarget.md §13。
# ⚠️ `GRID_CELL_Y_OFFSETS` 是**替换**列偏移、不是叠加（见 `col_y`）：第 2 层第 8 框要
# 比其它层再向右 40mm，所以它必须跟着列偏移一起改，否则"相对右移 40mm"就被吃掉了。
GRID_COL_Y_OFFSETS = {8: -40.0}
GRID_CELL_Y_OFFSETS = {(2, 8): 0.0}
# 全场**唯一**相机：老系统与 M1 共用同一台（spec §2；现场 2026-09-12 确认 238 无密码）。
# 收敛成全局常量而不是每站位一个字段——"每框一个 IP"是改造前的假想，实际只有一台。
CAMERA_IP = "192.168.1.238"
# 角度档。一期每框 1 个站位 → 单档；Z 的行程已全部让给"选层"，没有余量再切角度。
# 档位名的含义（改这里等于改**标签**，不改光轴）：
#   top0    顶拍/俯拍：光轴垂直向下（量菌盖直径、看菌包表面）
#   top45   侧斜 45°：光轴俯斜（量菇体长度）
#   level0  平视：光轴近水平（看菌杆/菌盖/整体形态）
# ⚠️ 2026-10-13 口径（ADR-0024、票 09 §4）：
#   * **现役物理现状 = 平视单档 level0**（2026-10-10 现场确认；旧标签 `top45` 与实物不符，
#     已随 z 重锚归一）。相机没有云台/电动变角 ⇒ **换角 = 现场重新固定支架 + 重扫 z**
#     （station-retarget.md §10.3）；**是否换角不预设**，由现场作业与用户安排。
#   * 现役平视下**菌盖直径 / 菌杆长度明确缺测**（NULL + `no_angle_profile`）：
#     不许拿平视投影宽近似充数，也不许静默降级成空列。
#   * **换角生效日 = 数据口径分界**：生效日前后不可直接比较，跨角禁止拼成一条趋势。
# 双档写法 ("top0", "top45") 本身仍可用（站位 id 会自动加角度后缀），但只在前两条落实后才有意义。
GRID_ANGLES = ("level0",)
DEFAULT_ANGLE = GRID_ANGLES[0]


@dataclass
class Station:
    id: str
    box_id: str
    y: float
    z: float
    angle_profile: str = DEFAULT_ANGLE   # 见 GRID_ANGLES（top0 顶拍 | top45 斜拍 | level0 平视）
    camera_ip: str = CAMERA_IP           # 全场同一台（见模块顶部的 CAMERA_IP）
    camera_user: str = "admin"
    camera_pwd: str = ""
    exposure_note: str = "manual"  # 手动对焦/固定曝光，标定时锁定
    #: 成像参数快照 id（指向归档的 `configs/imaging-profile.yaml` 里某一条）。
    #: **空 = 那时还没记**——逐帧写进图像索引的 `imaging_profile` 列（analysis 已加列，票 11 §3）。
    #: 存 id，不存参数全文：images 是一帧一行，塞全文既重复，又会让改参数时的历史行口径漂移。
    imaging_profile: str = ""
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
        """网格单元标识，如 ``4-12``（第 4 层第 12 框）；未标定返回 ``-``。"""
        return f"{self.layer}-{self.col}" if self.layer and self.col else "-"

    @property
    def target(self) -> tuple[float, float]:
        """实际要去的坐标 = 推导/示教坐标 + 学到的 trim。"""
        return (self.y + self.trim_y, self.z + self.trim_z)

    def with_trim(self, trim: tuple[float, float], *, note: str = "") -> Station:
        """带 trim 的副本（`trim` 上限由 `patrol.framing.clamp_trim` 负责夹紧）。"""
        return replace(self, trim_y=trim[0], trim_z=trim[1], trim_note=note or self.trim_note)


def layer_z(layer: int, profile: MotionProfile = M1) -> float:
    """层号 → 该层中心的 Z 坐标（第 1 层在最上，即最靠近 Z 原点 = 顶端）。

    以均分格心为基准叠加逐层实拍标定偏移；实际层间距不要求相等。
    当前锚点（2026-10-10 用户给定）：层心到顶端原点的距离 40 / 90 / 145 / 195mm，
    即 ``z = −40 / −90 / −145 / −195``（层距 50/55/50）。

    从**原点那一侧**起算：Z 的原点在顶端、`+Z` 向上，所以层号越大、离原点越远、z 越小
    （越负）。``distance`` 是该层中心到顶端原点的**距离** mm（为正），坐标取 ``home_position − distance``。
    """
    if not 1 <= layer <= GRID_LAYERS:
        raise ValueError(f"层号越界 {layer}（1…{GRID_LAYERS}）")
    pitch = profile.z.travel_span / GRID_LAYERS
    distance = (layer - 0.5) * pitch + GRID_LAYER_Z_OFFSETS[layer - 1]
    z = profile.z.home_position - distance
    if not profile.z.contains(z):
        raise ValueError(
            f"第 {layer} 层的 z={z} 不在行程 {profile.z.travel_min}…{profile.z.travel_max} 内："
            "第 1 层应当贴近顶端的 Z 原点（Z 需向上回零）"
        )
    return z


def col_y(
    col: int,
    profile: MotionProfile = M1,
    *,
    layer: int | None = None,
) -> float:
    """横向框序号 → 该框中心的 Y 坐标；有层号时优先使用格点偏移。"""
    if not 1 <= col <= GRID_COLS:
        raise ValueError(f"框序号越界 {col}（1…{GRID_COLS}）")
    if layer is not None and not 1 <= layer <= GRID_LAYERS:
        raise ValueError(f"层号越界 {layer}（1…{GRID_LAYERS}）")
    pitch = profile.y.travel_span / GRID_COLS
    offset = GRID_CELL_Y_OFFSETS.get((layer, col)) if layer is not None else None
    if offset is None:
        offset = GRID_COL_Y_OFFSETS.get(col, 0.0)
    return profile.y.travel_min + (col - 0.5) * pitch + offset


def grid_geometry(profile: MotionProfile = M1) -> dict[str, float]:
    """网格几何（供 console / 前端画平面图，避免两处各推一套）。"""
    z_centers = [layer_z(layer, profile) for layer in range(1, GRID_LAYERS + 1)]
    return {
        "cols": GRID_COLS,
        "layers": GRID_LAYERS,
        "y_pitch": profile.y.travel_span / GRID_COLS,
        # 层距取**幅值**：前端/`nearest_station` 只关心"一格多宽"，方向由 z_min/z_max 给出。
        "z_pitch": abs(z_centers[-1] - z_centers[0]) / (GRID_LAYERS - 1),
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

    蛇形不是美观问题：按层号顺序直走的话，换层时要从轨道最右端折回最左端
    （11 × 374 ≈ 4.1m 空程）；4 层的折返走法可省约 12.3m 横向空程。

    站位 id / 框 id 都用 ``{层}{框:02d}`` 编码（如 ``S412`` = 第 4 层第 12 框），
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
                        y=col_y(col, profile, layer=layer),
                        z=layer_z(layer, profile),
                        angle_profile=angle,
                        camera_ip=camera_ip_of(layer, col)
                        if camera_ip_of
                        else CAMERA_IP,
                        layer=layer,
                        col=col,
                    )
                )
    return stations


def retarget(stations: list[Station], profile: MotionProfile = M1) -> list[Station]:
    """按新 profile 重算坐标，保留 id/标定字段（现场整定后批量刷新用）。"""
    return [
        replace(s, y=col_y(s.col, profile, layer=s.layer), z=layer_z(s.layer, profile))
        if s.layer and s.col
        else s
        for s in stations
    ]


def fill_camera_ip(stations: list[Station], camera_ip: str = CAMERA_IP) -> tuple[list[Station], int]:
    """给 ``camera_ip`` 为空的站位补上全场默认相机，返回 ``(新表, 补了几个)``。

    本机**只有一台相机**（装在滑块上，48 个站位共用），所以 ``camera_ip`` 本质上是
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


def nearest_station(stations: list[Station], y: float, z: float,
                    profile: MotionProfile = M1) -> Station | None:
    """离 ``(y, z)`` **最近的站位**；表为空返回 ``None``。

    给"非格点抓拍"定归属用：操作者可以把机构挪到站位表以外的位置再拍，但那帧照片
    仍然要挂到一个站位上——否则历史模式按站位取图时它无处可寻（见
    ``deploy.manual_exec`` 里 `_station_for` 的说明）。位置本身不会被丢掉，它逐帧
    记在 ``y``/``z`` 两列里，所以"归属"只影响**怎么找得到**，不影响事实。

    距离按**格距归一化**后再比，不是直接比毫米：
    Y 的格距约 374mm、Z 的平均格距约 50mm（``grid_geometry``），直接比欧氏距离的话 Y 方向上
    的一点点偏差就完全盖过 Z —— 结果会把"上一层"的点判给同一层的站位。归一化之后两个
    轴各自以"差了几格"参与比较，边界落在相邻实测格心中间，与"这个点落在哪个格子"
    是同一个答案。

    站点表是**手写也可**的（``load_stations`` 不校验栅格），所以这里不假设站位严格
    落在格心，逐站比较即可。
    """
    if not stations:
        return None
    g = grid_geometry(profile)
    py = g["y_pitch"] or 1.0
    pz = g["z_pitch"] or 1.0
    return min(stations, key=lambda s: ((y - s.y) / py) ** 2 + ((z - s.z) / pz) ** 2)


# ---------- Z 框架迁移：原点搬到顶端、第 1 层改到最上面（2026-10-10） ----------


def retarget_z_in_file(path: str, *, out: str | None = None,
                       log: Callable[[str], None] = print) -> int:
    """把站位表的 Z 从"底端原点、0…212"搬到"顶端原点、−212…0"，返回改动的站位数。

    这一步把两件事一起做：

    1. **原点翻到顶端**——`+Z` 仍是"向上"，底端原点与顶端原点的 `z` 不再是一回事；
    2. **第 1 层改到最上面那层**——现场把"第 1 层"定义成最靠近新原点（顶端）的那一层。

    所以既不是 `z → -z`，也不是 `z → z ± 212`，而是**按层号重算**：新 `z = layer_z(layer)`。
    层心**位置集合不变**（还是那 4 个层心），只是编号与坐标一起翻过来。真实旧表
    （`z = 10/60/132/155`）迁完是 `-57/-80/-152/-202`（第 1 层在最上）。

    防重复执行：新框架的 z 必然 ≤ 0，所以表里若已全是 `z ≤ 0` 就认定迁过了并抛错，
    而不是再算一遍（再算结果相同，但会让人以为"上一次没生效"）。
    """
    if M1.z.travel_max > 1e-6:
        raise ValueError("这条迁移假设 Z 原点在顶端（travel_max=0）；先改 motion_profile 再迁表")
    stations = load_stations(path)
    if not stations:
        raise ValueError(f"{path} 里没有站位，不迁移")
    if all(s.z <= 1e-6 for s in stations):
        raise ValueError(f"{path} 看起来已经是新框架（z 全 ≤ 0），拒绝重复迁移")
    beyond = [s.id for s in stations if s.z > M1.z.travel_span + 1e-6]
    if beyond:
        raise ValueError(f"{path} 里有超出旧行程的 z（{beyond[:3]}…），这份表不对劲，先人工看")

    migrated = [replace(s, z=layer_z(s.layer)) if s.layer else s for s in stations]
    save_stations(migrated, out or path)
    log(f"Z 框架迁移：{path} → {out or path}，{len(migrated)} 个站位（按层号重算 z，"
        "第 1 层改为最上；层心位置集合不变）")
    return len(migrated)


# ---------- 重锚：按当前锚点刷新已落盘的表（2026-10-12 起两轴都刷） ----------


def reanchor_in_file(path: str, *, out: str | None = None,
                     log: Callable[[str], None] = print) -> int:
    """把站位表的 `(y, z)` 重算成**当前的锚点**（`col_y` / `layer_z`），返回改动数。

    改 `GRID_*` 常量**不会**重算已经落盘的站位表（`station-retarget.md` §2 记着这个坑）
    ——库里的 `configs/stations.yaml` 会继续按旧锚点跑，看起来就像"改了没生效"。
    这条命令就是那条固定路子：先看 diff、再落盘、落盘前备份
    （CLI：`patrol-migrate reanchor`；只改 Z 框架的那次迁移是 `z-frame`，见
    `retarget_z_in_file`——那个一辈子只用一次，这个随锚点调整随时用）。

    做五件事：

    1. `y` 按 `col_y(col, layer)` 重算，`z` 按 `layer_z(layer)` 重算；
    2. **只清零"锚点真的动了"的那个轴的 trim**——`trim_*` 是相对旧锚点学到的微调，
       锚点没动的轴上它仍然有效。只调了第 8 列的 Y 时，其余 47 站的 `trim_y` 不该被
       牵连清零（那是现场一点一点挪出来的有效标定）；
    3. 单角度档配置下把 `angle_profile` 归一成 `DEFAULT_ANGLE`（多档时不动：那张表
       本来就该有多个档，全刷成一个等于偷偷把双档改成单档）；
    4. `id` / `box_id` / 相机与曝光字段原样保留；
    5. **不需要重新回零**：坐标框架没动（原点还在顶端、行程还是 −212…0），动的只是
       目标值——这一点与 Z 框架迁移正相反，别照那次的开机清单做。

    防呆：表里每个站位都得带层号与框号（示教残留可能没有），否则报错并列出来，
    而不是跳过——跳过等于"悄悄少改几个站"。
    """
    stations = load_stations(path)
    if not stations:
        raise ValueError(f"{path} 里没有站位，不重锚")
    unknown = [s.id for s in stations if not (s.layer and s.col)]
    if unknown:
        raise ValueError(
            f"{path} 里有 {len(unknown)} 个站位没有层号/框号（{unknown[:3]}…），"
            "无从按锚点重算——用 patrol-teach record 补上层号/框号再来"
        )

    single_angle = len(GRID_ANGLES) == 1
    moved = 0
    out_stations: list[Station] = []
    for s in stations:
        y = col_y(s.col, layer=s.layer)
        z = layer_z(s.layer)
        trim_y, trim_z = s.trim_y, s.trim_z

        # 日志每行都带站位 id：现场是把日志整段贴出来看的，缩进归属在粘贴/截图里会丢
        if abs(y - s.y) > 1e-6:
            log(f"  {s.id}: y {s.y:.1f} → {y:.1f}")
            if trim_y:
                log(f"  {s.id}: trim_y {trim_y:+.1f} → 0.0（相对旧 y 锚点学到的微调，随重锚作废）")
                trim_y = 0.0
        if abs(z - s.z) > 1e-6:
            log(f"  {s.id}: z {s.z:.1f} → {z:.1f}")
            if trim_z:
                log(f"  {s.id}: trim_z {trim_z:+.1f} → 0.0（相对旧 z 锚点学到的微调，随重锚作废）")
                trim_z = 0.0
        if single_angle and s.angle_profile != DEFAULT_ANGLE:
            log(f"  {s.id}: 角度档 {s.angle_profile} → {DEFAULT_ANGLE}"
                "（标签与现场支架不一致，一并归一）")
        # 两个 trim 都归零了，"谁/何时学到的"那条备注也就没了指向对象
        note = s.trim_note if (trim_y or trim_z) else ""

        changed = (abs(y - s.y) > 1e-6 or abs(z - s.z) > 1e-6
                   or trim_y != s.trim_y or trim_z != s.trim_z
                   or note != s.trim_note
                   or (single_angle and s.angle_profile != DEFAULT_ANGLE))
        if changed:
            moved += 1
        out_stations.append(replace(
            s, y=y, z=z, trim_y=trim_y, trim_z=trim_z, trim_note=note,
            angle_profile=DEFAULT_ANGLE if single_angle else s.angle_profile,
        ))

    save_stations(out_stations, out or path)
    log(f"重锚：{path}，{len(out_stations)} 个站位按当前锚点重算 (y, z)"
        f"（{moved} 个站位有改动；框架未变，不需要重新回零）")
    return moved


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

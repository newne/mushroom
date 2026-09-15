"""运动参数单源（ADR-0002）：本机两轴 Y/Z 各一份（ADR-0007）。

本文件是**现场整定的唯一入口**——机械行程、目标速度/加减速、回零方向与速度、
控制器寻址都在这里；两条执行路径（``patrol.fmc.client`` 的 M1 主机驱动、
``patrol.elo.generator`` 的 M2 脱机脚本）在 import 时取到同一组值。

现场实测几何（2026-09-11；**Z 的方向 2026-09-15 更正**，见 ADR-0018）：

| 轴 | 行程 mm | 运动正方向 | 原点位置 | 回零方向 |
| --- | --- | --- | --- | --- |
| Y（轴 1） | 0 … 4492 | 向右 | 左端（**靠近电机**） | 负限位回零（homeDir=2） |
| Z（轴 2） | 0 … 212 | 向下 | 顶端（**靠近电机**） | 负限位回零（homeDir=2） |

原点即「两轴都向**负限位**回零」后的落点：Y 在左端、Z 在顶端。说明书 §三.2 写得很清楚：
**「正限位位于远离电机端，负限位为靠近电机端」**——于是这条约定可以一句话概括：

> **原点在靠近电机的那一端，坐标从原点朝"离开电机"的方向单侧展开。**

本机 Y 的电机在左端、Z 的电机在顶端，所以 **Y 增大 = 向右，Z 增大 = 向下**。这个不变式由
``AxisProfile.home_position`` 派生并可断言（回零落点必须等于原点 0）。

⚠️ **2026-09-15 更正**：Z 原先写成 `-212…0 / 向上为正 / 正限位回零`，方向整好反了——
`homeDir=1`（正限位）在 Z 上指向**远离电机端**，于是回零会往下跑到远端开关、把**底部**
当成 0；而代码却把那个 0 当成了顶端（ADR-0007 §四 的"实证"正是这么误读的，见 ADR-0018）。
修正后 Z 与 Y 同构：原点在靠近电机端（顶端），坐标单侧增长（向下为正）。

速度与加减速是**每轴**上限。两轴插补时由 ``fmc.geometry.composite_limits`` 按方向
余弦折算成合成量，保证任一根轴都不越限。接近段与点动没有现场整定值，按目标速度/
加减速的固定比例派生（``approach_ratio`` / ``jog_ratio``）；比例沿用改造前的整定值
（接近 30/150 = 0.2），需要时可在轴定义里单独覆盖。

回零速度（``home_speed`` / ``home_acc``）**有下界，不是越慢越好**：它受控制器
``homeTime`` 约束——超时会中止回零并置位 ``MACHINE_HOME_OVERTIME``，此时**原点不可信**。
最坏寻零距离是该轴全行程，所以 ``home_speed ≥ travel_span / (余量系数 × homeTime)``。
各轴的推导写在对应 ``AxisProfile`` 上方（Y 因 4492 mm 行程而成为受约束的那一根）。

**本文件管不到的、必须在控制器里整定的一组参数**（说明书 §三.4「参数」页，
读写见 ``fmc.device_para``）：导程、细分、**软限位正/负极限**、回零超时时间。

其中软限位最容易漏：控制器出厂默认正负各 200mm，而 Y 行程是 4492mm。没整定的话
控制器会把 Y 的目标位置**自行截断到 200mm 并返回成功**，本程序侧完全看不出来。
所以巡检启动前应调一次 ``Fmc4030.check_soft_limits()``（ADR-0008）。
``home_timeout``（主机侧）应当大于控制器的回零超时时间，两层超时都保留。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# ---------- 回零方向（SDK 的 homeDir；.elo「设置回零运动参数」参数 4） ----------
#
# 这两个值说的是"找**哪一个限位开关**"，而开关装在哪一端由说明书定死：
# **正限位 = 远离电机端；负限位 = 靠近电机端**（§三.2）。本机两轴的原点都在靠近电机端，
# 所以**两轴都用 HOME_DIR_NEGATIVE**（Y 往左、Z 往上）。别按"向上/向右"去猜。

HOME_DIR_POSITIVE = 1   # 正限位回零（找**远离**电机那一端的开关）
HOME_DIR_NEGATIVE = 2   # 负限位回零（找**靠近**电机那一端的开关 = 原点所在端）

# ---------- 控制器寻址（出厂默认 192.168.0.30:8088，本机已改 IP） ----------

CONTROLLER_IP = "192.168.1.239"
CONTROLLER_PORT = 8088   # 出厂默认端口
DEVICE_ID = 1


@dataclass(frozen=True)
class AxisProfile:
    """一根轴的机械行程 + 运动/回零参数。

    ``travel_min`` / ``travel_max`` 兼作软限位包络：越界的目标坐标在下发控制器前
    就被拒（``Fmc4030.check_travel``）。
    """

    name: str            # "Y" | "Z"，同时是 Line_2Axis 虚拟坐标的取值顺序
    index: int           # 控制器轴号：0=X、1=Y、2=Z（SDK 与 .elo 同此编号）
    travel_min: float
    travel_max: float
    travel_speed: float  # 目标速度 mm/s（该轴上限）
    travel_acc: float    # 目标加/减速度 mm/s²
    home_dir: int        # HOME_DIR_POSITIVE | HOME_DIR_NEGATIVE
    home_speed: float    # 回零速度 mm/s
    home_acc: float      # 回零加/减速度 mm/s²
    home_release: float  # 回零脱落（脱离限位开关）距离 mm
    #: 坐标**增大**时机构往哪个物理方向走（``"left"``/``"right"``/``"up"``/``"down"``）。
    #: 运动学本身不依赖它（行程 + 回零方向已经定死了框架）；它只服务于两件事：
    #: 把**图像**方向映射成轴方向（`patrol.framing` 的 sign_z）、以及页面/文档的文案。
    #: 之所以要有这个字段：Z 的方向搞反过一次（ADR-0018），而"向上为正"这句话在代码里
    #: 曾散落在注释里，改一处漏一处。
    positive_towards: str = ""
    approach_ratio: float = 0.2   # 接近段 = 目标速度/加减速 × 该比例
    jog_ratio: float = 0.2        # 点动档   = 目标速度/加减速 × 该比例

    @property
    def travel_span(self) -> float:
        return self.travel_max - self.travel_min

    @property
    def home_position(self) -> float:
        """回零落点坐标：正限位回零落在行程上限，负限位回零落在下限。"""
        return self.travel_max if self.home_dir == HOME_DIR_POSITIVE else self.travel_min

    @property
    def approach_speed(self) -> float:
        return self.travel_speed * self.approach_ratio

    @property
    def approach_acc(self) -> float:
        return self.travel_acc * self.approach_ratio

    @property
    def jog_speed(self) -> float:
        return self.travel_speed * self.jog_ratio

    @property
    def jog_acc(self) -> float:
        return self.travel_acc * self.jog_ratio

    def contains(self, pos: float) -> bool:
        """pos 是否落在该轴机械行程内（含端点，留浮点余量）。"""
        return self.travel_min - 1e-6 <= pos <= self.travel_max + 1e-6


@dataclass(frozen=True)
class MotionProfile:
    """一台机器的两轴参数 + 与轴无关的时序参数。

    ``lamp_hold_s`` 是 M1/M2 之间**唯一**允许的差异（ADR-0002）。
    """

    y: AxisProfile
    z: AxisProfile
    approach_offset: float = 5.0      # 接近段长度 mm
    decay_s: float = 0.4              # 到位后机械振动衰减
    lamp_settle_s: float = 0.15       # 补光灯点亮到稳定
    lamp_after_s: float = 0.2         # 采图完成后的冗余窗口
    lamp_hold_s: float | None = None  # 固定灯窗秒数；None=由采图返回决定（M1）
    travel_timeout: float = 300.0     # 运动到位确认超时 s（覆盖 4492 mm 长行程）
    jog_timeout: float = 300.0        # 点动到位确认超时 s
    home_timeout: float = 120.0       # 回零到位确认超时 s（**主机侧**兜底）

    @property
    def axes(self) -> tuple[AxisProfile, AxisProfile]:
        """按 Line_2Axis 虚拟坐标的取值顺序返回两轴（Y 在前、Z 在后）。"""
        return (self.y, self.z)

    @property
    def axis_indices(self) -> tuple[int, int]:
        return (self.y.index, self.z.index)

    @property
    def axis_mask(self) -> int:
        """两轴组合号：SDK ``Line_2Axis`` 的 axis 参数 / .elo「启动两轴直线插补」参数 1。

        低三位按位表示选中的轴（0x03=X+Y、0x05=X+Z、0x06=Y+Z），由轴号派生而非手写
        字面量，改轴接线时不会漏改。
        """
        mask = 0
        for spec in self.axes:
            mask |= 1 << spec.index
        return mask

    @property
    def travel_limits(self) -> tuple[tuple[float, float], ...]:
        """各轴的 (速度上限, 加速度上限)，顺序同 ``axes``（供合成量折算）。"""
        return tuple((a.travel_speed, a.travel_acc) for a in self.axes)

    @property
    def approach_limits(self) -> tuple[tuple[float, float], ...]:
        return tuple((a.approach_speed, a.approach_acc) for a in self.axes)

    @property
    def jog_limits(self) -> tuple[tuple[float, float], ...]:
        """M0 手动档（点动/单段绝对）的各轴 (速度, 加速度) 上限。"""
        return tuple((a.jog_speed, a.jog_acc) for a in self.axes)

    def by_index(self, index: int) -> AxisProfile:
        for spec in self.axes:
            if spec.index == index:
                return spec
        raise KeyError(f"未接线的轴号 {index}（本机仅 Y={self.y.index}、Z={self.z.index}）")

    def by_name(self, name: str) -> AxisProfile:
        key = name.strip().upper()
        for spec in self.axes:
            if spec.name == key:
                return spec
        raise KeyError(f"未知轴名 {name!r}（本机仅 {self.y.name} / {self.z.name}）")


# ---------- 现场整定值：Y（水平长行程，向右为正，左端回零） ----------

# 回零速度不能随便降：**Y 轴的回零速度由控制器 homeTime=100 s 从下方卡住**。
# 回零是「从行程内任意位置出发、向固定的限位开关方向找开关」，最坏起点是行程另一端，
# 距离 = 4492 mm，所以 v_home ≥ 4492 / (0.5 × 100) = 89.8 mm/s 才能留出 2 倍余量。
# 取 90 mm/s ⇒ 最坏寻零 4492/90 ≈ 49.9 s + 升速段 ≈ 50 s：
#   - 控制器 homeTime 100 s  → 2.0 倍余量（超时会置位 MACHINE_HOME_OVERTIME，可被我们检出）
#   - 主机侧 home_timeout 120 s → 2.4 倍余量
# 再慢（例如 50 mm/s ≈ 90 s）就会顶到 homeTime 上，回零会随机报超时。
# 想更慢必须先改控制器 homeTime，但那会让「限位开关坏了」时的硬顶时间从 100 s 变成 200 s，
# 是用机构损伤换速度，不建议。现场原值 150 mm/s，降到这里取到"更慢且仍留余量"。
#
# 巡检档 150 / 1500（2026-09-12 整定）：不是拍脑袋，是被两件事夹出来的。
#   1. 控制器侧**硬上界** = 200 kHz（说明书 §一）/ 1052.632 脉冲·mm⁻¹ = 190 mm/s，
#      再快控制器也发不出来。150 mm/s = 158 kHz = 上界的 79%，仍留 21% 余量。
#   2. 改造前的现场值就是 150：现场给出的 Y **回零**档正是 150 / 1500（同一比例，
#      acc = 10 × speed ⇒ 升速时间恒 0.1 s），即这条轴在 150 这一档有现场依据；
#      而原先写的 50 才是没依据的（双轴改造时按"假想机器"推的，见 ADR-0007 背景）。
# 整轮时长对这档速度**极其敏感**：蛇形遍历一圈 Y 空程累计 20.8 m（5 层 × 11 框 ×
# 374.33 mm），50 → 150 把整轮从 ~9.5 min 压到 ~4.6 min（含单站固定开销）。
# 推导与对照表见 `docs/patrol/prod-deploy/speed-tune.py`（可重跑）。
#
# ⚠️ 残留风险（上机时唯一要盯的）：**驱动器最大输入频率未知**。控制器发得出 158 kHz，
# 不代表 FMDD50D40NOM 收得下；收不下会**静默丢步**——控制器位置计数器只数发出去的
# 脉冲，`real_pos` 照样"到位"，是采图偏位而不是报错。上机验证步骤见
# `motor-command-review.md` §2.2.1「巡检档上机验证」。
_Y_TRAVEL_SPEED = 150.0
_Y = AxisProfile(
    name="Y",
    index=1,
    travel_min=0.0,
    travel_max=4492.0,
    travel_speed=_Y_TRAVEL_SPEED,
    travel_acc=1500.0,            # = 10 × travel_speed ⇒ 升速时间 0.1 s（现场比例）
    home_dir=HOME_DIR_NEGATIVE,   # 反向（负限位 = 靠近电机端 = 左端）回零
    home_speed=90.0,
    home_acc=900.0,
    home_release=5.0,
    positive_towards="right",     # 坐标增大 = 向右（离开左端电机）
    # 点动档**不跟着巡检档放**：默认 0.2 的比例会把 Y 点动推高到 30 mm/s——示教时
    # 肉眼跟不上、容易过冲撞架。改造前的现场点动整定值是 10 mm/s（acc 100），
    # 这里显式=10/150 把它钉住（acc 1500 × 10/150 = 100 恰好也是现场值）。
    # 由 `test_derived_ladder_matches_the_pre_migration_tuning` 守护：改巡检档
    # 若忘了这点动，测试会直接失败。
    jog_ratio=10.0 / _Y_TRAVEL_SPEED,
)

# ---------- 现场整定值：Z（竖直短行程，**向下为正**，顶端回零） ----------
#
# ⚠️ 方向别搞反（ADR-0018）：Z 的电机在**顶端**，按"原点在靠近电机端"这条约定（说明书
# §三.2：负限位 = 靠近电机端），顶端是原点、坐标**往下**增长，所以回零是 `homeDir=2`
# （往**上**找近端开关），行程 0…212 而不是 -212…0。
#
# Z 只有 212 mm 行程，没有任何超时压力（212/20 ≈ 10.6 s vs homeTime 100 s），
# 因此直接取目标速度整定值，不再保留"回零比巡检快 2.5 倍"的旧比例。
_Z = AxisProfile(
    name="Z",
    index=2,
    travel_min=0.0,
    travel_max=212.0,
    travel_speed=20.0,
    travel_acc=200.0,
    home_dir=HOME_DIR_NEGATIVE,   # 向上（负限位 = 靠近电机端 = 顶端）回零
    home_speed=20.0,
    home_acc=200.0,
    home_release=5.0,
    positive_towards="down",      # 坐标增大 = 向下（离开顶端电机）
)

# M1（主机驱动巡检）与 M2（控制器脱机脚本）共用同一组轴参数，只差灯窗（ADR-0002）
M1 = MotionProfile(y=_Y, z=_Z)
M2 = replace(M1, lamp_hold_s=1.0)

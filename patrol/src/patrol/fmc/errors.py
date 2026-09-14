"""FMC4030 SDK 错误码 → 异常（错误码表见《FMC4030二次开发库详解》）。"""

_ERROR_TEXT = {
    -1: "连接失败（检查网线连接、IP 地址及端口号，或重启控制器）",
    -5: "数据发送失败（检查网络连接）",
    -6: "数据接收控制错误",
    -7: "接收数据错误（检查网络连接）",
    -8: "空指针错误（传入参数为空）",
    # 厂商手册（《FMC4030二次开发库详解》）的错误码表只列到 -8，-10 未收录。
    # 下面这条由**现场实测**确定，只描述观测到的情形，不替厂商下定义：
    #   2026-09-12 对仍在运动的 Y 轴下发 FMC4030_Home_Single_Axis → 返回 -10。
    #   （同一天对运动中轴下发 Jog/Line 则是**静默丢弃**，不报错，见 client.wait_stop。）
    -10: "轴忙：控制器拒绝了这条运动指令（实测：对仍在运动的轴下发回零时返回）",
}


class FmcError(RuntimeError):
    """SDK 调用返回负值时抛出；code 为 SDK 原始返回值。"""

    def __init__(self, code: int, action: str = "") -> None:
        self.code = code
        detail = _ERROR_TEXT.get(code, f"未知错误码 {code}")
        msg = f"FMC4030 调用失败: {detail} (code={code})"
        if action:
            msg = f"[{action}] {msg}"
        super().__init__(msg)


class MotionTimeoutError(FmcError):
    '''运动域超时：轴未在期限内确认到位（作为 FmcError 子类，可被 except FmcError 捕获）。'''

    def __init__(self, action: str = '') -> None:
        self.detail = f'到位确认超时: {action}' if action else '到位确认超时'
        FmcError.__init__(self, code=0, action=self.detail)

    def __str__(self) -> str:
        # 域错误不是 SDK 返回值，消息直接就是 detail，不套「未知错误码 0」模板
        return self.detail


class TravelLimitError(FmcError):
    '''目标坐标超出该轴机械行程（软限位），在**下发控制器之前**拒绝。

    作为 FmcError 子类，可被 except FmcError 捕获（同 MotionTimeoutError）。
    '''

    def __init__(self, axis_name: str, pos: float, lo: float, hi: float) -> None:
        self.axis_name = axis_name
        self.pos = pos
        self.limits = (lo, hi)
        self.detail = f'{axis_name} 轴目标 {pos:g} mm 超出行程 [{lo:g}, {hi:g}] mm，已拒绝下发'
        FmcError.__init__(self, code=0, action=self.detail)

    def __str__(self) -> str:
        return self.detail


class HomeTimeoutError(MotionTimeoutError):
    '''回零未在期限内完成（读回「轴回零超时」位，见 FMC4030.h MACHINE_HOME_OVERTIME）。

    控制器在长时间没触发限位开关时会自行终止回零，此时点位不能当作原点使用——
    必须抛错而不是静默返回，否则后续所有绝对坐标都会整体偏移。
    '''

    def __init__(self, axis_name: str = '') -> None:
        self.axis_name = axis_name
        self.detail = (
            f'回零超时：{axis_name} 轴未触发限位开关，控制器已终止回零（原点不可信）'
            if axis_name else '回零超时'
        )
        FmcError.__init__(self, code=0, action=self.detail)

    def __str__(self) -> str:
        return self.detail


class SoftLimitMismatchError(FmcError):
    '''控制器自带软限位窄于机械行程（说明书 §三.4：出厂默认 ±200mm）。

    这属于**现场整定缺项**而非运动错误：控制器会把越界目标自行截断，表现为
    "指令下发成功但只走一小段"。作为 FmcError 子类，可在巡检启动前拦下。
    '''

    def __init__(self, issues) -> None:
        self.issues = list(issues)
        self.detail = '控制器软限位未按行程整定：' + '；'.join(str(i) for i in self.issues)
        FmcError.__init__(self, code=0, action=self.detail)

    def __str__(self) -> str:
        return self.detail


class TravelShortfallError(FmcError):
    '''一次移动**没有按指令执行完成**——按控制器自己的计数，落点离目标很远。

    ## 先说清这条证据的边界（重要，别过度解读）

    FMC4030 是**脉冲型控制器，没有任何位置反馈**：``real_pos`` 是"我发了多少脉冲"的
    自述，不是测出来的位置。所以本异常的含义严格来说是：

        移动结束后，**控制器计数器**的值 ≠ 指令值

    正常（无丢步、机械正常）时这两者**必然相等**——因为数的是自己发的脉冲。实测最大相差
    **4300 mm**，这在正常前提下不可能出现。所以"某个环节出问题了"是确凿的。

    **但本异常不能证明滑块物理上停在哪儿。** 没有反馈，分不清"控制器提前放弃运动"
    与"滑块被卡住、控制器等超时后放弃"——两者对读数的影响一样。本节只描述**计数口径**。

    ## 另一条不依赖读数的证据：时间

    同一条 ``Y=0 → 4442 mm`` 的指令，正常时稳定 30.0 s（= 4442/150 + 斜坡），
    异常时出现 **0.7 / 5.1 / 8.9 / 11.3 s**。以 150 mm/s 走完 4442 mm 在物理上不可能
    少于 ~30 s，所以**控制器确实中途改变了这次运动的状态**，且不报任何错误码。

    ## 为什么必须拦

    不主动比较"指令 vs 计数"，这个异常就完全不可见。运维会拿到一次"成功"的移动，
    而`real_pos` 已不再等于指令——相机可能对着错的地方拍图，元数据却照写该站位编号。

    ## 处置

    实测它**不具预测性**（20 趟里 2 趟；异常后的下一趟通常正常），且回零始终能把
    坐标系拉回硬限位（落点 0.000），所以处置是**跳过该站 + 重新建立参照**，
    而不是把整轮作废。见 ``patrol.round`` 对它的单独分类与 `patrol.journal` 的取证。
    '''

    def __init__(self, axis_name: str, target: float, actual: float, tol: float) -> None:
        self.axis_name = axis_name
        self.target = target
        self.actual = actual
        self.tol = tol
        self.delta = target - actual
        # 措辞刻意不含"停在/短停"这类物理断言：``actual`` 是控制器计数，不是实测位置。
        self.detail = (
            f'{axis_name} 轴这次移动未执行完成：指令 {target:.3f} mm，'
            f'而控制器计数器报 {actual:.3f} mm（差 {self.delta:+.3f} mm，容差 {tol:g} mm）。'
            f'注意这是计数口径——控制器无位置反馈，无法据此判断滑块实际停在哪里'
        )
        FmcError.__init__(self, code=0, action=self.detail)

    @property
    def shortfall(self) -> float:
        """``target - actual``（保留旧名：现场脚本与测试按这个字段读偏差）。"""
        return self.delta

    def __str__(self) -> str:
        return self.detail

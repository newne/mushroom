# 01 — Python 封装 FMC4030 SDK：连通、回零、点动

**Spec:** ../spec.md §2.3、§4.2、§4.6

**What to build:** 在 Ubuntu 主机上用 Python（ctypes + Linux 版 libFMC4030）封装出可复用的控制器客户端：连接/关闭、X/Y 双轴回零、点动与绝对运动、状态心跳轮询、错误码转异常。端到端行为：跑一条命令，滑块回零后移动到指定坐标并安全停止，终端实时打印位置与轴状态。

**Blocked by:** 00 — 仓库治理：git 化、目录重组、workspace 骨架（代码落位在 `patrol/fmc/`，先有包骨架）

**Status:** ready-for-agent

- [ ] 连接/关闭控制器成功，退出后能再次连接（资源正确释放）
- [ ] X、Y 双轴回零完成（脱落距离、方向按设计），状态可查询
- [ ] 点动/绝对运动到目标坐标，到位判定可靠（Check_Axis_Is_Stop 轮询）
- [ ] SDK 负返回值（-1/-5/-7/-8）转为带中文说明的异常
- [ ] 心跳轮询 ≤30s 周期持续运行不掉线（规避 1 分钟无交互断连）
- [ ] 阻塞/非阻塞库选型定案（用非阻塞 + 轮询）并记录

## Comments

- 2026-08-30 代码完成（commit `67a4fa2`/`829ec8d`）：`patrol/fmc/` 下 errors（错误码→中文异常）、status（结构体解析纯函数）、geometry（两段速接近点）、loader（.so 加载，FMC4030_LIB_PATH 可覆盖）、client（open/close/get_status/home/goto 两段速/lamp/脚本下发），19 项单测通过（假库注入）。
- 选型已定案：非阻塞库 + `Check_Axis_Is_Stop` 轮询（`wait_stop` 封装）。
- **待现场验收**：真机连接/关闭、双轴回零、点动到位、心跳长跑——以上验收项需 FMC4030 实机，保持未勾选。

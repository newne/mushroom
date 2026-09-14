# ADR-0002：M1/M2 运动参数同源于 motion_profile

日期：2026-08-31
状态：已接受
背景：架构评审候选 #4。库房导轨只有一组物理运动参数，但 M1（主机驱动巡检，
`patrol.orchestrator`/`patrol.fmc.client`）与 M2（控制器脱机 .elo 脚本，
`patrol.elo.generator`）两条执行路径各自抄写常量，已经发生漂移
（巡检速度 M1=150 而 M2=100 mm/s，无设计依据）。

## 决定

- 运动参数（巡检/接近/回零速度与加减速、接近段长度、振动衰减）**单源**于
  `patrol.motion_profile.MotionProfile`；两条路径 import 同一份值。
- 两种模式间**唯一**的合法差异是补光窗口保持方式：M1 由采图返回决定关灯
  （`lamp_hold_s=None`），M2 依赖相机 SD 卡计划快照、用固定灯窗
  （`lamp_hold_s=1.0`）。差异必须是 profile 的显式字段，不允许散落常量。
- 现场整定只改 `motion_profile.py`（并同步重编译下发 M2 脚本）。

## 后果

- 漂移在 import 时即消除；参数改动一次生效两条路径。
- M2 脚本由 `elo.generate` 生成，参数更新后必须重新下发（生成器即文档）。

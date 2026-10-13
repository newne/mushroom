# 14 — 「当前是否处于加湿段」的只读信号（给巡检拍图避让）

**Spec:** ../spec.md §2.2（实际参数）、`fmc4030-camera-scan/spec.md` §6；消费者 = 巡检手（hold/defer）

**归属：** 信号与接口（`analysis/**`）= 环境官；hold/defer 的**写入侧** = 巡检手（本票不改巡检侧代码）。
**Status:** ready-for-agent（实现可后置；**阈值定标未完成前只出"未定标"状态**）

**What to build:** 一个**只读**信号，回答"某库房现在是否处于加湿段"，判据写清、可复算、可测，
数据只取**已经采到的东西**，不新造硬件假设。消费者据此自动 hold/defer，不再靠人按按钮。

## 0. 一页结论（先说坏消息）

**现有已采数据不足以判定"加湿器正在喷"**，因此本票 v1 交付的是
**"可复算的原料 + 明确标 `uncalibrated` 的合成状态"**，不是拍脑袋的启发式。

缺的两样，缺一不可：

1. **加湿器的运行状态**。我们登记的点位表（`src/configs/monitoring_points_config.json:207-299`）
   里，加湿器设备 `TD1_Q{1..4}MDHUM01/HUM02` 只有三个点：`Model`（加湿模式：0 关闭 /
   1 自动 / 2 手动）、`On`（加湿**开启设定**）、`Off`（加湿**停止设定**）—— **全是设定值，
   没有一个运行反馈点**。"设成自动"不等于"此刻在喷"。
2. **阈值与雾散时间的现场定标**。多高的 RH 会让画面过曝、停喷后水汽还要多久散掉
   （现有 hold 的现场口径是"保持结束 + 20 分钟"，见 `deploy/src/deploy/patrol_trigger.py:17-19`），
   这两条都得拿现场数据标，不能由我拍。

⇒ 在定标之前，接口**不许**给出 `humidifying=true` 这种硬判定。三态语义见 §2。

## 1. 手里有什么（只读、已采）

| 来源 | 有什么 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| `operations`（EMS 命令审计，逐条） | 加湿器点位的**设定变更**事件：谁、何时、把 `Model`/`On`/`Off` 改成什么（`device_name` 形如 `607库-右加湿器`、`content` 形如 `加湿开启设定[5]`；夹具见 `analysis/tests/test_audit.py:25-26`） | "有人/远程改过设定" | 改了之后**喷了多久**、**此刻喷没喷**；且设定不常改，事件稀疏 |
| TDengine 原始分钟序列（`GET /environment?granularity=raw`，上限 6 h） | 同库房 `MDTM01::Hatmosp` 的**实测 RH** 分钟级 | 水汽的**结果**（RH 高/在升） | 高 RH 也可能是通风差/降雨天；"RH 高"≠"雾滴正在挡光" |
| `environment` 小时聚合 | RH mean/min/max + `n` | 长时间尺度的背景 | 分钟级起止，**判不了"现在"** |
| 加湿器**运行状态** | **没有**（见 §0.1） | —— | 本票的核心缺口 |

## 2. 交付物：`GET /humidity/state?zone=`

只读接口，返回（所有字段都可复算：同一输入同一输出）：

```json
{
  "zone": "611",
  "state": "uncalibrated",          // uncalibrated | unknown | humidifying | clear
  "calibrated": false,
  "thresholds_used": null,          // 定标后回填 {rh_pct: .., mist_minutes: ..}
  "basis": {
    "last_humidifier_command": {"ts": "2026-10-13T15:42:00", "device": "611库-右加湿器",
                                 "point": "加湿开启设定", "value": "5",
                                 "content": "加湿开启设定[5]", "operator": "..."},
    "rh_recent": {"from": "2026-10-13T15:55:00", "to": "2026-10-13T16:55:00",
                  "mean": 91.2, "slope_pct_per_h": 1.4, "n": 55},
    "source": "operations+tdengine", "computed_at": "2026-10-13T16:57:12"
  },
  "caveats": ["加湿器运行状态未采集：state 无法由'正在喷'判定", "阈值未定标"]
}
```

**三态语义（必须写进接口文档，消费方按这个用）：**

| state | 何时给出 | 消费方可以做什么 |
| --- | --- | --- |
| `uncalibrated` | **当前默认**：阈值未定标 | **不许**自动 hold；保持现状（人工按钮） |
| `unknown` | 该 zone 在窗口内没有任何 RH 样本、或 `operations` 取不到 | **不许**自动 hold（"取不到"不等于"没有加湿"） |
| `humidifying` | 定标后：RH 超阈值窗口 + 最近有开启类操作且未超雾散时长 | 可以自动 hold/defer |
| `clear` | 定标后：RH 低于阈值且无近期开启类操作 | 不阻塞巡检（**不是**保证画面一定好） |

参数（RH 阈值、雾散时长、窗口长度）**全部从配置读**，不硬编码；配置缺省 ⇒ `uncalibrated`。

## 3. 定标怎么做（follow-up 小票，现场提供数据）

1. 现场连做 N 次（≥5 次）加湿，记录每次：按下/解除人工 hold 的时刻 `t_hold`/`t_release`。
2. 对齐该区间 TDengine RH 分钟序列，标出：RH 从多少开始画面变差（过曝阈值）、
   停喷后多久画面恢复（雾散时长）。
3. 把两个数写进配置 → 接口自动从 `uncalibrated` 切到 `humidifying`/`clear`。
4. 验收：同一批现场数据回放，接口判定与人工 hold 的重合度（漏判优先于误判：
   宁可多 defer 一次，也不让一整轮 48 帧废掉）。

## 4. 待现场核实 / 未知（不许猜）

1. **加湿器有没有运行反馈点**：① TDengine 里是否存在 `TD1_Q{1..4}MDHUM01/HUM02`
   子表且带状态列（一条 `SHOW STABLES`/`DESCRIBE` 就能确认）；
   ② 算法侧 `src/environment/processor.py:234` 那一带的"左右加湿器 on/off/status"
   是从哪个接口取的（`src/utils/create_table.py:102` 提到 "running状态!=3"）——
   若有**现成的运行状态读通路**，本票的缺口就能补上，但那属别人的域，走票据。
2. EMS 平台是否有"设备运行状态"查询接口（与命令审计同一个 `X-AUTH-TOKEN`）。
3. 阈值与雾散时长的数值（现场定标，见 §3）。

## 5. 明确不做

- 不改巡检侧（`patrol/**`、`deploy/**`）代码：hold/defer 的写入归巡检手。
- 不新增硬件假设（不假装有雾滴/照度传感器）。
- 不在未定标时给"确定性"结论 —— 宁可是 `uncalibrated`。

## Comments

- 2026-10-13 开票（Chief 指示，排在"调度侧自动避让"之前）。写票时只读了仓库里的点表、
  审计夹具与 hold 口径，**未连生产**；`state` 的判据与"缺什么"是结论，阈值是待定标项。

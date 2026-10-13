# 14 — 「当前是否处于加湿段」的只读信号（给巡检拍图避让）

**Spec:** ../spec.md §2.2（实际参数）、`fmc4030-camera-scan/spec.md` §6；消费者 = 巡检手（hold/defer）

**归属：** 信号与接口（`analysis/**`）= 环境官；hold/defer 的**写入侧** = 巡检手（本票不改巡检侧代码）。
**Status:** ready-for-agent（实现可后置；**阈值定标未完成前只出"未定标"状态**）

**What to build:** 一个**只读**信号，回答"某库房现在是否处于加湿段"，判据写清、可复算、可测，
数据只取**已经采到的东西**，不新造硬件假设。消费者据此自动 hold/defer，不再靠人按按钮。

## 0. 一页结论（先说坏消息）

**现有已采数据不足以判定"加湿器正在喷"**，因此本票 v1 交付的是
**"可复算的原料 + 明确标 `uncalibrated` 的合成状态"**，不是拍脑袋的启发式。

**更新（2026-10-13，发版官只读实测）**：加湿器在 TDengine 里**是存在的** ——
8 个 stable `SUP_TD1_Q{1..4}MDHUM01/HUM02`，列
`ts, OnAlarm, Model, On, Off, Running, device TAG`，而且有数据
（`SUP_TD1_Q1MDHUM01` 20.07 万行，最新一行 = 2026-10-13T16:32 CST；对照温度表 19.37 万行）。
但**仍然判不了"正在喷"**：

1. **`Running` 不是布尔位**：Q1HUM01 最新值 = **2**（不是 0/1）。它更像模式/档位。
   `Running>0 ⇒ 正在喷`是**未经验证的假设**，不许当判据用。
2. `On=4` / `Off=8` 的形状像**时段或时长**，不是开关；语义待现场确认。
3. 8 个 stable 里**只有 5 个有子表**（Q1HUM01/HUM02、Q2HUM01、Q3HUM02、Q4HUM02）；
   `Q2HUM02`、`Q3HUM01`、`Q4HUM01` 在 `ins_tables` 里查不到 ⇒ 这三路**没有上报**，
   相关库房不能假装有加湿信号。
4. 取数坑：`On`/`Off` 是 **TDengine 保留字，必须反引号**，否则查询静默返回空。

⇒ 缺的两样（缺一不可）：**① `Running`/`On`/`Off` 的语义**（现场或厂商口径）；
**② 阈值与雾散时长的现场定标**。在此之前接口只出 `uncalibrated`/`unknown`。

**但有一件现在就该做**：把 HUM 的 `Model/On/Off/Running` 也纳进采集
（`analysis.env.tdengine` 现在只取 TM01 与 LIGHT01）。**先存原始、后判定** ——
不定标时也无法回溯出"当时加湿器是什么状态"，那定标永远做不了。

## 1. 手里有什么（只读、已采）

| 来源 | 有什么 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| `operations`（EMS 命令审计，逐条） | 加湿器点位的**设定变更**事件：谁、何时、把 `Model`/`On`/`Off` 改成什么（`device_name` 形如 `607库-右加湿器`、`content` 形如 `加湿开启设定[5]`；夹具见 `analysis/tests/test_audit.py:25-26`） | "有人/远程改过设定" | 改了之后**喷了多久**、**此刻喷没喷**；且设定不常改，事件稀疏 |
| TDengine 原始分钟序列（`GET /environment?granularity=raw`，上限 6 h） | 同库房 `MDTM01::Hatmosp` 的**实测 RH** 分钟级 | 水汽的**结果**（RH 高/在升） | 高 RH 也可能是通风差/降雨天；"RH 高"≠"雾滴正在挡光" |
| `environment` 小时聚合 | RH mean/min/max + `n` | 长时间尺度的背景 | 分钟级起止，**判不了"现在"** |
| TDengine 加湿器表 `SUP_TD1_Q{1..4}MDHUM01/HUM02` | `Model` / `On` / `Off` / `Running`（**有数据，8 个 stable，5 个有子表**） | 设备侧的原始读数 —— **采集起来就有定标素材** | **`Running` 语义未确认（实测值 = 2，不是布尔）**，所以还判不了"正在喷"；另 3 路无上报 |

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

1. **`Running` 的语义**：实测最新值 = **2**（不是 0/1 布尔），`On=4`/`Off=8` 像时段/时长。
   0/1/2 各代表什么、`Running` 到底是不是运行反馈 —— 现场或厂商口径确认后本票才能出硬判定。
   （算法侧 `src/environment/processor.py:234` 一带的"左右加湿器 on/off/status"取自哪个接口，
   可以顺带对一下，可能已有现成口径。）
2. **8 个 stable / 5 个子表**：`Q2HUM02`、`Q3HUM01`、`Q4HUM01` 没上报 ⇒ 对应库房无信号，
   接口必须显示 `unknown`（不是 `clear`）。
3. 阈值与雾散时长的数值（现场定标，见 §3）。
4. EMS 平台是否有独立的"设备运行状态"查询接口（与命令审计同一个 `X-AUTH-TOKEN`）。

## 5. 实施顺序（我）

1. **先采集**：`analysis.env.tdengine` 增加 HUM 表的 `Model/On/Off/Running`（反引号标识符！），
   入库口径按 ADR-0023（小时聚合；`Running` 与 `lamp_state` 一样按众数存）。
2. **再出接口**：`GET /humidity/state`，恒出 `uncalibrated`/`unknown`（§2），
   把原料（最近一次加湿器命令、RH 窗口统计）先给巡检手看，**不参与自动 hold**。
3. **定标后**才切 `humidifying`/`clear`。

## 5. 明确不做

- 不改巡检侧（`patrol/**`、`deploy/**`）代码：hold/defer 的写入归巡检手。
- 不新增硬件假设（不假装有雾滴/照度传感器）。
- 不在未定标时给"确定性"结论 —— 宁可是 `uncalibrated`。

## Comments

- 2026-10-13 开票（Chief 指示，排在"调度侧自动避让"之前）。写票时只读了仓库里的点表、
  审计夹具与 hold 口径，**未连生产**；`state` 的判据与"缺什么"是结论，阈值是待定标项。
- 2026-10-13 更新（发版官只读实测，**证据在手，不是推断**）：HUM 8 个 stable 存在且有数据
  （Q1HUM01 20.07 万行，最新 16:32 CST），列含 `Running`；**但 `Running` 不是 0/1 布尔
  （实测值 = 2）**，`On=4`/`Off=8` 像时段；只有 5/8 有子表；`On`/`Off` 是保留字必须反引号。
  ⇒ §0、§1、§4 已同步：**信号改为"先采集原始、定标后再判定"**，未定标前恒 `uncalibrated`。

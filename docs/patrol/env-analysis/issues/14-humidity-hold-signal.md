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
但"正在喷"仍不能由单个字段直接读出：

1. **`Running` 的语义有权威定义（2026-10-13 Chief 校正 —— 我原来登记成"未知"是错的）**：
   `docs/technical/reference/设备监控点参考.md:239-247`（§3 加湿器 → status）与汇总表
   `:567-574` 写明 **`0`=加湿自动运行、`1`=加湿手动运行、`2`=加湿停止、`3`=加湿关闭**
   （`docs/technical/troubleshooting/chinese_encoding_fix.md:143` 同一份映射）。
   所以发版官实测的 `Running = 2` 是**加湿停止**，不是"正在加湿"。
   ⇒ 判据：`Running ∈ {0,1}` = 设备处于**运行态**；`Running ∈ {2,3}` = **明确不在加湿**。
   ⚠️ 但**"运行态"≠"正在喷水"**：自动模式下它按湿度上下限**间歇启停**，
   所以还得看 RH 的方向 —— 复合判据见 §2。
2. `On=4` / `Off=8` 的形状像**时段或时长**，不是开关；这层语义仍待现场确认（不影响本票判据）。
3. 8 个 stable 里**只有 5 个有子表**（Q1HUM01/HUM02、Q2HUM01、Q3HUM02、Q4HUM02）；
   `Q2HUM02`、`Q3HUM01`、`Q4HUM01` 在 `ins_tables` 里查不到 ⇒ 这三路**没有上报**，
   相关库房只能 `unknown`。
4. 取数坑：`On`/`Off` 是 **TDengine 保留字，必须反引号**，否则查询静默返回空。

⇒ 现在只剩一样要等现场：**RH 上升阈值与雾散时长的定标**（`Running` 那层已有据可依、
不需要定标）。需要 RH 判据才能区分的情形，定标前一律出 `uncalibrated`。

**但有一件现在就该做**：把 HUM 的 `Model/On/Off/Running` 也纳进采集
（`analysis.env.tdengine` 现在只取 TM01 与 LIGHT01）。**先存原始、后判定** ——
不定标时也无法回溯出"当时加湿器是什么状态"，那定标永远做不了。

## 1. 手里有什么（只读、已采）

| 来源 | 有什么 | 能证明什么 | 不能证明什么 |
| --- | --- | --- | --- |
| `operations`（EMS 命令审计，逐条） | 加湿器点位的**设定变更**事件：谁、何时、把 `Model`/`On`/`Off` 改成什么（`device_name` 形如 `607库-右加湿器`、`content` 形如 `加湿开启设定[5]`；夹具见 `analysis/tests/test_audit.py:25-26`） | "有人/远程改过设定" | 改了之后**喷了多久**、**此刻喷没喷**；且设定不常改，事件稀疏 |
| TDengine 原始分钟序列（`GET /environment?granularity=raw`，上限 6 h） | 同库房 `MDTM01::Hatmosp` 的**实测 RH** 分钟级 | 水汽的**结果**（RH 高/在升） | 高 RH 也可能是通风差/降雨天；"RH 高"≠"雾滴正在挡光" |
| `environment` 小时聚合 | RH mean/min/max + `n` | 长时间尺度的背景 | 分钟级起止，**判不了"现在"** |
| TDengine 加湿器表 `SUP_TD1_Q{1..4}MDHUM01/HUM02` | `Model` / `On` / `Off` / `Running`（**有数据，8 个 stable，5 个有子表**） | **`Running` 语义已明确**（`设备监控点参考.md:239-247`）：`{0,1}`=运行态、`{2,3}`=不在加湿 | "运行态"**不等于**"正在喷"（自动模式间歇启停）；另 3 路无上报 |

## 2. 交付物：`GET /humidity/state?zone=`

只读接口，返回（所有字段都可复算：同一输入同一输出）：

```json
{
  "zone": "611",
  "state": "uncalibrated",          // uncalibrated | unknown | humidifying | standby | clear
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
  "caveats": ["RH 阈值未定标：standby/humidifying 还分不开", "Q2HUM02/Q3HUM01/Q4HUM01 无上报"]
}
```

**判据（复合，按优先级从上到下；消费方按 state 用）：**

1. 窗口内**没有 `Running` 样本或没有 RH 样本**（含那 3 路无上报的库房）⇒ `unknown`。
2. `Running ∈ {2,3}`（加湿停止 / 加湿关闭，`设备监控点参考.md:239-247`）⇒ **`clear`**
   —— 这条**不需要阈值**，是明确结论。
3. `Running ∈ {0,1}`（自动/手动**运行态**）⇒ 才需要 RH 判据：
   - 阈值**未定标** ⇒ `uncalibrated`（分不清 `standby` 与 `humidifying`）；
   - 定标后：RH 在窗口内**上升过阈值**（或处于雾散时长内的遗留高位）⇒ `humidifying`；
   - 定标后：RH 未显著上升 ⇒ `standby`（设备在运行态、此刻没喷，随时可能开始）。
4. `operations` 里最近的**开启类**事件只作**辅助证据**（说明"刚被启动过"），
   **不能单独定 `humidifying`**：设定改了不等于在喷。

| state | 何时给出 | 消费方可以做什么 |
| --- | --- | --- |
| `unknown` | 缺 `Running` 或 RH 样本 | **不许**自动 hold（"取不到"≠"没有加湿"） |
| `clear` | `Running ∈ {2,3}`；或定标后 `{0,1}` 且 RH 未升过阈值 | 不阻塞巡检（**不是**保证画面一定好） |
| `uncalibrated` | `Running ∈ {0,1}` **且阈值未定标** | **不许**自动 hold；保持现状（人工按钮） |
| `standby` | 定标后：`Running ∈ {0,1}` 且 RH 未显著上升 | 不阻塞；提示"加湿随时可能开始" |
| `humidifying` | 定标后：`Running ∈ {0,1}` **且** RH 上升过阈值/仍在雾散窗口内 | 可以自动 hold/defer |

**配置项**：RH 上升阈值、雾散时长、窗口长度 —— 全部从配置读，缺省 ⇒ `uncalibrated`。
**`Running` 的枚举不放进配置**：它是仓库文档里的权威定义，写死在代码里（免得有人配错）。

## 3. 定标怎么做（follow-up 小票，现场提供数据）

1. 现场连做 N 次（≥5 次）加湿，记录每次：按下/解除人工 hold 的时刻 `t_hold`/`t_release`。
2. 对齐该区间 TDengine RH 分钟序列，标出：RH 从多少开始画面变差（过曝阈值）、
   停喷后多久画面恢复（雾散时长）。
3. 把两个数写进配置 → 接口自动从 `uncalibrated` 切到 `humidifying`/`clear`。
4. 验收：同一批现场数据回放，接口判定与人工 hold 的重合度（漏判优先于误判：
   宁可多 defer 一次，也不让一整轮 48 帧废掉）。

## 4. 待现场核实 / 未知（不许猜）

> **方法论（2026-10-13 记，本票翻车过一次）：写"未知"之前先查仓库文档。**
> `docs/technical/reference/设备监控点参考.md` 是这个项目**点位语义的权威源**，
> `docs/technical/troubleshooting/` 是它的补充；它已经回答过的问题不许再登记成
> "未验证假设"。我原来把 `Running` 判成"语义未知"，其实 §3 与汇总表早写了枚举 ——
> 这条教训比结论本身值钱。

1. **阈值与雾散时长的数值**（现场定标，见 §3）—— 本票**唯一**还缺的输入。
2. **`On=4` / `Off=8` 的语义**（像时段或时长，不是开关）：不影响本票判据，记着。
3. **8 个 stable / 5 个子表**：`Q2HUM02`、`Q3HUM01`、`Q4HUM01` 没上报 ⇒ 对应库房只能
   `unknown`（**不是 `clear`**）。
4. EMS 平台是否有独立的"设备运行状态"查询接口（与命令审计同一个 `X-AUTH-TOKEN`）——
   有则更省事，但**不再阻塞**：TDengine 的 HUM 表已经够本票用。

**已解决，不再挂起：`Running` 的语义** —— `{0,1}`=加湿自动/手动运行、`{2,3}`=加湿停止/关闭
（`设备监控点参考.md:239-247`、`:567-574`；`chinese_encoding_fix.md:143` 同源）。

## 5. 实施顺序（我）

1. **先采集**：`analysis.env.tdengine` 增加 HUM 表的 `Model/On/Off/Running`（反引号标识符！），
   入库口径按 ADR-0023（小时聚合；`Running` 与 `lamp_state` 一样按众数存）。
2. **再出接口**：`GET /humidity/state` —— `Running ∈ {2,3}` 直接给 `clear`；
   `Running ∈ {0,1}` 且阈值未定标给 `uncalibrated`；无数据给 `unknown`；
   把原料（`Running` 众数、最近一次加湿器命令、RH 窗口统计）一并返回，
   但 `uncalibrated`/`unknown` **不参与自动 hold**。
3. **定标后**才切 `standby`/`humidifying`。

## 6. 明确不做

- 不改巡检侧（`patrol/**`、`deploy/**`）代码：hold/defer 的写入归巡检手。
- 不新增硬件假设（不假装有雾滴/照度传感器）。
- 不在未定标时给"确定性"结论 —— 宁可是 `uncalibrated`。

## Comments

- 2026-10-13 开票（Chief 指示，排在"调度侧自动避让"之前）。写票时只读了仓库里的点表、
  审计夹具与 hold 口径，**未连生产**；`state` 的判据与"缺什么"是结论，阈值是待定标项。
- 2026-10-13 更新（发版官只读实测，**证据在手，不是推断**）：HUM 8 个 stable 存在且有数据
  （Q1HUM01 20.07 万行，最新 16:32 CST），列含 `Running`；只有 5/8 有子表；
  `On`/`Off` 是保留字必须反引号。
- 2026-10-13 **第二次校正（Chief 指正，我错了一次）**：我原写"`Running` 语义未知、
  实测值 2 像模式/档位"——**仓库里早有权威定义**：
  `docs/technical/reference/设备监控点参考.md:239-247` 与汇总表 `:567-574`：
  `0`=加湿自动运行、`1`=加湿手动运行、`2`=加湿停止、`3`=加湿关闭
  ⇒ `Running=2` 是**停止**，`Running>0 ⇒ 正在喷`恰恰是**反的**。
  判据已改成复合式（§2）：`{2,3}` 直接 `clear`（不需要阈值）、`{0,1}` 是**运行态**
  但仍要看 RH 方向才能分 `standby`/`humidifying`，未定标即 `uncalibrated`。
  §0/§1/§4 已同步，并把"**先查仓库文档再谈未知**"写进 §4 的方法论。

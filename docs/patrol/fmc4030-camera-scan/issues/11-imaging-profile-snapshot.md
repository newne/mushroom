# 11 — 成像参数快照归档（快照文件 + 每站引用 id）

**依据:** step code 的成像票 `docs/patrol/imaging-quality/issues/01-camera-imaging-quality.md` 第 5 项
（"参数快照归档"）；Chief 2026-10-13 批准方案。

**Status:** ready-for-agent（我实现；`image_index` 挂钩部分归环境官/analysis，另开票）

---

## 1. 为什么

成像参数（对焦/曝光/白平衡/增益/WDR）锁上之后，若回答不了"**这张图是哪套参数拍的**"，
"跨天可比"仍然只是一个说法，没有证据链。现状只有 `Station.exposure_note: str = "manual"`
（`patrol/src/patrol/stations.py:82`）——自由文本、48 站各写一份会重复，且换相机/重装后
无从对照。跨天结论要能追溯到参数快照。

## 2. 方案（Chief 批准）

1. **独立快照文件**（建议 `configs/imaging-profile.yaml`），一行一套参数：

   ```yaml
   profiles:
     p20261013a:
       locked_at: "2026-10-13"
       camera_ip: 192.168.1.238
       resolution: 2880x1616          # 以现场实测为准，不抄文档
       focus: manual                  # 手动对焦 + 是否锁紧
       exposure: manual
       gain: off
       white_balance: n/a             # 单色/红外模式下不适用
       wdr: off
       day_night_mode: ""
       working_distance_mm: null
       by: ""
       note: ""
   ```
2. **每站引用 id**：`Station` 增字段 `imaging_profile: str = ""`（空 = 沿用旧行为，不假设）。
   站位表里写 `imaging_profile: p20261013a`。`exposure_note` **保留原样**（兼容，不改语义）。
3. **迁移与示教**：`patrol-migrate reanchor`/`z-frame` 必须**原样保留**这两个字段
   （reanchor 现在是按 `replace(s, y=…, z=…)` 重建，理论上保留——**要补测试钉住**）；
   `patrol-teach` 增可选参数写该字段。
4. **缺引用怎么办（fail-closed 口径）**：站位表引用了不存在的快照 id ⇒ 装载时
   **打警告并写明是哪个站位引用了哪个 id**，该字段按空处理、**不猜**；不许静默当成"已锁"。
   （对齐票 09 §4.4：缺档要显式表达。）
5. **不建空壳**：参数没锁之前不生成示例快照——否则文件看起来像"已经锁了"。

## 3. 与 `image_index` 的关系（**跨域，归环境官/analysis**）

要让每帧直接带 `imaging_profile` 进图像索引，需要 `analysis` 的 `images` DDL 加列
（`analysis/src/analysis/db.py:43-80` 一带）。**这不在我领地**，由环境官另开票决定
"加列 / 还是用轮次+站位间接关联"。

在加列之前：靠"轮次时间 × 站位"间接关联（同一时期同一相机只有一套参数），
并且图像索引里的 `angle_profile`/相机字段保持真值不变（这条是我这边的义务）。

## 4. 验收（命令 + 判据）

- [ ] `Station` 新字段 + yaml 读写往返（`patrol-teach` / `load_station_list`）
- [ ] `reanchor` / 单站 `record` / `save` 后 `imaging_profile` 与 `exposure_note` **未被改动**（新增用例）
- [ ] 引用不存在的 id：有明确警告文案 + 不静默（新增用例）
- [ ] `cd D:\code\mushroom\patrol-workspace && .\.venv\Scripts\python.exe -m pytest -q` 全绿
- [ ] 现场：等 step code 的锁参作业完成后填**一份真实快照**（不预填、不猜值）

## 5. 不做

- 不写任何"参数已锁"的默认值（锁没锁只有现场能证明，`gap-list.md:372` D2 至今未验证）；
- 不在快照里存相机口令（凭据不入仓库，见项目铁律）。

## 6. 现场判据的前置（Chief 2026-10-13 加）

- 锁参验收用的"同站位连续两轮平均亮度差 <5% / σ 差 <10%"**必须先避开加湿段**，
  否则判据本身被雾气污染（加湿期过曝、对比度塌，§12）。
- 环境官票 14 的结论是"**现有数据不足以判定加湿段**" ⇒ 短期只能靠人工 `hold` 按钮
  （已在 `deploy.patrol_trigger`）；等只读信号落地再自动化。**不另造一套湿度判定。**
- 补光灯那条仍是**未证实、当前主假设**：对照帧用
  `POST /api/cmd {"kind":"capture","args":{"no_lamp":true}}` 抓（同链路、同分辨率、只差灯），
  **不许拿实时预览当对照**（预览与抓拍成像不能逐像素互推）。
- 真机帧 S306 是**纯灰度（sat=0）+ 强晕影**，开/关灯对照**别只看整帧平均亮度**——
  最好同时看中央区域与方差。

## Comments

- 2026-10-13 巡检手：立项。方案已由 Chief 批准（独立快照文件 + 每站引用 id）。
  与 step code 票 01 第 5 项对接：**它出结论、我实现**（`stations.py` 与站位表在我领地）。
- 关联：票 10（口径数字清理）、票 09（角度/取景与口径分界）；`image_index` 加列属 analysis DDL。

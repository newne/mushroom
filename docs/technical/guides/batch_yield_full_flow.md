# Mushroom Batch Yield 全量流程图与补录结果

## 1. 场景说明

本文档覆盖两条流程：

1. **日常任务流程**：`safe_daily_batch_yield_init()` 每日初始化批次产量模板。
2. **全量补录流程**：对所有库房执行“来源批次 vs 目标表批次”差异扫描并落库。

---

## 2. 全量流程图（Mermaid）

```mermaid
flowchart TD
    A([开始]) --> B[定时/手工触发 safe_daily_batch_yield_init]
    B --> C[stat_date = today]
    C --> D[创建数据库会话 SessionLocal]

    D --> E{{try}}
    E --> F[记录开始日志]
    F --> G[调用 init_batch_yield_records]

    %% init_batch_yield_records 子流程
    G --> H[汇总来源批次 source_dates]
    H --> H1[来源1: image_text_quality.room_id + in_date]
    H --> H2[来源2: device_setpoint_changes.room_id + in_date]
    H --> H3[来源3: mushroom_env_daily_stats.room_id + batch_date]

    H1 --> I[UNION 去重得到 source_distinct]
    H2 --> I
    H3 --> I

    I --> J[按 room_id 与日期条件过滤并组装]
    J --> K{batch_ranges 是否为空}
    K -->|是| K1[返回 0]
    K -->|否| L[build_template_records: 生成模板记录\n规则: stat_date = in_date]

    L --> M{template_records 是否为空}
    M -->|是| M1[返回 0]
    M -->|否| N[插入 mushroom_batch_yield\n冲突时忽略重复]
    N --> O[返回 rowcount]

    K1 --> P[created = 返回值]
    M1 --> P
    O --> P

    P --> Q[db.commit]
    Q --> R[记录完成日志(新增条数)]
    R --> W{{finally}}

    E -->|异常| S[记录错误日志]
    S --> T[db.rollback]
    T --> W

    W --> U[db.close]
    U --> V([结束])

    %% 全量补录扩展流程
    X([全量补录触发]) --> X1[扫描所有库房缺失批次]
    X1 --> X2{是否存在缺失批次}
    X2 -->|否| X3[输出 NO_MISSING_BATCHES]
    X2 -->|是| X4[批量插入缺失批次\nstat_date 等于 in_date 且 version 为 1]
    X4 --> X5[二次核验 remaining missing]
    X5 --> X6[输出补录结果并归档文档]
```

---

## 3. 本次全量扫描与落库结果（2026-02-25）

### 3.1 扫描发现的缺失批次

- `608`：缺失 2 个批次
  - `2025-12-09`
  - `2026-01-05`
- `612`：缺失 1 个批次
  - `2025-11-21`

### 3.2 已执行落库（幂等）

插入到 `mushroom_batch_yield` 的记录：

- `id=201`, `room_id=608`, `in_date=2025-12-09`, `stat_date=2025-12-09`
- `id=202`, `room_id=608`, `in_date=2026-01-05`, `stat_date=2026-01-05`
- `id=203`, `room_id=612`, `in_date=2025-11-21`, `stat_date=2025-11-21`

### 3.3 落库后核验

- 核验结果：`NO_MISSING_BATCHES`
- 结论：当前所有库房在来源表可见的历史批次，均已在 `mushroom_batch_yield` 落库。

---

## 4. 关键约束与实现要点

1. **幂等性**：依赖唯一键 `(room_id, in_date, stat_date)` + `ON CONFLICT DO NOTHING`。
2. **批次口径统一**：模板记录保持 `stat_date = in_date`。
3. **来源兼容**：支持图文质量、设定点变更、环境日统计三路来源联合补齐。
4. **事务安全**：任务函数内统一 `try/commit/rollback/finally close`。

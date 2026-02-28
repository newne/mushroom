import uuid
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy_utils import create_database, database_exists

from global_const.global_const import pgsql_engine

# 向量维度配置，建议放到 settings 中
EMBEDDING_DIM = 512

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, Text, text

Base = declarative_base()


class MushroomImageEmbedding(Base):
    __tablename__ = "mushroom_embedding"

    # 修正点：这里必须是一个元组，字典作为最后一个元素
    __table_args__ = (
        # --- 索引定义 ---
        Index("idx_room_growth_day", "room_id", "growth_day"),  # 库房+生长天数查询
        Index("idx_collection_time", "collection_datetime"),  # 时间范围查询
        Index("idx_in_date", "in_date"),  # 时间索引：进库日期（支持范围查询）
        Index("uq_image_path", "image_path", unique=True),  # 唯一索引：图片路径（去重）
        Index("idx_collection_ip", "collection_ip"),  # 采集IP索引（支持按设备查询）
        Index(
            "idx_room_collection_ip", "room_id", "collection_ip"
        ),  # 复合索引：库房+采集IP
        # --- 表级参数字典（必须放在最后） ---
        {"comment": "蘑菇图片多模态向量存储表（含结构化控制参数）"},
    )
    # --- 字段定义 ---
    id = Column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键 (BIGINT)"
    )
    collection_datetime = Column(DateTime, nullable=False, comment="采集时间")
    image_path = Column(Text, nullable=False, unique=True, comment="图片存储路径")
    room_id = Column(String(10), nullable=False, comment="库房编号")
    in_date = Column(Date, nullable=False, comment="进库日期 (YYYY-MM-DD)")
    in_num = Column(Integer, nullable=True, comment="进库包数")
    growth_day = Column(Integer, nullable=False, comment="生长天数")

    # 图像采集IP字段 - 从image_path中自动解析
    collection_ip = Column(
        String(15), nullable=True, comment="图像采集设备IP地址，从image_path自动解析"
    )

    air_cooler_config = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="冷风机配置: {on_off, status, temp_set, temp, temp_diffset}",
    )

    # 2. 详细配置：JSONB 存储
    # 结构说明:
    # mode: 0=关闭, 1=自动, 2=手动
    # control: 0=时控, 1=CO2控制
    # status: 0=自动, 1=手动, 2=停止, 3=关闭
    # time_on / time_off: 时控模式下的开关时间
    # co2_on / co2_off: CO2模式下的启停阈值
    fresh_fan_config = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="新风机详细配置: {mode, control, status, time_on, time_off, co2_on, co2_off}",
    )
    # 1. 计数：0=关闭, 1=开启
    # 逻辑判断：模式 != 0 (非关闭) AND 状态 < 3 (非关闭状态)
    light_count = Column(
        Integer, nullable=False, default=0, comment="补光灯启用状态 (0:关闭, 1:开启)"
    )

    # 2. 详细配置：JSONB 存储
    # 结构: {"model": 1, "status": 1, "on_mset": 60, "off_mset": 180}
    # 对应 PLC: Model, Running, OnMSet, OffMSet
    light_config = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="补光灯详细配置: {model, status, on_mset, off_mset}",
    )

    humidifier_count = Column(
        Integer, nullable=False, default=0, comment="启用的加湿器台数 (running状态!=3)"
    )
    # 2. 详细配置：JSONB 存储
    # 结构对应 PLC 的 device_list + point_list
    # 示例数据: {"left": {"on": 30, "off": 50, "status": 1}, "right": {"on": 40, "off": 60, "status": 3}}
    humidifier_config = Column(
        JSON,
        nullable=False,
        default=lambda: {"left": {}, "right": {}},
        comment="加湿器详细配置: {left: {on, off, status}, right: {on, off, status}}",
    )

    # --- 环境感知数据 (新增) ---
    # 对应设备: mushroom_env_status
    # 结构: {temperature, humidity, co2}
    # 注意: 数值建议带单位或保留原始精度 (如 25.5 表示 25.5°C)
    env_sensor_status = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="环境传感器状态: {temperature, humidity, co2}",
    )

    semantic_description = Column(Text, nullable=False, comment="策略文本")

    # LLaMA生成的蘑菇生长情况描述已移至独立表

    # 假设 EMBEDDING_DIM 已定义
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False, comment="图像嵌入向量")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class ImageTextQuality(Base):
    """文本描述与图像质量评分表（推荐使用）"""

    __tablename__ = "image_text_quality"

    __table_args__ = (
        Index("idx_text_quality_room_date", "room_id", "in_date"),
        Index("idx_text_quality_score", "image_quality_score"),
        Index("idx_text_quality_image_path", "image_path"),
        Index("idx_text_quality_embedding_id", "mushroom_embedding_id"),
        {"comment": "文本描述与图像质量评分表（不保留历史时可避免重复）"},
    )

    id = Column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键 (BIGINT)"
    )
    mushroom_embedding_id = Column(
        BigInteger, nullable=True, comment="关联mushroom_embedding.id"
    )
    image_path = Column(Text, nullable=False, comment="图片存储路径")
    room_id = Column(String(10), nullable=True, comment="库房编号")
    in_date = Column(Date, nullable=True, comment="进库日期 (YYYY-MM-DD)")
    collection_datetime = Column(DateTime, nullable=True, comment="采集时间")
    llama_description = Column(
        Text, nullable=True, comment="LLaMA生成的蘑菇生长情况描述"
    )
    image_quality_score = Column(Float, nullable=True, comment="图像质量评分 (0-100)")
    human_evaluation = Column(Text, nullable=True, comment="人工评估结果或备注")
    chinese_description = Column(Text, nullable=True, comment="中文描述文本")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class MushroomBatchYield(Base):
    """每批次产量统计表"""

    __tablename__ = "mushroom_batch_yield"

    __table_args__ = (
        Index("idx_batch_yield_room_in_date", "room_id", "in_date"),
        Index("idx_batch_yield_stat_date", "stat_date"),
        Index("idx_batch_yield_harvest_time", "harvest_time"),
        Index(
            "uq_batch_yield_room_in_stat",
            "room_id",
            "in_date",
            "stat_date",
            unique=True,
        ),
        {"comment": "每批次产量统计表（鲜菇/干菇重量）"},
    )

    id = Column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键 (BIGINT)"
    )
    room_id = Column(String(10), nullable=False, comment="库房编号")
    in_date = Column(Date, nullable=False, comment="进库日期 (YYYY-MM-DD)")
    stat_date = Column(Date, nullable=False, comment="统计日期 (YYYY-MM-DD)")
    harvest_time = Column(DateTime, nullable=True, comment="采收时间")

    fresh_weight = Column(Float, nullable=True, comment="鲜菇重量 (斤)")
    dried_weight = Column(Float, nullable=True, comment="干菇重量 (斤)")

    human_evaluation = Column(Text, nullable=True, comment="人工评价/备注")
    version = Column(Integer, nullable=False, default=1, comment="乐观锁版本号")
    create_time = Column(DateTime, server_default=func.now(), comment="创建时间")
    update_time = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class MushroomBatchYieldAudit(Base):
    """批次产量变更审计日志"""

    __tablename__ = "mushroom_batch_yield_audit"

    __table_args__ = (
        Index("idx_batch_yield_audit_record", "record_id"),
        Index("idx_batch_yield_audit_room_date", "room_id", "in_date"),
        Index("idx_batch_yield_audit_time", "created_at"),
        {"comment": "批次产量变更审计日志"},
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")
    record_id = Column(BigInteger, nullable=False, comment="批次产量记录ID")
    room_id = Column(String(10), nullable=False, comment="库房编号")
    in_date = Column(Date, nullable=False, comment="进库日期 (YYYY-MM-DD)")
    stat_date = Column(Date, nullable=False, comment="统计日期 (YYYY-MM-DD)")
    before_snapshot = Column(JSON, nullable=False, comment="修改前数据快照")
    after_snapshot = Column(JSON, nullable=False, comment="修改后数据快照")
    operator = Column(String(100), nullable=True, comment="操作者")
    request_id = Column(String(100), nullable=True, comment="请求ID")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")


class MushroomEnvDailyStats(Base):
    """蘑菇房环境统计（日级）"""

    __tablename__ = "mushroom_env_daily_stats"

    __table_args__ = (
        Index("idx_env_room_date", "room_id", "stat_date"),
        Index("uq_env_room_stat_date", "room_id", "stat_date", unique=True),
        Index("idx_env_stat_date", "stat_date"),
        Index("idx_env_batch_date", "batch_date"),
        {"comment": "蘑菇房环境统计（日级）"},
    )

    id = Column(
        BigInteger, primary_key=True, autoincrement=True, comment="自增主键 (BIGINT)"
    )
    room_id = Column(String(10), nullable=False, comment="库房编号")
    stat_date = Column(Date, nullable=False, comment="统计日期 (YYYY-MM-DD)")

    in_day_num = Column(Integer, nullable=True, comment="当日典型入库天数")
    is_growth_phase = Column(Boolean, nullable=False, comment="是否为生长阶段")

    # 环境统计字段（保持不变）
    temp_median = Column(Float, nullable=True)
    temp_min = Column(Float, nullable=True)
    temp_max = Column(Float, nullable=True)
    # 上下分位数（25% / 75%）
    temp_q25 = Column(Float, nullable=True, comment="温度25分位")
    temp_q75 = Column(Float, nullable=True, comment="温度75分位")
    temp_count = Column(Integer, nullable=False, default=0)

    humidity_median = Column(Float, nullable=True)
    humidity_min = Column(Float, nullable=True)
    humidity_max = Column(Float, nullable=True)
    humidity_q25 = Column(Float, nullable=True, comment="湿度25分位")
    humidity_q75 = Column(Float, nullable=True, comment="湿度75分位")
    humidity_count = Column(Integer, nullable=False, default=0)

    co2_median = Column(Float, nullable=True)
    co2_min = Column(Float, nullable=True)
    co2_max = Column(Float, nullable=True)
    co2_q25 = Column(Float, nullable=True, comment="CO2 25分位")
    co2_q75 = Column(Float, nullable=True, comment="CO2 75分位")
    co2_count = Column(Integer, nullable=False, default=0)

    # light_hours_est = Column(Float, nullable=False, default=0.0)
    batch_date = Column(Date, nullable=True, comment="关联批次日期")
    remark = Column(Text, nullable=True, comment="数据备注")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class DeviceSetpointChange(Base):
    """设备设定点变更记录表"""

    __tablename__ = "device_setpoint_changes"

    __table_args__ = (
        Index("idx_room_change_time", "room_id", "change_time"),
        Index(
            "uq_setpoint_change_natural_key",
            "room_id",
            "device_name",
            "point_name",
            "change_time",
            unique=True,
        ),
        Index("idx_room_in_date", "room_id", "in_date"),
        Index("idx_room_growth_day", "room_id", "growth_day"),
        Index("idx_device_point", "device_name", "point_name"),
        Index("idx_change_time", "change_time"),
        Index("idx_device_type", "device_type"),
        {"comment": "设备设定点变更记录表"},
    )

    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)",
    )

    room_id = Column(String(10), nullable=False, comment="库房编号")
    device_type = Column(String(50), nullable=False, comment="设备类型")
    device_name = Column(String(100), nullable=False, comment="设备名称")
    point_name = Column(String(100), nullable=False, comment="测点名称")
    point_description = Column(String(200), nullable=True, comment="测点描述")

    change_time = Column(DateTime, nullable=False, comment="变更发生时间")
    previous_value = Column(Float, nullable=False, comment="变更前值")
    current_value = Column(Float, nullable=False, comment="变更后值")

    in_date = Column(Date, nullable=True, comment="进库日期 (YYYY-MM-DD)")
    growth_day = Column(Integer, nullable=True, comment="生长天数")
    in_num = Column(Integer, nullable=True, comment="进库包数")
    batch_id = Column(String(50), nullable=True, comment="批次ID (room_id+in_date)")

    change_type = Column(String(50), nullable=False, comment="变更类型")

    detection_time = Column(DateTime, nullable=False, comment="检测时间")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")


class ControlStrategyKnowledgeBaseRun(Base):
    """控制策略知识库构建批次元信息表"""

    __tablename__ = "control_strategy_kb_runs"

    __table_args__ = (
        Index("idx_control_kb_run_type", "kb_type"),
        Index("idx_control_kb_run_generated_at", "generated_at"),
        Index("idx_control_kb_run_is_active", "is_active"),
        {"comment": "控制策略知识库构建批次元信息（stage/model/cluster）"},
    )

    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)",
    )
    kb_type = Column(
        String(20),
        nullable=False,
        comment="知识库类型：stage/model/cluster",
    )
    source = Column(String(100), nullable=True, comment="来源标识")
    description = Column(Text, nullable=True, comment="知识库描述")
    generated_at = Column(DateTime, nullable=False, comment="知识库生成时间")
    cluster_method = Column(String(50), nullable=True, comment="聚类方法（cluster型）")
    stage_definition = Column(JSON, nullable=True, comment="阶段定义映射")
    pipeline = Column(JSON, nullable=True, comment="构建流水线参数")
    scope = Column(JSON, nullable=True, comment="数据范围统计")
    rooms = Column(JSON, nullable=True, comment="覆盖库房列表")
    payload = Column(JSON, nullable=False, comment="原始知识库JSON快照")
    source_file = Column(Text, nullable=True, comment="来源文件路径")
    is_active = Column(Boolean, nullable=False, default=True, comment="是否启用")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class ControlStrategyKnowledgeBaseStageRule(Base):
    """控制策略知识库阶段/日粒度规则明细表"""

    __tablename__ = "control_strategy_kb_stage_rules"

    __table_args__ = (
        Index("idx_control_kb_stage_run", "run_id"),
        Index(
            "idx_control_kb_stage_room_device_point",
            "room_id",
            "device_type",
            "point_key",
        ),
        Index("idx_control_kb_stage_stage", "stage"),
        Index("idx_control_kb_stage_growth_day", "growth_day_num"),
        Index(
            "uq_control_kb_stage_rule",
            "run_id",
            "room_id",
            "device_type",
            "point_key",
            "profile_type",
            "stage",
            "growth_day_num",
            unique=True,
        ),
        {
            "comment": "控制策略知识库规则明细（stage_profile/daily_profile/stage_rules）"
        },
    )

    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="自增主键 (BIGINT)",
    )
    run_id = Column(PgUUID(as_uuid=True), nullable=False, comment="关联批次ID")
    profile_type = Column(
        String(20),
        nullable=False,
        comment="规则类型：stage_profile/daily_profile/stage_rules",
    )

    room_id = Column(String(10), nullable=True, comment="库房编号（model全局规则可空）")
    device_type = Column(String(50), nullable=False, comment="设备类型")
    point_key = Column(
        String(150), nullable=False, comment="点位键（point_group/设备.点位）"
    )
    point_display = Column(String(200), nullable=True, comment="点位显示名")

    stage = Column(String(20), nullable=True, comment="生长阶段（D1-D7等）")
    growth_day_num = Column(Integer, nullable=True, comment="生长天数（日粒度规则）")

    changes = Column(Integer, nullable=True, comment="变更次数")
    active_batches = Column(Integer, nullable=True, comment="活跃批次数")
    active_days = Column(Integer, nullable=True, comment="活跃天数")

    value_median = Column(Float, nullable=True, comment="设定值中位数")
    value_p25 = Column(Float, nullable=True, comment="设定值25分位")
    value_p75 = Column(Float, nullable=True, comment="设定值75分位")
    value_min = Column(Float, nullable=True, comment="设定值最小值")
    value_max = Column(Float, nullable=True, comment="设定值最大值")
    delta_median = Column(Float, nullable=True, comment="变化量中位数")

    preferred_setpoint = Column(String(100), nullable=True, comment="推荐设定点")
    major_change_type = Column(String(50), nullable=True, comment="主要变更类型")
    preferred_hour = Column(Integer, nullable=True, comment="推荐调整小时")

    raw_rule = Column(JSON, nullable=False, comment="原始规则JSON快照")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class ControlStrategyKnowledgeBaseClusterMeta(Base):
    """控制策略聚类知识库点位元信息表"""

    __tablename__ = "control_strategy_kb_cluster_meta"

    __table_args__ = (
        Index("idx_control_kb_cluster_meta_run", "run_id"),
        Index(
            "uq_control_kb_cluster_meta",
            "run_id",
            "device_type",
            "point_key",
            unique=True,
        ),
        {"comment": "控制策略聚类知识库点位元信息"},
    )

    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="自增主键 (BIGINT)",
    )
    run_id = Column(PgUUID(as_uuid=True), nullable=False, comment="关联批次ID")
    device_type = Column(String(50), nullable=False, comment="设备类型")
    point_key = Column(String(150), nullable=False, comment="点位键")
    point_display = Column(String(200), nullable=True, comment="点位显示名")

    cluster_count = Column(Integer, nullable=True, comment="聚类簇数量")
    sample_count = Column(Integer, nullable=True, comment="样本数量")
    silhouette_score = Column(Float, nullable=True, comment="轮廓系数")
    cluster_method = Column(String(50), nullable=True, comment="聚类方法")
    feature_columns = Column(JSON, nullable=True, comment="聚类特征列")

    raw_meta = Column(JSON, nullable=False, comment="原始聚类元信息JSON")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class ControlStrategyKnowledgeBaseClusterRule(Base):
    """控制策略聚类知识库时间簇规则明细表"""

    __tablename__ = "control_strategy_kb_cluster_rules"

    __table_args__ = (
        Index("idx_control_kb_cluster_rule_run", "run_id"),
        Index(
            "idx_control_kb_cluster_rule_device_point",
            "device_type",
            "point_key",
        ),
        Index("idx_control_kb_cluster_rule_growth", "growth_day_min", "growth_day_max"),
        Index(
            "uq_control_kb_cluster_rule",
            "run_id",
            "device_type",
            "point_key",
            "cluster_id",
            "point_display",
            unique=True,
        ),
        {"comment": "控制策略聚类知识库时间簇规则明细"},
    )

    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="自增主键 (BIGINT)",
    )
    run_id = Column(PgUUID(as_uuid=True), nullable=False, comment="关联批次ID")

    device_type = Column(String(50), nullable=False, comment="设备类型")
    point_key = Column(String(150), nullable=False, comment="点位键")
    point_display = Column(String(200), nullable=True, comment="点位显示名")

    cluster_id = Column(Integer, nullable=False, comment="聚类ID")
    cluster_name = Column(String(50), nullable=True, comment="聚类名称")

    sample_days = Column(Integer, nullable=True, comment="样本天数")
    active_rooms = Column(Integer, nullable=True, comment="活跃库房数")
    active_batches = Column(Integer, nullable=True, comment="活跃批次数")

    growth_day_min = Column(Integer, nullable=True, comment="生长天数最小值")
    growth_day_max = Column(Integer, nullable=True, comment="生长天数最大值")
    growth_day_median = Column(Float, nullable=True, comment="生长天数中位数")
    growth_window = Column(String(50), nullable=True, comment="生长窗口")

    daily_changes_median = Column(Float, nullable=True, comment="日变更中位数")
    value_median = Column(Float, nullable=True, comment="设定值中位数")
    value_p25 = Column(Float, nullable=True, comment="设定值25分位")
    value_p75 = Column(Float, nullable=True, comment="设定值75分位")
    value_min = Column(Float, nullable=True, comment="设定值最小值")
    value_max = Column(Float, nullable=True, comment="设定值最大值")
    day_delta_median = Column(Float, nullable=True, comment="日变化中位数")

    preferred_change_type = Column(String(50), nullable=True, comment="偏好变更类型")
    preferred_hour = Column(Float, nullable=True, comment="偏好调整小时")
    value_trend = Column(String(30), nullable=True, comment="值变化趋势")

    raw_rule = Column(JSON, nullable=False, comment="原始时间簇规则JSON")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class DecisionAnalysisStaticConfig(Base):
    """决策分析静态配置表 - 存储库房-设备-点位的静态元数据"""

    __tablename__ = "decision_analysis_static_config"

    __table_args__ = (
        # 唯一约束：确保点位唯一性
        Index(
            "uq_decision_room_device_point",
            "room_id",
            "device_alias",
            "point_alias",
            unique=True,
        ),
        # 备选唯一约束
        Index(
            "uq_decision_room_device_point_name",
            "room_id",
            "device_name",
            "point_name",
            unique=True,
        ),
        # 查询索引
        Index("idx_decision_room_device_type", "room_id", "device_type"),
        Index("idx_decision_static_device_alias", "device_alias"),
        Index("idx_decision_static_point_alias", "point_alias"),
        Index("idx_decision_is_active", "is_active"),
        Index("idx_decision_config_version", "config_version"),
        Index("idx_decision_effective_time", "effective_time"),
        {"comment": "决策分析静态配置表：存储库房-设备-点位的静态元数据，变更频率低"},
    )

    # 主键
    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)",
    )

    # ✅ 必选字段（全部来自现有 key，严格保持不变）
    room_id = Column(String(10), nullable=False, comment="库房编号")
    device_type = Column(
        String(50),
        nullable=False,
        comment="设备类型：来自devices的一级key（如air_cooler/fresh_air_fan/humidifier/grow_light）",
    )
    device_name = Column(
        String(100), nullable=False, comment="设备名称：如TD1_Q611MDCH01"
    )
    device_alias = Column(
        String(100), nullable=False, comment="设备别名：如air_cooler_611"
    )
    point_alias = Column(String(100), nullable=False, comment="点位别名：如on_off")
    point_name = Column(String(100), nullable=False, comment="点位名称：如OnOff")
    remark = Column(String(200), nullable=True, comment="点位备注：如冷风机开关")
    change_type = Column(
        String(50),
        nullable=False,
        comment="变更类型：digital_on_off/analog_value/enum_state",
    )
    threshold = Column(Float, nullable=True, comment="阈值：模拟量变更阈值，可空")
    enum_mapping = Column(
        JSON, nullable=True, comment="枚举映射：数字状态到文本的映射，JSON格式存储"
    )

    # ➕ 推荐新增字段（不影响前端现有 key）
    config_version = Column(
        Integer, nullable=False, default=1, comment="配置版本号：递增整数"
    )
    is_active = Column(
        Boolean, nullable=False, default=True, comment="是否启用：true/false"
    )
    effective_time = Column(
        DateTime, nullable=False, server_default=func.now(), comment="配置生效时间"
    )
    source = Column(
        String(50),
        nullable=False,
        default="manual",
        comment="配置来源：manual/import/platform等",
    )
    operator = Column(String(100), nullable=True, comment="配置维护人/系统")
    comment = Column(Text, nullable=True, comment="配置备注")

    # 时间戳
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class DecisionAnalysisDynamicResult(Base):
    """决策分析动态结果表 - 存储模型每次输出的调节结果"""

    __tablename__ = "decision_analysis_dynamic_result"

    __table_args__ = (
        # 查询索引
        Index("idx_decision_room_batch_time", "room_id", "batch_id", "time"),
        Index("idx_decision_batch_id", "batch_id"),
        Index("idx_decision_time", "time"),
        Index("idx_decision_dynamic_device_point", "device_alias", "point_alias"),
        Index("idx_decision_room_time", "room_id", "time"),
        Index("idx_decision_dynamic_device_type", "device_type"),
        Index("idx_decision_dynamic_apply_time", "apply_time"),
        {
            "comment": "决策分析动态结果表：存储模型每次输出的调节结果，高频写入用于回溯对比"
        },
    )

    # 主键
    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)",
    )

    # ✅ 必选字段（保持现有 key 不变）
    room_id = Column(String(10), nullable=False, comment="库房编号")
    device_type = Column(String(50), nullable=False, comment="设备类型")
    device_alias = Column(String(100), nullable=False, comment="设备别名")
    point_alias = Column(String(100), nullable=False, comment="点位别名")
    change = Column(Boolean, nullable=False, comment="是否变更：true/false")
    old = Column(
        String(100), nullable=True, comment="变更前值：兼容数字/枚举/开关量，string存储"
    )
    new = Column(
        String(100), nullable=True, comment="变更后值：兼容数字/枚举/开关量，string存储"
    )
    level = Column(String(20), nullable=False, comment="变更级别：high/medium/low")

    # ➕ 强烈建议新增字段（用于一致性、追溯、闭环）
    # (1) 事件定位 / 去重
    batch_id = Column(
        String(100),
        nullable=False,
        comment="一次模型推理批次号：把同一批输出聚合在一起",
    )
    time = Column(DateTime, nullable=False, comment="推理产生时间：毫秒级时间戳")
    trace_id = Column(String(100), nullable=True, comment="链路追踪ID")

    # (2) 模型可追溯
    model_name = Column(String(100), nullable=True, comment="模型名称")
    model_version = Column(String(50), nullable=True, comment="模型版本")
    strategy_version = Column(String(50), nullable=True, comment="策略版本/规则版本")
    confidence = Column(Float, nullable=True, comment="置信度")

    # (3) 可解释性
    reason = Column(Text, nullable=True, comment="变更原因/解释")
    features = Column(JSON, nullable=True, comment="关键特征快照")
    rule_hit = Column(JSON, nullable=True, comment="命中规则列表")

    # (4) 控制闭环
    apply_time = Column(DateTime, nullable=True, comment="实际下发时间")
    apply_result = Column(Text, nullable=True, comment="下发结果：成功/失败原因")
    operator = Column(String(100), nullable=True, comment="操作者：人/系统")
    rollback = Column(Boolean, nullable=False, default=False, comment="是否回滚")

    # (5) 便于排查的冗余字段
    device_name = Column(
        String(100), nullable=True, comment="设备名称：来自现有key，冗余存储减少join"
    )
    point_name = Column(
        String(100), nullable=True, comment="点位名称：来自现有key，冗余存储"
    )
    remark = Column(
        String(200), nullable=True, comment="点位备注：来自现有key，可选冗余"
    )

    # 时间戳
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class DecisionAnalysisBatchStatus(Base):
    """决策分析批次人工响应状态表"""

    __tablename__ = "decision_analysis_batch_status"

    __table_args__ = (
        Index("uq_decision_batch_status_batch", "batch_id", unique=True),
        Index("idx_decision_batch_status_room", "room_id"),
        Index("idx_decision_batch_status_status", "status"),
        {"comment": "决策分析批次状态表：记录每个batch_id的人工响应状态"},
    )

    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="自增主键 (BIGINT)",
    )
    batch_id = Column(String(100), nullable=False, comment="批次ID")
    room_id = Column(String(10), nullable=False, comment="库房编号")
    status = Column(
        Integer,
        nullable=False,
        default=0,
        comment="状态：0=pending, 1=采纳建议, 2=手动调整, 3=忽略建议",
    )
    operator = Column(String(100), nullable=True, comment="操作者：人/系统")
    comment = Column(Text, nullable=True, comment="备注说明")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


class DecisionAnalysisSkillAudit(Base):
    """决策分析 Skill 执行审计表"""

    __tablename__ = "decision_analysis_skill_audit"

    __table_args__ = (
        Index("idx_skill_audit_batch", "batch_id"),
        Index("idx_skill_audit_room_time", "room_id", "analysis_time"),
        Index("idx_skill_audit_skill_enabled", "skill_enabled"),
        Index("idx_skill_audit_kb_prior_enabled", "kb_prior_enabled"),
        {"comment": "Skill执行审计：记录命中、修正、KB先验引用等证据"},
    )

    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)",
    )
    batch_id = Column(String(100), nullable=False, comment="批次ID")
    room_id = Column(String(10), nullable=False, comment="库房编号")
    analysis_time = Column(DateTime, nullable=False, comment="分析时间")

    skill_enabled = Column(Boolean, nullable=False, default=True, comment="Skill是否启用")
    kb_prior_enabled = Column(
        Boolean, nullable=False, default=False, comment="Skill KB先验是否启用"
    )
    kb_prior_points = Column(Integer, nullable=False, default=0, comment="可用KB先验点位数")
    kb_prior_used = Column(Integer, nullable=False, default=0, comment="KB先验引用次数")

    matched_count = Column(Integer, nullable=False, default=0, comment="命中Skill数量")
    constraint_corrections = Column(Integer, nullable=False, default=0, comment="约束修正次数")
    matched_skill_ids = Column(JSON, nullable=True, comment="命中Skill ID列表")

    trigger_context = Column(JSON, nullable=True, comment="触发上下文")
    correction_details = Column(JSON, nullable=True, comment="修正明细")
    raw_feedback = Column(JSON, nullable=True, comment="原始skill_feedback")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间"
    )


def migrate_dynamic_status_to_batch_status():
    """
    将 decision_analysis_dynamic_result 中的批次信息迁移到批次状态表。

    迁移策略：
    - 若 dynamic_result 存在 status 字段：按 batch_id + room_id 聚合，status 取最大值
    - 若仅有 batch_id：status 统一填充为 0（pending）
    """
    try:
        with pgsql_engine.connect() as conn:
            status_column = conn.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'decision_analysis_dynamic_result'
                      AND column_name = 'status'
                    """
                )
            ).fetchone()

            if status_column:
                conn.execute(
                    text(
                        """
                        INSERT INTO decision_analysis_batch_status (batch_id, room_id, status, operator, comment)
                        SELECT
                            batch_id,
                            room_id,
                            MAX(status) AS status,
                            MAX(operator) AS operator,
                            'migrated from decision_analysis_dynamic_result' AS comment
                        FROM decision_analysis_dynamic_result
                        WHERE batch_id IS NOT NULL
                        GROUP BY batch_id, room_id
                        ON CONFLICT (batch_id) DO NOTHING
                        """
                    )
                )
                conn.commit()
                logger.info("[Migration] Migrated dynamic status to batch status table")
                return

            conn.execute(
                text(
                    """
                    INSERT INTO decision_analysis_batch_status (batch_id, room_id, status, operator, comment)
                    SELECT DISTINCT
                        batch_id,
                        room_id,
                        0 AS status,
                        'system' AS operator,
                        'migrated from decision_analysis_dynamic_result (status defaulted)' AS comment
                    FROM decision_analysis_dynamic_result
                    WHERE batch_id IS NOT NULL
                    ON CONFLICT (batch_id) DO NOTHING
                    """
                )
            )
            conn.commit()
            logger.info(
                "[Migration] Migrated dynamic batch_ids to batch status table with status=0"
            )
    except Exception as e:
        logger.error(f"[Migration] Failed to migrate dynamic status: {e}")
        raise


def init_database_extensions():
    """
    初始化数据库扩展 (pgcrypto, pgvector)
    """
    # 使用原生 SQL 语句创建扩展，确保不会因已存在而报错
    try:
        with pgsql_engine.connect() as conn:
            # 1. 创建 pgcrypto 扩展 (用于生成 UUID 等)
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

            # 2. 创建 pgvector 扩展 (用于向量存储)
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

            conn.commit()
            logger.info("[0.0.0] Database extensions (pgcrypto, vector) ensured.")
    except Exception as e:
        logger.error(f"[0.0.1] Failed to create extensions: {str(e)}")


def add_collection_ip_field():
    """
    添加collection_ip字段到现有的mushroom_embedding表

    这个函数会：
    1. 检查字段是否已存在
    2. 如果不存在，添加字段
    3. 创建相关索引
    4. 更新现有记录的collection_ip字段
    """
    try:
        with pgsql_engine.connect() as conn:
            # 检查字段是否已存在
            check_column_sql = text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'mushroom_embedding' 
                AND column_name = 'collection_ip'
            """)

            result = conn.execute(check_column_sql).fetchone()

            if result is None:
                logger.info(
                    "[Migration] Adding collection_ip field to mushroom_embedding table..."
                )

                # 添加collection_ip字段（PostgreSQL不支持在ALTER TABLE ADD COLUMN中使用COMMENT）
                add_column_sql = text("""
                    ALTER TABLE mushroom_embedding 
                    ADD COLUMN collection_ip VARCHAR(15) NULL
                """)
                conn.execute(add_column_sql)

                # 单独添加字段注释
                add_comment_sql = text("""
                    COMMENT ON COLUMN mushroom_embedding.collection_ip 
                    IS '图像采集设备IP地址，从image_path自动解析'
                """)
                conn.execute(add_comment_sql)

                # 创建索引
                create_ip_index_sql = text("""
                    CREATE INDEX IF NOT EXISTS idx_collection_ip 
                    ON mushroom_embedding (collection_ip)
                """)
                conn.execute(create_ip_index_sql)

                # 创建复合索引
                create_room_ip_index_sql = text("""
                    CREATE INDEX IF NOT EXISTS idx_room_collection_ip 
                    ON mushroom_embedding (room_id, collection_ip)
                """)
                conn.execute(create_room_ip_index_sql)

                conn.commit()
                logger.info(
                    "[Migration] Successfully added collection_ip field and indexes"
                )

                # 更新现有记录
                update_existing_collection_ip_data()

            else:
                logger.info(
                    "[Migration] collection_ip field already exists, skipping migration"
                )

    except Exception as e:
        logger.error(f"[Migration] Failed to add collection_ip field: {e}")
        raise


def add_image_text_quality_fields():
    """
    添加 human_evaluation 和 chinese_description 字段到现有的 image_text_quality 表

    这个函数会：
    1. 检查字段是否已存在
    2. 如果不存在，添加字段
    3. 添加字段注释
    """
    try:
        with pgsql_engine.connect() as conn:
            check_columns_sql = text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'image_text_quality'
                  AND column_name IN ('human_evaluation', 'chinese_description')
                """
            )

            existing_columns = {row[0] for row in conn.execute(check_columns_sql)}

            if "human_evaluation" not in existing_columns:
                logger.info(
                    "[Migration] Adding human_evaluation field to image_text_quality table..."
                )
                conn.execute(
                    text(
                        """
                        ALTER TABLE image_text_quality
                        ADD COLUMN human_evaluation TEXT NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        COMMENT ON COLUMN image_text_quality.human_evaluation
                        IS '人工评估结果或备注'
                        """
                    )
                )

            if "chinese_description" not in existing_columns:
                logger.info(
                    "[Migration] Adding chinese_description field to image_text_quality table..."
                )
                conn.execute(
                    text(
                        """
                        ALTER TABLE image_text_quality
                        ADD COLUMN chinese_description TEXT NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        COMMENT ON COLUMN image_text_quality.chinese_description
                        IS '中文描述文本'
                        """
                    )
                )

            if existing_columns and (
                "human_evaluation" in existing_columns
                and "chinese_description" in existing_columns
            ):
                logger.info(
                    "[Migration] image_text_quality fields already exist, skipping migration"
                )

            conn.commit()

    except Exception as e:
        logger.error(f"[Migration] Failed to add image_text_quality fields: {e}")
        raise


def update_device_setpoint_change_schema() -> None:
    """
    同步 device_setpoint_changes 表结构：新增批次字段并移除旧字段
    """
    try:
        with pgsql_engine.connect() as conn:
            existing_columns = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = 'device_setpoint_changes'
                        """
                    )
                )
            }

            if "in_date" not in existing_columns:
                conn.execute(
                    text(
                        """
                        ALTER TABLE device_setpoint_changes
                        ADD COLUMN in_date DATE NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        COMMENT ON COLUMN device_setpoint_changes.in_date
                        IS '进库日期 (YYYY-MM-DD)'
                        """
                    )
                )

            if "growth_day" not in existing_columns:
                conn.execute(
                    text(
                        """
                        ALTER TABLE device_setpoint_changes
                        ADD COLUMN growth_day INTEGER NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        COMMENT ON COLUMN device_setpoint_changes.growth_day
                        IS '生长天数'
                        """
                    )
                )

            if "in_num" not in existing_columns:
                conn.execute(
                    text(
                        """
                        ALTER TABLE device_setpoint_changes
                        ADD COLUMN in_num INTEGER NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        COMMENT ON COLUMN device_setpoint_changes.in_num
                        IS '进库包数'
                        """
                    )
                )

            if "batch_id" not in existing_columns:
                conn.execute(
                    text(
                        """
                        ALTER TABLE device_setpoint_changes
                        ADD COLUMN batch_id VARCHAR(50) NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        """
                        COMMENT ON COLUMN device_setpoint_changes.batch_id
                        IS '批次ID (room_id+in_date)'
                        """
                    )
                )

            if "change_detail" in existing_columns:
                conn.execute(
                    text(
                        """
                        ALTER TABLE device_setpoint_changes
                        DROP COLUMN IF EXISTS change_detail
                        """
                    )
                )

            if "change_magnitude" in existing_columns:
                conn.execute(
                    text(
                        """
                        ALTER TABLE device_setpoint_changes
                        DROP COLUMN IF EXISTS change_magnitude
                        """
                    )
                )

            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_room_in_date
                    ON device_setpoint_changes (room_id, in_date)
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_room_growth_day
                    ON device_setpoint_changes (room_id, growth_day)
                    """
                )
            )
            conn.commit()
            logger.info("[Migration] device_setpoint_changes schema updated")

    except Exception as e:
        logger.error(
            f"[Migration] Failed to update device_setpoint_changes schema: {e}"
        )
        raise


def ensure_env_and_setpoint_uniqueness() -> None:
    """为环境统计与设定点变化表执行去重并建立唯一索引。"""
    try:
        with pgsql_engine.connect() as conn:
            conn.execute(
                text(
                    """
                    WITH ranked AS (
                        SELECT
                            ctid,
                            ROW_NUMBER() OVER (
                                PARTITION BY room_id, stat_date
                                ORDER BY updated_at DESC NULLS LAST,
                                         created_at DESC NULLS LAST,
                                         id DESC
                            ) AS rn
                        FROM mushroom_env_daily_stats
                    )
                    DELETE FROM mushroom_env_daily_stats t
                    USING ranked r
                    WHERE t.ctid = r.ctid AND r.rn > 1
                    """
                )
            )

            conn.execute(
                text(
                    """
                    WITH ranked AS (
                        SELECT
                            ctid,
                            ROW_NUMBER() OVER (
                                PARTITION BY room_id, device_name, point_name, change_time
                                ORDER BY id DESC
                            ) AS rn
                        FROM device_setpoint_changes
                    )
                    DELETE FROM device_setpoint_changes t
                    USING ranked r
                    WHERE t.ctid = r.ctid AND r.rn > 1
                    """
                )
            )

            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_env_room_stat_date
                    ON mushroom_env_daily_stats (room_id, stat_date)
                    """
                )
            )

            conn.execute(
                text(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS uq_setpoint_change_natural_key
                    ON device_setpoint_changes (room_id, device_name, point_name, change_time)
                    """
                )
            )

            conn.commit()

        logger.info("[Migration] ensured env/setpoint uniqueness and deduplication")
    except Exception as e:
        logger.error(f"[Migration] Failed to ensure uniqueness constraints: {e}")
        raise


def update_existing_collection_ip_data():
    """
    更新现有记录的collection_ip字段

    从image_path中解析IP地址并更新到collection_ip字段
    """
    try:
        logger.info("[Migration] Starting to update existing collection_ip data...")

        # 创建数据库会话
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()

        try:
            # 查询所有collection_ip为空的记录
            records = (
                session.query(MushroomImageEmbedding)
                .filter(MushroomImageEmbedding.collection_ip.is_(None))
                .all()
            )

            logger.info(f"[Migration] Found {len(records)} records to update")

            updated_count = 0
            failed_count = 0

            for record in records:
                try:
                    # 从image_path中提取IP地址
                    ip_address = MushroomImageEmbedding.extract_collection_ip_from_path(
                        record.image_path
                    )

                    if ip_address:
                        record.collection_ip = ip_address
                        updated_count += 1

                        # 每100条记录提交一次
                        if updated_count % 100 == 0:
                            session.commit()
                            logger.info(
                                f"[Migration] Updated {updated_count} records so far..."
                            )
                    else:
                        failed_count += 1
                        logger.debug(
                            f"[Migration] Could not extract IP from: {record.image_path}"
                        )

                except Exception as e:
                    failed_count += 1
                    logger.warning(
                        f"[Migration] Error processing record {record.id}: {e}"
                    )

            # 最终提交
            session.commit()

            logger.info("[Migration] Collection IP update completed:")
            logger.info(f"  - Successfully updated: {updated_count} records")
            logger.info(f"  - Failed to extract IP: {failed_count} records")
            logger.info(f"  - Total processed: {len(records)} records")

        finally:
            session.close()

    except Exception as e:
        logger.error(f"[Migration] Failed to update existing collection_ip data: {e}")
        raise


def validate_collection_ip_field():
    """
    验证collection_ip字段的数据完整性

    Returns:
        dict: 验证结果统计
    """
    try:
        logger.info("[Validation] Validating collection_ip field data...")

        Session = sessionmaker(bind=pgsql_engine)
        session = Session()

        try:
            # 统计总记录数
            total_records = session.query(MushroomImageEmbedding).count()

            # 统计有IP地址的记录数
            records_with_ip = (
                session.query(MushroomImageEmbedding)
                .filter(MushroomImageEmbedding.collection_ip.isnot(None))
                .count()
            )

            # 统计无IP地址的记录数
            records_without_ip = (
                session.query(MushroomImageEmbedding)
                .filter(MushroomImageEmbedding.collection_ip.is_(None))
                .count()
            )

            # 统计不同IP地址的数量
            unique_ips = (
                session.query(MushroomImageEmbedding.collection_ip)
                .distinct()
                .filter(MushroomImageEmbedding.collection_ip.isnot(None))
                .count()
            )

            # 按IP地址分组统计
            ip_counts = (
                session.query(
                    MushroomImageEmbedding.collection_ip,
                    func.count(MushroomImageEmbedding.id).label("count"),
                )
                .filter(MushroomImageEmbedding.collection_ip.isnot(None))
                .group_by(MushroomImageEmbedding.collection_ip)
                .all()
            )

            validation_result = {
                "total_records": total_records,
                "records_with_ip": records_with_ip,
                "records_without_ip": records_without_ip,
                "unique_ips": unique_ips,
                "coverage_percentage": (records_with_ip / total_records * 100)
                if total_records > 0
                else 0,
                "ip_distribution": {ip: count for ip, count in ip_counts},
            }

            logger.info("[Validation] Collection IP field validation results:")
            logger.info(f"  - Total records: {validation_result['total_records']}")
            logger.info(f"  - Records with IP: {validation_result['records_with_ip']}")
            logger.info(
                f"  - Records without IP: {validation_result['records_without_ip']}"
            )
            logger.info(f"  - Unique IP addresses: {validation_result['unique_ips']}")
            logger.info(
                f"  - Coverage: {validation_result['coverage_percentage']:.2f}%"
            )

            if validation_result["ip_distribution"]:
                logger.info("  - IP distribution:")
                for ip, count in validation_result["ip_distribution"].items():
                    logger.info(f"    {ip}: {count} records")

            return validation_result

        finally:
            session.close()

    except Exception as e:
        logger.error(f"[Validation] Failed to validate collection_ip field: {e}")
        raise


def create_vector_index():
    """
    单独创建向量索引。
    注意：向量索引通常建议在表中有一定数据量后再创建（例如几千条），
    以便 IVFFlat 获取更准确的聚类中心。
    """
    index_name = "idx_mushroom_embedding_ivfflat"
    # 使用 ivfflat 索引，lists 参数建议为行数的平方根 (例如 100万行 -> lists = 1000)
    # 如果是小规模数据（<10万），lists 可以设小一点，如 100
    sql = f"""
    CREATE INDEX IF NOT EXISTS {index_name} 
    ON mushroom_embedding 
    USING ivfflat (embedding vector_l2_ops) 
    WITH (lists = 100);
    """
    try:
        with pgsql_engine.connect() as conn:
            # conn.execute 是 SQLAlchemy Core 的执行方式
            # 注意：某些版本 SQLAlchemy 对于 create index 语法可能需要 text() 包装
            from sqlalchemy import text

            conn.execute(text(sql))
            conn.commit()
            logger.info(f"[0.0.3] Vector index {index_name} created.")
    except Exception as e:
        logger.warning(f"[0.0.4] Vector index creation skipped or failed: {str(e)}")


def ensure_mushroom_embedding_id_autoincrement() -> None:
    """确保 mushroom_embedding.id 具备自增默认值"""
    try:
        from sqlalchemy import text

        with pgsql_engine.connect() as conn:
            result = conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'mushroom_embedding')"
                )
            )
            if not result.scalar():
                logger.warning(
                    "[0.1.4] Table mushroom_embedding not found, skip id autoincrement check"
                )
                return

            id_type_result = conn.execute(
                text(
                    """
                    SELECT data_type
                    FROM information_schema.columns
                    WHERE table_name = 'mushroom_embedding'
                      AND column_name = 'id'
                    """
                )
            ).first()
            id_type = id_type_result[0] if id_type_result else None
            if id_type and id_type.lower() not in {"bigint", "integer", "smallint"}:
                logger.warning(
                    f"[0.1.4] mushroom_embedding.id 类型为 {id_type}，跳过自增设置"
                )
                return

            conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_class WHERE relname = 'mushroom_embedding_id_seq'
                        ) THEN
                            CREATE SEQUENCE mushroom_embedding_id_seq OWNED BY mushroom_embedding.id;
                        END IF;
                    END$$;
                    """
                )
            )

            conn.execute(
                text(
                    """
                    ALTER TABLE mushroom_embedding
                    ALTER COLUMN id SET DEFAULT nextval('mushroom_embedding_id_seq');
                    """
                )
            )

            conn.execute(
                text(
                    """
                    SELECT setval(
                        'mushroom_embedding_id_seq',
                        COALESCE((SELECT MAX(id) FROM mushroom_embedding), 0) + 1,
                        false
                    );
                    """
                )
            )
            conn.commit()
            logger.info("[0.1.4] Ensured mushroom_embedding.id autoincrement default")
    except Exception as e:
        logger.error(f"[0.1.5] Failed to ensure id autoincrement: {e}")


def create_tables():
    """
    优化后的建表函数
    """
    from global_const.global_const import pgsql_engine, settings

    # 1. 检查并创建数据库
    if not database_exists(pgsql_engine.url):
        create_database(pgsql_engine.url)
        logger.info(f"[0.1.0] Database {settings.pgsql.DATABASE_NAME} created.")

    # 2. 初始化扩展 (必须在建表前)
    # init_database_extensions()

    # 3. 创建表结构 (使用 Base.metadata)
    # checkfirst=True 会自动检查表是否存在，无需手动 reflect
    try:
        Base.metadata.create_all(bind=pgsql_engine, checkfirst=True)
        logger.info("[0.1.1] Tables created/verified successfully.")
    except Exception as e:
        logger.error(f"[0.1.2] Failed to create tables: {str(e)}")
        return

    # 4. 添加collection_ip字段（如果不存在）
    try:
        add_collection_ip_field()
    except Exception as e:
        logger.error(f"[0.1.3] Failed to add collection_ip field: {str(e)}")

    # 4.1 添加 image_text_quality 新字段（如果不存在）
    try:
        add_image_text_quality_fields()
    except Exception as e:
        logger.error(f"[0.1.3] Failed to add image_text_quality fields: {str(e)}")

    # 4.2 更新 device_setpoint_changes 表结构
    try:
        update_device_setpoint_change_schema()
    except Exception as e:
        logger.error(
            f"[0.1.3] Failed to update device_setpoint_changes schema: {str(e)}"
        )

    # 4.3 迁移 dynamic status 到批次状态表（如果 status 列存在）
    try:
        migrate_dynamic_status_to_batch_status()
    except Exception as e:
        logger.error(f"[0.1.3] Failed to migrate dynamic status: {str(e)}")

    # 4.4 去重并补唯一索引（防并发重复写）
    try:
        ensure_env_and_setpoint_uniqueness()
    except Exception as e:
        logger.error(f"[0.1.3] Failed to ensure uniqueness constraints: {str(e)}")

    # 5. 创建向量索引
    # 向量索引不需要在建表时立即完成，且 DDL 语句较长，分离出来管理更清晰
    # create_vector_index()


def migrate_collection_ip_field():
    """
    独立的迁移函数，用于添加和更新collection_ip字段

    可以单独调用此函数来执行迁移，而不影响其他表创建过程
    """
    logger.info("[Migration] Starting collection_ip field migration...")

    try:
        # 添加字段和索引
        add_collection_ip_field()

        # 验证迁移结果
        validation_result = validate_collection_ip_field()

        logger.info("[Migration] Collection IP field migration completed successfully")
        return validation_result

    except Exception as e:
        logger.error(f"[Migration] Collection IP field migration failed: {e}")
        raise


def extract_static_config_from_json(json_data: dict) -> list:
    """
    从JSON数据中提取静态配置信息，扁平化为点位记录列表

    Args:
        json_data: 增强决策分析的JSON输出数据

    Returns:
        静态配置记录列表
    """
    try:
        static_configs = []

        # 处理不同的JSON格式
        if "enhanced_decision" in json_data:
            # enhanced格式
            enhanced_data = json_data["enhanced_decision"]
            room_id = enhanced_data.get("room_id")
            devices = enhanced_data.get("device_recommendations", {})
        elif "monitoring_points" in json_data:
            # both格式
            monitoring_data = json_data["monitoring_points"]
            room_id = monitoring_data.get("room_id")
            devices = monitoring_data.get("devices", {})
        elif "devices" in json_data:
            # monitoring格式
            room_id = json_data.get("room_id")
            devices = json_data.get("devices", {})
        else:
            logger.warning("[Static Config] Unknown JSON format")
            return []

        for device_type, device_list in devices.items():
            if isinstance(device_list, list):
                for device in device_list:
                    device_name = device.get("device_name")
                    device_alias = device.get("device_alias")
                    point_list = device.get("point_list", [])

                    for point in point_list:
                        config = {
                            "room_id": room_id,
                            "device_type": device_type,
                            "device_name": device_name,
                            "device_alias": device_alias,
                            "point_alias": point.get("point_alias"),
                            "point_name": point.get("point_name"),
                            "remark": point.get("remark"),
                            "change_type": point.get("change_type"),
                            "threshold": point.get("threshold"),
                            "enum_mapping": point.get("enum_mapping"),
                            "source": "enhanced_decision_analysis",
                            "operator": "system",
                        }
                        static_configs.append(config)

        return static_configs

    except Exception as e:
        logger.error(f"[Static Config] Failed to extract static config from JSON: {e}")
        return []


def extract_dynamic_results_from_json(
    json_data: dict, batch_id: str, analysis_time: datetime
) -> list:
    """
    从JSON数据中提取动态结果信息，扁平化为点位结果记录列表

    Args:
        json_data: 增强决策分析的JSON输出数据
        batch_id: 批次ID
        analysis_time: 分析时间

    Returns:
        动态结果记录列表
    """
    try:
        dynamic_results = []

        # 处理不同的JSON格式
        if "monitoring_points" in json_data:
            # both格式或monitoring格式 - 优先使用monitoring_points
            monitoring_data = json_data["monitoring_points"]
            room_id = monitoring_data.get("room_id")
            devices = monitoring_data.get("devices", {})

            logger.info(
                f"[Dynamic Results] Processing monitoring_points format for room {room_id}"
            )

        elif "enhanced_decision" in json_data:
            # enhanced格式 - 需要从device_recommendations转换
            enhanced_data = json_data["enhanced_decision"]
            room_id = enhanced_data.get("room_id")

            logger.warning(
                "[Dynamic Results] Enhanced format detected, but no monitoring_points found"
            )
            return []  # enhanced格式的device_recommendations不适合直接转换为动态结果

        elif "devices" in json_data:
            # 纯monitoring格式
            room_id = json_data.get("room_id")
            devices = json_data.get("devices", {})

            logger.info(
                f"[Dynamic Results] Processing pure monitoring format for room {room_id}"
            )

        else:
            logger.warning(
                "[Dynamic Results] Unknown JSON format, no suitable data structure found"
            )
            return []

        if not room_id:
            logger.error("[Dynamic Results] No room_id found in JSON data")
            return []

        # 处理设备和点位数据
        for device_type, device_list in devices.items():
            if not isinstance(device_list, list):
                logger.warning(
                    f"[Dynamic Results] Device list for {device_type} is not a list, skipping"
                )
                continue

            for device in device_list:
                device_name = device.get("device_name")
                device_alias = device.get("device_alias")
                point_list = device.get("point_list", [])

                logger.debug(
                    f"[Dynamic Results] Processing device {device_alias} with {len(point_list)} points"
                )

                for point in point_list:
                    # 确保必要字段存在
                    point_alias = point.get("point_alias")
                    if not point_alias:
                        logger.warning(
                            "[Dynamic Results] Point missing point_alias, skipping"
                        )
                        continue

                    if not bool(point.get("change", False)):
                        logger.debug(
                            f"[Dynamic Results] No change for {device_type}.{point_alias}, skipping"
                        )
                        continue

                    result = {
                        "room_id": room_id,
                        "device_type": device_type,
                        "device_alias": device_alias,
                        "point_alias": point_alias,
                        "change": True,
                        "old": str(point.get("old"))
                        if point.get("old") is not None
                        else "0",
                        "new": str(point.get("new"))
                        if point.get("new") is not None
                        else "0",
                        "level": point.get("level", "low"),
                        "batch_id": batch_id,
                        "time": analysis_time,
                        "device_name": device_name,
                        "point_name": point.get("point_name"),
                        "remark": point.get("remark"),
                        "reason": point.get("reason"),
                        "confidence": point.get("confidence"),
                        "model_name": "enhanced_decision_analysis",
                    }
                    dynamic_results.append(result)

                    logger.debug(
                        f"[Dynamic Results] Added result for {device_type}.{point_alias}: change={result['change']}, old={result['old']}, new={result['new']}"
                    )

        logger.info(
            f"[Dynamic Results] Extracted {len(dynamic_results)} dynamic results from JSON"
        )
        return dynamic_results

    except Exception as e:
        logger.error(
            f"[Dynamic Results] Failed to extract dynamic results from JSON: {e}"
        )
        return []


def extract_skill_feedback_from_json(json_data: dict) -> dict:
    """从决策JSON中提取 skill_feedback。"""
    try:
        if not isinstance(json_data, dict):
            return {}
        feedback = json_data.get("skill_feedback")
        if isinstance(feedback, dict):
            return feedback
        return {}
    except Exception:
        return {}


def store_decision_analysis_skill_audit(
    skill_feedback: dict,
    batch_id: str,
    room_id: str,
    analysis_time: datetime,
) -> int:
    """存储 Skill 执行审计记录。"""
    if not isinstance(skill_feedback, dict) or not skill_feedback:
        return 0

    def _insert_once() -> int:
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        try:
            row = DecisionAnalysisSkillAudit(
                batch_id=batch_id,
                room_id=room_id,
                analysis_time=analysis_time,
                skill_enabled=bool(skill_feedback.get("enabled", True)),
                kb_prior_enabled=bool(skill_feedback.get("kb_prior_enabled", False)),
                kb_prior_points=int(skill_feedback.get("kb_prior_points", 0) or 0),
                kb_prior_used=int(skill_feedback.get("kb_prior_used", 0) or 0),
                matched_count=int(skill_feedback.get("matched_count", 0) or 0),
                constraint_corrections=int(
                    skill_feedback.get("constraint_corrections", 0) or 0
                ),
                matched_skill_ids=skill_feedback.get("matched_skill_ids") or [],
                trigger_context=skill_feedback.get("trigger_context") or {},
                correction_details=skill_feedback.get("correction_details") or [],
                raw_feedback=skill_feedback,
            )
            session.add(row)
            session.commit()
            logger.info(
                f"[Skill Audit] Stored skill audit: batch={batch_id}, room={room_id}, "
                f"matched={row.matched_count}, corrections={row.constraint_corrections}, kb_used={row.kb_prior_used}"
            )
            return 1
        finally:
            session.close()

    try:
        return _insert_once()
    except Exception as e:
        error_text = str(e)
        if "decision_analysis_skill_audit" in error_text and "does not exist" in error_text:
            try:
                Base.metadata.create_all(
                    bind=pgsql_engine,
                    tables=[DecisionAnalysisSkillAudit.__table__],
                    checkfirst=True,
                )
                logger.info("[Skill Audit] 已自动创建 decision_analysis_skill_audit 表")
                return _insert_once()
            except Exception as create_error:
                logger.warning(
                    f"[Skill Audit] 自动建表后写入仍失败: {create_error}"
                )
                return 0

        logger.warning(f"[Skill Audit] Failed to store skill audit: {e}")
        return 0


def store_decision_analysis_static_configs(configs: list) -> int:
    """
    存储静态点位配置到数据库（支持批量插入和更新）

    Args:
        configs: 静态配置记录列表

    Returns:
        成功存储的记录数量
    """
    try:
        from sqlalchemy.orm import sessionmaker

        logger.info(f"[Static Config] Storing {len(configs)} static point configs...")

        Session = sessionmaker(bind=pgsql_engine)
        session = Session()

        try:
            stored_count = 0

            for config in configs:
                # 检查是否已存在相同配置
                existing = (
                    session.query(DecisionAnalysisStaticConfig)
                    .filter_by(
                        room_id=config["room_id"],
                        device_alias=config["device_alias"],
                        point_alias=config["point_alias"],
                    )
                    .first()
                )

                if existing:
                    # 更新现有配置
                    for key, value in config.items():
                        if hasattr(existing, key) and key not in ["id", "created_at"]:
                            setattr(existing, key, value)
                    existing.config_version += 1
                    existing.updated_at = func.now()
                else:
                    # 创建新配置
                    new_config = DecisionAnalysisStaticConfig(**config)
                    session.add(new_config)

                stored_count += 1

            session.commit()
            logger.info(
                f"[Static Config] Successfully stored {stored_count} static point configs"
            )

            return stored_count

        finally:
            session.close()

    except Exception as e:
        logger.error(f"[Static Config] Failed to store static point configs: {e}")
        raise


def store_decision_analysis_dynamic_results(results: list) -> int:
    """
    存储动态点位结果到数据库（支持批量插入）

    Args:
        results: 动态结果记录列表

    Returns:
        成功存储的记录数量
    """
    try:
        from sqlalchemy.orm import sessionmaker

        logger.info(
            f"[Dynamic Results] Storing {len(results)} dynamic point results..."
        )

        Session = sessionmaker(bind=pgsql_engine)
        session = Session()

        try:
            # 批量插入动态结果
            for result in results:
                new_result = DecisionAnalysisDynamicResult(**result)
                session.add(new_result)

            if results:
                batch_id = results[0].get("batch_id")
                room_id = results[0].get("room_id")
                if batch_id and room_id:
                    existing = (
                        session.query(DecisionAnalysisBatchStatus)
                        .filter(DecisionAnalysisBatchStatus.batch_id == batch_id)
                        .first()
                    )
                    if not existing:
                        session.add(
                            DecisionAnalysisBatchStatus(
                                batch_id=batch_id,
                                room_id=room_id,
                                status=0,
                                operator="system",
                                comment="auto-created on dynamic results insert",
                            )
                        )

            session.commit()
            logger.info(
                f"[Dynamic Results] Successfully stored {len(results)} dynamic point results"
            )

            return len(results)

        finally:
            session.close()

    except Exception as e:
        logger.error(f"[Dynamic Results] Failed to store dynamic point results: {e}")
        raise


def store_decision_analysis_dynamic_results_only(
    json_data: dict, room_id: str, analysis_time: datetime, batch_id: str = None
) -> dict:
    """
    仅存储决策分析动态结果到数据库（不存储静态配置）

    用于定时调度任务，因为静态配置相对固定，只需要存储每次的动态调整结果。

    Args:
        json_data: 决策分析的JSON输出数据（monitoring格式）
        room_id: 库房编号
        analysis_time: 分析时间
        batch_id: 批次ID，如果不提供则自动生成

    Returns:
        存储结果统计信息
    """
    try:
        if not batch_id:
            batch_id = f"{room_id}_{analysis_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        logger.info(
            f"[Dynamic Storage] Storing dynamic results only for room {room_id}, batch {batch_id}"
        )

        # 仅提取并存储动态结果
        dynamic_results = extract_dynamic_results_from_json(
            json_data, batch_id, analysis_time
        )
        dynamic_count = (
            store_decision_analysis_dynamic_results(dynamic_results)
            if dynamic_results
            else 0
        )

        skill_feedback = extract_skill_feedback_from_json(json_data)
        skill_audit_count = store_decision_analysis_skill_audit(
            skill_feedback=skill_feedback,
            batch_id=batch_id,
            room_id=room_id,
            analysis_time=analysis_time,
        )

        result_stats = {
            "batch_id": batch_id,
            "room_id": room_id,
            "analysis_time": analysis_time.isoformat(),
            "dynamic_results_stored": dynamic_count,
            "dynamic_results_count": dynamic_count,
            "change_count": len([r for r in dynamic_results if r.get("change", False)])
            if dynamic_results
            else 0,
            "total_points_processed": len(dynamic_results) if dynamic_results else 0,
            "processing_time": 0.0,
            "static_configs_skipped": True,  # 标记跳过了静态配置存储
            "skill_audit_count": skill_audit_count,
        }

        logger.info("[Dynamic Storage] Dynamic results stored successfully:")
        logger.info(f"  - Batch ID: {batch_id}")
        logger.info(f"  - Dynamic results: {dynamic_count}")
        logger.info(f"  - Changes: {result_stats['change_count']}")
        logger.info(f"  - Skill audit: {skill_audit_count}")
        logger.info("  - Static configs: skipped (optimization)")

        return result_stats

    except Exception as e:
        logger.error(f"[Dynamic Storage] Failed to store dynamic results: {e}")
        raise


def store_decision_analysis_results(
    json_data: dict, room_id: str, analysis_time: datetime, batch_id: str = None
) -> dict:
    """
    存储IoT分析结果到静态配置表和动态结果表

    Args:
        json_data: 增强决策分析的JSON输出数据
        room_id: 库房编号
        analysis_time: 分析时间
        batch_id: 批次ID，如果不提供则自动生成

    Returns:
        存储结果统计信息
    """
    try:
        if not batch_id:
            batch_id = f"{room_id}_{analysis_time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        logger.info(
            f"[IoT Storage] Storing IoT analysis results for room {room_id}, batch {batch_id}"
        )

        # 1. 提取并存储静态配置
        static_configs = extract_static_config_from_json(json_data)
        static_count = (
            store_decision_analysis_static_configs(static_configs)
            if static_configs
            else 0
        )

        # 2. 提取并存储动态结果
        dynamic_results = extract_dynamic_results_from_json(
            json_data, batch_id, analysis_time
        )
        dynamic_count = (
            store_decision_analysis_dynamic_results(dynamic_results)
            if dynamic_results
            else 0
        )

        # 3. 存储Skill执行审计
        skill_feedback = extract_skill_feedback_from_json(json_data)
        skill_audit_count = store_decision_analysis_skill_audit(
            skill_feedback=skill_feedback,
            batch_id=batch_id,
            room_id=room_id,
            analysis_time=analysis_time,
        )

        if batch_id:
            Session = sessionmaker(bind=pgsql_engine)
            session = Session()
            try:
                existing = (
                    session.query(DecisionAnalysisBatchStatus)
                    .filter(DecisionAnalysisBatchStatus.batch_id == batch_id)
                    .first()
                )
                if not existing:
                    session.add(
                        DecisionAnalysisBatchStatus(
                            batch_id=batch_id,
                            room_id=room_id,
                            status=0,
                            operator="system",
                            comment="auto-created on batch storage",
                        )
                    )
                    session.commit()
            finally:
                session.close()

        result_stats = {
            "batch_id": batch_id,
            "room_id": room_id,
            "analysis_time": analysis_time.isoformat(),
            "static_configs_stored": static_count,
            "dynamic_results_stored": dynamic_count,
            "dynamic_results_count": dynamic_count,  # 添加这个字段用于显示
            "change_count": len([r for r in dynamic_results if r.get("change", False)])
            if dynamic_results
            else 0,  # 计算变更数量
            "total_points_processed": len(static_configs) if static_configs else 0,
            "processing_time": 0.0,  # 这里可以添加处理时间统计
            "skill_audit_count": skill_audit_count,
        }

        logger.info("[IoT Storage] IoT analysis results stored successfully:")
        logger.info(f"  - Batch ID: {batch_id}")
        logger.info(f"  - Static configs: {static_count}")
        logger.info(f"  - Dynamic results: {dynamic_count}")
        logger.info(f"  - Skill audit: {skill_audit_count}")

        return result_stats

    except Exception as e:
        logger.error(f"[IoT Storage] Failed to store IoT analysis results: {e}")
        raise


def query_decision_analysis_static_configs(
    room_id: str = None,
    device_type: str = None,
    device_alias: str = None,
    is_active: bool = True,
    limit: int = 1000,
) -> list:
    """
    查询静态点位配置

    Args:
        room_id: 库房编号过滤
        device_type: 设备类型过滤
        device_alias: 设备别名过滤
        is_active: 是否启用过滤
        limit: 结果数量限制

    Returns:
        查询结果列表
    """
    try:
        from sqlalchemy.orm import sessionmaker

        logger.info("[Static Config Query] Querying static point configs")

        Session = sessionmaker(bind=pgsql_engine)
        session = Session()

        try:
            query = session.query(DecisionAnalysisStaticConfig)

            if room_id:
                query = query.filter(DecisionAnalysisStaticConfig.room_id == room_id)
            if device_type:
                query = query.filter(
                    DecisionAnalysisStaticConfig.device_type == device_type
                )
            if device_alias:
                query = query.filter(
                    DecisionAnalysisStaticConfig.device_alias == device_alias
                )
            if is_active is not None:
                query = query.filter(
                    DecisionAnalysisStaticConfig.is_active == is_active
                )

            query = query.order_by(
                DecisionAnalysisStaticConfig.room_id,
                DecisionAnalysisStaticConfig.device_type,
                DecisionAnalysisStaticConfig.device_alias,
                DecisionAnalysisStaticConfig.point_alias,
            )

            if limit:
                query = query.limit(limit)

            results = query.all()
            logger.info(
                f"[Static Config Query] Found {len(results)} static point configs"
            )

            return results

        finally:
            session.close()

    except Exception as e:
        logger.error(f"[Static Config Query] Failed to query static point configs: {e}")
        raise


def query_decision_analysis_dynamic_results(
    room_id: str = None,
    batch_id: str = None,
    device_alias: str = None,
    point_alias: str = None,
    change_only: bool = False,
    start_time: datetime = None,
    end_time: datetime = None,
    limit: int = 1000,
) -> list:
    """
    查询动态点位结果

    Args:
        room_id: 库房编号过滤
        batch_id: 批次ID过滤
        device_alias: 设备别名过滤
        point_alias: 点位别名过滤
        change_only: 是否只查询有变更的记录
        start_time: 开始时间过滤
        end_time: 结束时间过滤
        limit: 结果数量限制

    Returns:
        查询结果列表
    """
    try:
        from sqlalchemy.orm import sessionmaker

        logger.info("[Dynamic Results Query] Querying dynamic point results")

        Session = sessionmaker(bind=pgsql_engine)
        session = Session()

        try:
            query = session.query(DecisionAnalysisDynamicResult)

            if room_id:
                query = query.filter(DecisionAnalysisDynamicResult.room_id == room_id)
            if batch_id:
                query = query.filter(DecisionAnalysisDynamicResult.batch_id == batch_id)
            if device_alias:
                query = query.filter(
                    DecisionAnalysisDynamicResult.device_alias == device_alias
                )
            if point_alias:
                query = query.filter(
                    DecisionAnalysisDynamicResult.point_alias == point_alias
                )
            if change_only:
                query = query.filter(DecisionAnalysisDynamicResult.change == True)
            if start_time:
                query = query.filter(DecisionAnalysisDynamicResult.time >= start_time)
            if end_time:
                query = query.filter(DecisionAnalysisDynamicResult.time <= end_time)

            query = query.order_by(DecisionAnalysisDynamicResult.time.desc())

            if limit:
                query = query.limit(limit)

            results = query.all()
            logger.info(
                f"[Dynamic Results Query] Found {len(results)} dynamic point results"
            )

            return results

        finally:
            session.close()

    except Exception as e:
        logger.error(
            f"[Dynamic Results Query] Failed to query dynamic point results: {e}"
        )
        raise


def _parse_kb_generated_at(generated_at: str | None) -> datetime:
    """解析知识库 generated_at 字段。"""
    if not generated_at:
        return datetime.now()
    try:
        return datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
    except Exception:
        return datetime.now()


def _to_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except Exception:
        return None


def _to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def store_control_strategy_knowledge_base(
    kb_payload: dict,
    kb_type: str,
    source_file: str | None = None,
    mark_previous_inactive: bool = False,
) -> dict:
    """将控制策略知识库JSON持久化到数据库。

    Args:
        kb_payload: 知识库JSON对象
        kb_type: 知识库类型，支持 stage/model/cluster
        source_file: 来源文件路径
        mark_previous_inactive: 是否将同类型历史run标记为非激活

    Returns:
        持久化统计信息
    """
    kb_type = (kb_type or "").strip().lower()
    if kb_type not in {"stage", "model", "cluster"}:
        raise ValueError("kb_type must be one of: stage/model/cluster")

    Session = sessionmaker(bind=pgsql_engine)
    session = Session()

    try:
        generated_at = _parse_kb_generated_at(kb_payload.get("generated_at"))
        run = ControlStrategyKnowledgeBaseRun(
            kb_type=kb_type,
            source=kb_payload.get("source"),
            description=kb_payload.get("description"),
            generated_at=generated_at,
            cluster_method=kb_payload.get("cluster_method"),
            stage_definition=kb_payload.get("stage_definition"),
            pipeline=kb_payload.get("pipeline"),
            scope=kb_payload.get("scope"),
            rooms=(kb_payload.get("scope") or {}).get("rooms"),
            payload=kb_payload,
            source_file=source_file,
            is_active=True,
        )
        session.add(run)
        session.flush()

        if mark_previous_inactive:
            (
                session.query(ControlStrategyKnowledgeBaseRun)
                .filter(
                    ControlStrategyKnowledgeBaseRun.kb_type == kb_type,
                    ControlStrategyKnowledgeBaseRun.id != run.id,
                    ControlStrategyKnowledgeBaseRun.is_active.is_(True),
                )
                .update({ControlStrategyKnowledgeBaseRun.is_active: False})
            )

        stage_rule_count = 0
        cluster_meta_count = 0
        cluster_rule_count = 0

        devices = kb_payload.get("devices") or {}

        if kb_type == "cluster":
            for device_type, dev_data in devices.items():
                points = (dev_data or {}).get("points") or {}
                for point_key, point_data in points.items():
                    point_display = (point_data or {}).get("point_display")
                    cluster_meta = (point_data or {}).get("cluster_meta") or {}
                    meta_row = ControlStrategyKnowledgeBaseClusterMeta(
                        run_id=run.id,
                        device_type=str(device_type),
                        point_key=str(point_key),
                        point_display=point_display,
                        cluster_count=_to_int(cluster_meta.get("cluster_count")),
                        sample_count=_to_int(cluster_meta.get("sample_count")),
                        silhouette_score=_to_float(
                            cluster_meta.get("silhouette_score")
                        ),
                        cluster_method=cluster_meta.get("cluster_method"),
                        feature_columns=cluster_meta.get("feature_columns"),
                        raw_meta=cluster_meta,
                    )
                    session.add(meta_row)
                    cluster_meta_count += 1

                    for item in (point_data or {}).get("time_clusters") or []:
                        rule = ControlStrategyKnowledgeBaseClusterRule(
                            run_id=run.id,
                            device_type=str(device_type),
                            point_key=str(point_key),
                            point_display=item.get("point_display") or point_display,
                            cluster_id=_to_int(item.get("cluster_id")) or 0,
                            cluster_name=item.get("cluster_name"),
                            sample_days=_to_int(item.get("sample_days")),
                            active_rooms=_to_int(item.get("active_rooms")),
                            active_batches=_to_int(item.get("active_batches")),
                            growth_day_min=_to_int(item.get("growth_day_min")),
                            growth_day_max=_to_int(item.get("growth_day_max")),
                            growth_day_median=_to_float(item.get("growth_day_median")),
                            growth_window=item.get("growth_window"),
                            daily_changes_median=_to_float(
                                item.get("daily_changes_median")
                            ),
                            value_median=_to_float(item.get("value_median")),
                            value_p25=_to_float(item.get("value_p25")),
                            value_p75=_to_float(item.get("value_p75")),
                            value_min=_to_float(item.get("value_min")),
                            value_max=_to_float(item.get("value_max")),
                            day_delta_median=_to_float(item.get("day_delta_median")),
                            preferred_change_type=item.get("preferred_change_type"),
                            preferred_hour=_to_float(item.get("preferred_hour")),
                            value_trend=item.get("value_trend"),
                            raw_rule=item,
                        )
                        session.add(rule)
                        cluster_rule_count += 1
        elif kb_type == "stage":
            rooms_data = kb_payload.get("rooms") or {}
            for room_id, room_data in rooms_data.items():
                room_devices = (room_data or {}).get("devices") or {}
                for device_type, dev_data in room_devices.items():
                    points = (dev_data or {}).get("points") or {}
                    for point_key, point_data in points.items():
                        for item in (point_data or {}).get("stage_profile") or []:
                            rule = ControlStrategyKnowledgeBaseStageRule(
                                run_id=run.id,
                                profile_type="stage_profile",
                                room_id=str(item.get("room_id") or room_id),
                                device_type=str(item.get("device_type") or device_type),
                                point_key=str(item.get("point_group") or point_key),
                                point_display=(point_data or {}).get("point_display"),
                                stage=item.get("stage"),
                                growth_day_num=None,
                                changes=_to_int(item.get("changes")),
                                active_batches=_to_int(item.get("active_batches")),
                                active_days=_to_int(item.get("active_days")),
                                value_median=_to_float(item.get("value_median")),
                                value_p25=_to_float(item.get("value_p25")),
                                value_p75=_to_float(item.get("value_p75")),
                                value_min=_to_float(item.get("value_min")),
                                value_max=_to_float(item.get("value_max")),
                                delta_median=_to_float(item.get("delta_median")),
                                preferred_setpoint=str(item.get("preferred_setpoint")),
                                major_change_type=item.get("major_change_type"),
                                preferred_hour=_to_int(item.get("preferred_hour")),
                                raw_rule=item,
                            )
                            session.add(rule)
                            stage_rule_count += 1

                        for item in (point_data or {}).get("daily_profile") or []:
                            rule = ControlStrategyKnowledgeBaseStageRule(
                                run_id=run.id,
                                profile_type="daily_profile",
                                room_id=str(item.get("room_id") or room_id),
                                device_type=str(item.get("device_type") or device_type),
                                point_key=str(item.get("point_group") or point_key),
                                point_display=(point_data or {}).get("point_display"),
                                stage=None,
                                growth_day_num=_to_int(item.get("growth_day_num")),
                                changes=_to_int(item.get("changes")),
                                active_batches=_to_int(item.get("active_batches")),
                                active_days=_to_int(item.get("active_days")),
                                value_median=_to_float(item.get("value_median")),
                                value_p25=_to_float(item.get("value_p25")),
                                value_p75=_to_float(item.get("value_p75")),
                                value_min=_to_float(item.get("value_min")),
                                value_max=_to_float(item.get("value_max")),
                                delta_median=_to_float(item.get("delta_median")),
                                preferred_setpoint=str(item.get("preferred_setpoint")),
                                major_change_type=item.get("major_change_type"),
                                preferred_hour=_to_int(item.get("preferred_hour")),
                                raw_rule=item,
                            )
                            session.add(rule)
                            stage_rule_count += 1
        else:
            for device_type, dev_data in devices.items():
                points = (dev_data or {}).get("points") or {}
                for point_key, point_data in points.items():
                    point_display = (point_data or {}).get("point_display")
                    for item in (point_data or {}).get("stage_rules") or []:
                        rule = ControlStrategyKnowledgeBaseStageRule(
                            run_id=run.id,
                            profile_type="stage_rules",
                            room_id=None,
                            device_type=str(device_type),
                            point_key=str(point_key),
                            point_display=point_display,
                            stage=item.get("stage"),
                            growth_day_num=None,
                            changes=_to_int(item.get("changes")),
                            active_batches=_to_int(item.get("active_batches")),
                            active_days=None,
                            value_median=_to_float(item.get("value_median")),
                            value_p25=None,
                            value_p75=None,
                            value_min=_to_float((item.get("value_range") or [None])[0])
                            if isinstance(item.get("value_range"), list)
                            and len(item.get("value_range")) >= 1
                            else None,
                            value_max=_to_float(
                                (item.get("value_range") or [None, None])[1]
                            )
                            if isinstance(item.get("value_range"), list)
                            and len(item.get("value_range")) >= 2
                            else None,
                            delta_median=_to_float(item.get("delta_median")),
                            preferred_setpoint=str(item.get("preferred_setpoint")),
                            major_change_type=item.get("major_change_type"),
                            preferred_hour=_to_int(item.get("preferred_adjust_hour")),
                            raw_rule=item,
                        )
                        session.add(rule)
                        stage_rule_count += 1

        session.commit()
        logger.info(
            f"[Control KB] Stored kb_type={kb_type} run_id={run.id} "
            f"stage_rules={stage_rule_count} cluster_meta={cluster_meta_count} "
            f"cluster_rules={cluster_rule_count}"
        )
        return {
            "run_id": str(run.id),
            "kb_type": kb_type,
            "stage_rule_count": stage_rule_count,
            "cluster_meta_count": cluster_meta_count,
            "cluster_rule_count": cluster_rule_count,
        }
    except Exception as e:
        session.rollback()
        logger.error(f"[Control KB] Failed to store knowledge base: {e}")
        raise
    finally:
        session.close()


def test_decision_analysis_tables():
    """
    测试IoT静态配置表和动态结果表的功能
    """
    import json

    logger.info("[Test] Starting IoT tables test...")

    try:
        # 1. 加载示例JSON数据
        output_dir = Path(__file__).parent.parent.parent / "output"
        json_files = list(output_dir.glob("enhanced_decision_analysis_*.json"))

        if not json_files:
            logger.warning("[Test] No JSON files found for testing")
            return False

        # 选择最新的文件
        latest_file = max(json_files, key=lambda f: f.stat().st_mtime)
        logger.info(f"[Test] Using test file: {latest_file}")

        with open(latest_file, encoding="utf-8") as f:
            json_data = json.load(f)

        # 处理不同的JSON格式来获取room_id
        if "enhanced_decision" in json_data:
            room_id = json_data["enhanced_decision"].get("room_id", "611")
        elif "monitoring_points" in json_data:
            room_id = json_data["monitoring_points"].get("room_id", "611")
        else:
            room_id = json_data.get("room_id", "611")

        analysis_time = datetime.now()

        # 2. 测试存储功能
        storage_result = store_decision_analysis_results(
            json_data, room_id, analysis_time
        )
        logger.info(f"[Test] Storage result: {storage_result}")

        # 3. 测试查询功能
        static_configs = query_decision_analysis_static_configs(
            room_id=room_id, limit=10
        )
        logger.info(f"[Test] Found {len(static_configs)} static configs")

        dynamic_results = query_decision_analysis_dynamic_results(
            room_id=room_id, batch_id=storage_result["batch_id"], limit=10
        )
        logger.info(f"[Test] Found {len(dynamic_results)} dynamic results")

        # 4. 测试变更查询
        change_results = query_decision_analysis_dynamic_results(
            room_id=room_id, change_only=True, limit=10
        )
        logger.info(f"[Test] Found {len(change_results)} change results")

        logger.info("[Test] IoT tables test completed successfully")
        return True

    except Exception as e:
        logger.error(f"[Test] IoT tables test failed: {e}")
        return False


if __name__ == "__main__":
    create_tables()
    # test_enhanced_decision_analysis_table()
    # test_iot_tables()

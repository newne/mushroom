import uuid

from loguru import logger
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import declarative_base
from sqlalchemy_utils import database_exists, create_database

from global_const.global_const import pgsql_engine, settings

# 向量维度配置，建议放到 settings 中
EMBEDDING_DIM = 512

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, func, Index, text, Date, JSON
)
from pgvector.sqlalchemy import Vector

Base = declarative_base()


class MushroomImageEmbedding(Base):
    __tablename__ = "mushroom_embedding"

    # 修正点：这里必须是一个元组，字典作为最后一个元素
    __table_args__ = (
        # --- 索引定义 ---
        Index('idx_room_stage', 'room_id', 'growth_stage'),  # 库房+阶段查询
        Index('idx_collection_time', 'collection_datetime'),  # 时间范围查询
        Index('idx_in_date', 'in_date'),  # # 2. 时间索引：进库日期（支持范围查询）
        # 4. 唯一索引：图片路径（去重）
        Index('uq_image_path', 'image_path', unique=True),
        # --- 表级参数字典（必须放在最后） ---
        {"comment": "蘑菇图片多模态向量存储表（含结构化控制参数）"}
    )
    # --- 字段定义 ---
    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,  # 使用 UUID4 替代 UUID7
        comment="主键ID (UUID4)"
    )
    collection_datetime = Column(DateTime, nullable=False, comment="采集时间")
    image_path = Column(Text, nullable=False, unique=True, comment="图片存储路径")
    file_name = Column(String(255), nullable=False, comment="原始文件名")
    room_id = Column(String(10), nullable=False, comment="库房编号")
    in_date = Column(Date, nullable=False, comment="进库日期 (YYYY-MM-DD)")
    in_num = Column(Integer, nullable=True, comment="进库包数")
    growth_day = Column(Integer, nullable=False, comment="生长天数")
    growth_stage = Column(String(20), nullable=False, comment="生长阶段")

    air_cooler_config = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="冷风机配置: {on_off, status, temp_set, temp, temp_diffset}"
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
        comment="新风机详细配置: {mode, control, status, time_on, time_off, co2_on, co2_off}"
    )
    # 1. 计数：0=关闭, 1=开启
    # 逻辑判断：模式 != 0 (非关闭) AND 状态 < 3 (非关闭状态)
    light_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="补光灯启用状态 (0:关闭, 1:开启)"
    )

    # 2. 详细配置：JSONB 存储
    # 结构: {"model": 1, "status": 1, "on_mset": 60, "off_mset": 180}
    # 对应 PLC: Model, Running, OnMSet, OffMSet
    light_config = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="补光灯详细配置: {model, status, on_mset, off_mset}"
    )

    humidifier_count = Column(
        Integer,
        nullable=False,
        default=0,
        comment="启用的加湿器台数 (running状态!=3)"
    )
    # 2. 详细配置：JSONB 存储
    # 结构对应 PLC 的 device_list + point_list
    # 示例数据: {"left": {"on": 30, "off": 50, "status": 1}, "right": {"on": 40, "off": 60, "status": 3}}
    humidifier_config = Column(
        JSON,
        nullable=False,
        default=lambda: {"left": {}, "right": {}},
        comment="加湿器详细配置: {left: {on, off, status}, right: {on, off, status}}"
    )

    # --- 环境感知数据 (新增) ---
    # 对应设备: mushroom_env_status
    # 结构: {temperature, humidity, co2}
    # 注意: 数值建议带单位或保留原始精度 (如 25.5 表示 25.5°C)
    env_sensor_status = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="环境传感器状态: {temperature, humidity, co2}"
    )

    semantic_description = Column(Text, nullable=False, comment="策略文本")
    
    # LLaMA生成的蘑菇生长情况描述
    llama_description = Column(Text, nullable=True, comment="LLaMA生成的蘑菇生长情况描述")
    
    # 完整的文本描述（身份元数据 + LLaMA描述）
    full_text_description = Column(Text, nullable=True, comment="完整文本描述（身份元数据 + LLaMA描述）")

    # 假设 EMBEDDING_DIM 已定义
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False, comment="图像嵌入向量")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")


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


def create_tables():
    """
    优化后的建表函数
    """
    # 1. 检查并创建数据库
    if not database_exists(pgsql_engine.url):
        create_database(pgsql_engine.url)
        logger.info(f"[0.1.0] Database {settings.pgsql.DATABASE_NAME} created.")

    # 2. 初始化扩展 (必须在建表前)
    init_database_extensions()

    # 3. 创建表结构 (使用 Base.metadata)
    # checkfirst=True 会自动检查表是否存在，无需手动 reflect
    try:
        Base.metadata.create_all(bind=pgsql_engine, checkfirst=True)
        logger.info("[0.1.1] Tables created/verified successfully.")
    except Exception as e:
        logger.error(f"[0.1.2] Failed to create tables: {str(e)}")
        return

    # 4. 创建向量索引
    # 向量索引不需要在建表时立即完成，且 DDL 语句较长，分离出来管理更清晰
    # create_vector_index()


if __name__ == "__main__":
    create_tables()

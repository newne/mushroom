import uuid
import re
from pathlib import Path
from datetime import datetime

from loguru import logger
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy_utils import database_exists, create_database
from sqlalchemy import (
    Column, BigInteger, Integer, String, DateTime, func, Index, Date, Float, Boolean
)

from global_const.global_const import pgsql_engine
from utils.model_inference_storage import ModelInferenceResult, create_inference_tables

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
        Index('idx_room_growth_day', 'room_id', 'growth_day'),  # 库房+生长天数查询
        Index('idx_collection_time', 'collection_datetime'),  # 时间范围查询
        Index('idx_in_date', 'in_date'),  # 时间索引：进库日期（支持范围查询）
        Index('uq_image_path', 'image_path', unique=True),  # 唯一索引：图片路径（去重）
        Index('idx_image_quality', 'image_quality_score'),  # 图像质量索引（支持质量筛选）
        Index('idx_collection_ip', 'collection_ip'),  # 采集IP索引（支持按设备查询）
        Index('idx_room_collection_ip', 'room_id', 'collection_ip'),  # 复合索引：库房+采集IP
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
    room_id = Column(String(10), nullable=False, comment="库房编号")
    in_date = Column(Date, nullable=False, comment="进库日期 (YYYY-MM-DD)")
    in_num = Column(Integer, nullable=True, comment="进库包数")
    growth_day = Column(Integer, nullable=False, comment="生长天数")
    
    # 图像质量评价字段
    image_quality_score = Column(Float, nullable=True, comment="图像质量评分 (0-100)")
    
    # 图像采集IP字段 - 从image_path中自动解析
    collection_ip = Column(String(15), nullable=True, comment="图像采集设备IP地址，从image_path自动解析")

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

    # 假设 EMBEDDING_DIM 已定义
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False, comment="图像嵌入向量")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    @staticmethod
    def extract_collection_ip_from_path(image_path: str) -> str:
        """
        从图像路径中提取采集IP地址
        
        支持的路径格式:
        1. 标准格式: {蘑菇库号}/{日期}/{蘑菇库号}_{采集IP}_{采集日期}_{详细时间}.jpg
           示例: 611/20251224/611_192.168.1.100_20251224_143052.jpg
        2. 压缩IP格式: {蘑菇库号}/{日期}/{蘑菇库号}_{压缩IP}_{采集日期}_{详细时间}.jpg
           示例: 611/20260115/611_1921681237_20251224_20260115162230.jpg (1921681237 -> 192.168.1.237)
        3. 12位压缩格式: 7/20260115/7_1921681233_20251231_20260115080130.jpg (1921681233 -> 192.168.1.233)
        4. 其他压缩格式: 611/20251219/611_192168001237_20251127_20251219180000.jpg (192168001237 -> 192.168.1.237)
        
        Args:
            image_path: 图像文件路径
            
        Returns:
            采集IP地址，如果解析失败返回None
        """
        try:
            if not image_path:
                return None
                
            # 获取文件名（不含扩展名）
            path_obj = Path(image_path)
            filename = path_obj.stem
            
            # 方法1: 标准IP格式匹配 (xxx.xxx.xxx.xxx)
            # 格式: {库号}_{IP}_{日期}_{时间}
            standard_pattern = r'^\d+_(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})_\d{8}_\d{6,}$'
            match = re.match(standard_pattern, filename)
            
            if match:
                ip_address = match.group(1)
                # 验证IP地址的有效性
                ip_parts = ip_address.split('.')
                if len(ip_parts) == 4 and all(0 <= int(part) <= 255 for part in ip_parts):
                    return ip_address
            
            # 方法2: 宽松匹配 - 查找文件名中的标准IP地址模式
            ip_pattern = r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b'
            ip_matches = re.findall(ip_pattern, filename)
            
            for ip in ip_matches:
                ip_parts = ip.split('.')
                if len(ip_parts) == 4 and all(0 <= int(part) <= 255 for part in ip_parts):
                    return ip
            
            # 方法3: 压缩IP格式匹配 - 支持多种长度的数字序列
            # 查找可能的IP数字序列 (8-12位数字)
            number_pattern = r'_(\d{8,12})_'
            number_matches = re.findall(number_pattern, filename)
            
            for num_str in number_matches:
                ip_candidates = []
                
                # 处理10位压缩格式: 1921681237 -> 192.168.1.237
                if len(num_str) == 10:
                    # 方式1: 192168xxxx -> 192.168.x.xxx
                    if num_str.startswith('192168'):
                        remaining = num_str[6:]  # xxxx
                        if len(remaining) == 4:
                            try:
                                part3 = int(remaining[:1])
                                part4 = int(remaining[1:])
                                if part3 <= 255 and part4 <= 255:
                                    ip_candidates.append(f"192.168.{part3}.{part4}")
                            except ValueError:
                                pass
                    
                    # 方式2: 其他常见网段 (10.x.x.xxx)
                    elif num_str.startswith('10'):
                        try:
                            part2 = int(num_str[2:4])
                            part3 = int(num_str[4:6])
                            part4 = int(num_str[6:])
                            if all(p <= 255 for p in [part2, part3, part4]):
                                ip_candidates.append(f"10.{part2}.{part3}.{part4}")
                        except ValueError:
                            pass
                
                # 处理12位压缩格式: 192168001237 -> 192.168.1.237
                elif len(num_str) == 12:
                    # 方式1: 192168001xxx -> 192.168.1.xxx
                    if num_str.startswith('192168'):
                        try:
                            # 提取后6位: 001237
                            remaining = num_str[6:]  # 001237
                            # 分割为两部分: 001 和 237
                            part3 = int(remaining[:3])  # 001 -> 1
                            part4 = int(remaining[3:])  # 237 -> 237
                            if part3 <= 255 and part4 <= 255:
                                ip_candidates.append(f"192.168.{part3}.{part4}")
                        except ValueError:
                            pass
                    
                    # 方式2: 其他12位格式尝试
                    # 尝试按 xxx.xxx.xxx.xxx 的方式分割
                    try:
                        part1 = int(num_str[:3])    # 前3位
                        part2 = int(num_str[3:6])   # 中3位
                        part3 = int(num_str[6:9])   # 第7-9位
                        part4 = int(num_str[9:])    # 最后3位
                        if all(0 <= p <= 255 for p in [part1, part2, part3, part4]):
                            ip_candidates.append(f"{part1}.{part2}.{part3}.{part4}")
                    except ValueError:
                        pass
                
                # 处理其他长度的压缩格式
                elif len(num_str) in [8, 9, 11]:
                    # 尝试常见的192.168网段压缩
                    if num_str.startswith('192168'):
                        try:
                            remaining = num_str[6:]
                            if len(remaining) >= 2:
                                # 对于11位数字 (19216800100): 尝试 192.168.0.100 而不是 192.168.00.100
                                if len(num_str) == 11:
                                    # 特殊处理11位: 19216800100 -> 192.168.0.100
                                    part3 = int(remaining[:2])  # 00 -> 0
                                    part4 = int(remaining[2:])  # 100 -> 100
                                    if part3 <= 255 and part4 <= 255:
                                        ip_candidates.append(f"192.168.{part3}.{part4}")
                                else:
                                    # 尝试不同的分割方式
                                    for split_pos in range(1, len(remaining)):
                                        part3 = int(remaining[:split_pos])
                                        part4 = int(remaining[split_pos:])
                                        if part3 <= 255 and part4 <= 255:
                                            ip_candidates.append(f"192.168.{part3}.{part4}")
                                            break
                        except ValueError:
                            pass
                    
                    # 尝试10.x网段 - 改进逻辑
                    elif num_str.startswith('10'):
                        try:
                            remaining = num_str[2:]
                            if len(remaining) >= 4:
                                # 对于10开头的数字，尝试更智能的分割
                                # 例如: 1001001100 -> 10.01.00.1100 (无效) 或 10.0.10.01100 (无效)
                                # 实际上应该是: 10.1.1.100
                                if len(remaining) == 8:  # 1001001100 -> 01001100
                                    # 尝试 10.1.1.100 格式
                                    if remaining.startswith('01001'):
                                        part2 = 1
                                        part3 = 1
                                        part4 = int(remaining[5:])  # 100
                                        if part4 <= 255:
                                            ip_candidates.append(f"10.{part2}.{part3}.{part4}")
                                    else:
                                        # 标准分割: 按2-2-4分割
                                        part2 = int(remaining[:2])
                                        part3 = int(remaining[2:4])
                                        part4 = int(remaining[4:])
                                        if all(0 <= p <= 255 for p in [part2, part3, part4]):
                                            ip_candidates.append(f"10.{part2}.{part3}.{part4}")
                        except ValueError:
                            pass
                
                # 返回第一个有效的IP候选
                for candidate in ip_candidates:
                    parts = candidate.split('.')
                    if len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts):
                        return candidate
            
            # 方法4: 特殊格式处理 - 处理可能的日期格式错误
            # 例如: 8_192168001231_2025129_20251219180000.jpg (日期格式错误但IP可提取)
            special_pattern = r'^\d+_(\d{8,12})_\d{6,8}_\d{6,}$'
            match = re.match(special_pattern, filename)
            
            if match:
                num_str = match.group(1)
                # 使用上面相同的逻辑处理压缩IP
                if len(num_str) == 12 and num_str.startswith('192168'):
                    try:
                        remaining = num_str[6:]  # 001231
                        part3 = int(remaining[:3])  # 001 -> 1
                        part4 = int(remaining[3:])  # 231 -> 231
                        if part3 <= 255 and part4 <= 255:
                            return f"192.168.{part3}.{part4}"
                    except ValueError:
                        pass
                        
            logger.debug(f"[MushroomImageEmbedding] Could not extract IP from path: {image_path}")
            return None
            
        except Exception as e:
            logger.warning(f"[MushroomImageEmbedding] Error extracting IP from path {image_path}: {e}")
            return None


class MushroomEnvDailyStats(Base):
    __tablename__ = "mushroom_env_daily_stats"

    __table_args__ = (
        Index('idx_room_date', 'room_id', 'stat_date'),
        Index('idx_stat_date', 'stat_date'),
        Index('idx_growth_phase', 'is_growth_phase'),
        Index('idx_in_day_num', 'in_day_num'),
        {"comment": "蘑菇库房每日环境统计宽表（用于可视化）"}
    )

    # ✅ 改为 BIGINT 自增主键
    id = Column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        comment="自增主键 (BIGINT)"
    )

    room_id = Column(String(10), nullable=False, comment="库房编号，如 '611'")
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

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(),
                        onupdate=func.now())


class DeviceSetpointChange(Base):
    """设备设定点变更记录表"""
    __tablename__ = "device_setpoint_changes"
    
    __table_args__ = (
        Index('idx_room_change_time', 'room_id', 'change_time'),
        Index('idx_device_point', 'device_name', 'point_name'),
        Index('idx_change_time', 'change_time'),
        Index('idx_device_type', 'device_type'),
        {"comment": "设备设定点变更记录表"}
    )
    
    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)"
    )
    
    room_id = Column(String(10), nullable=False, comment="库房编号")
    device_type = Column(String(50), nullable=False, comment="设备类型")
    device_name = Column(String(100), nullable=False, comment="设备名称")
    point_name = Column(String(100), nullable=False, comment="测点名称")
    point_description = Column(String(200), nullable=True, comment="测点描述")
    
    change_time = Column(DateTime, nullable=False, comment="变更发生时间")
    previous_value = Column(Float, nullable=False, comment="变更前值")
    current_value = Column(Float, nullable=False, comment="变更后值")
    
    change_type = Column(String(50), nullable=False, comment="变更类型")
    change_detail = Column(String(200), nullable=True, comment="变更详情")
    change_magnitude = Column(Float, nullable=True, comment="变更幅度")
    
    detection_time = Column(DateTime, nullable=False, comment="检测时间")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")


class DecisionAnalysisStaticConfig(Base):
    """决策分析静态配置表 - 存储库房-设备-点位的静态元数据"""
    __tablename__ = "decision_analysis_static_config"
    
    __table_args__ = (
        # 唯一约束：确保点位唯一性
        Index('uq_decision_room_device_point', 'room_id', 'device_alias', 'point_alias', unique=True),
        # 备选唯一约束
        Index('uq_decision_room_device_point_name', 'room_id', 'device_name', 'point_name', unique=True),
        # 查询索引
        Index('idx_decision_room_device_type', 'room_id', 'device_type'),
        Index('idx_decision_static_device_alias', 'device_alias'),
        Index('idx_decision_static_point_alias', 'point_alias'),
        Index('idx_decision_is_active', 'is_active'),
        Index('idx_decision_config_version', 'config_version'),
        Index('idx_decision_effective_time', 'effective_time'),
        {"comment": "决策分析静态配置表：存储库房-设备-点位的静态元数据，变更频率低"}
    )
    
    # 主键
    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)"
    )
    
    # ✅ 必选字段（全部来自现有 key，严格保持不变）
    room_id = Column(String(10), nullable=False, comment="库房编号")
    device_type = Column(String(50), nullable=False, comment="设备类型：来自devices的一级key（如air_cooler/fresh_air_fan/humidifier/grow_light）")
    device_name = Column(String(100), nullable=False, comment="设备名称：如TD1_Q611MDCH01")
    device_alias = Column(String(100), nullable=False, comment="设备别名：如air_cooler_611")
    point_alias = Column(String(100), nullable=False, comment="点位别名：如on_off")
    point_name = Column(String(100), nullable=False, comment="点位名称：如OnOff")
    remark = Column(String(200), nullable=True, comment="点位备注：如冷风机开关")
    change_type = Column(String(50), nullable=False, comment="变更类型：digital_on_off/analog_value/enum_state")
    threshold = Column(Float, nullable=True, comment="阈值：模拟量变更阈值，可空")
    enum_mapping = Column(JSON, nullable=True, comment="枚举映射：数字状态到文本的映射，JSON格式存储")
    
    # ➕ 推荐新增字段（不影响前端现有 key）
    config_version = Column(Integer, nullable=False, default=1, comment="配置版本号：递增整数")
    is_active = Column(Boolean, nullable=False, default=True, comment="是否启用：true/false")
    effective_time = Column(DateTime, nullable=False, server_default=func.now(), comment="配置生效时间")
    source = Column(String(50), nullable=False, default="manual", comment="配置来源：manual/import/platform等")
    operator = Column(String(100), nullable=True, comment="配置维护人/系统")
    comment = Column(Text, nullable=True, comment="配置备注")
    
    # 时间戳
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")


class DecisionAnalysisDynamicResult(Base):
    """决策分析动态结果表 - 存储模型每次输出的调节结果"""
    __tablename__ = "decision_analysis_dynamic_result"
    
    __table_args__ = (
        # 查询索引
        Index('idx_decision_room_batch_time', 'room_id', 'batch_id', 'time'),
        Index('idx_decision_batch_id', 'batch_id'),
        Index('idx_decision_time', 'time'),
        Index('idx_decision_dynamic_device_point', 'device_alias', 'point_alias'),
        Index('idx_decision_change_status', 'change', 'status'),
        Index('idx_decision_room_time', 'room_id', 'time'),
        Index('idx_decision_dynamic_device_type', 'device_type'),
        Index('idx_decision_dynamic_status', 'status'),
        Index('idx_decision_dynamic_apply_time', 'apply_time'),
        {"comment": "决策分析动态结果表：存储模型每次输出的调节结果，高频写入用于回溯对比"}
    )
    
    # 主键
    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)"
    )
    
    # ✅ 必选字段（保持现有 key 不变）
    room_id = Column(String(10), nullable=False, comment="库房编号")
    device_type = Column(String(50), nullable=False, comment="设备类型")
    device_alias = Column(String(100), nullable=False, comment="设备别名")
    point_alias = Column(String(100), nullable=False, comment="点位别名")
    change = Column(Boolean, nullable=False, comment="是否变更：true/false")
    old = Column(String(100), nullable=True, comment="变更前值：兼容数字/枚举/开关量，string存储")
    new = Column(String(100), nullable=True, comment="变更后值：兼容数字/枚举/开关量，string存储")
    level = Column(String(20), nullable=False, comment="变更级别：high/medium/low")
    
    # ➕ 强烈建议新增字段（用于一致性、追溯、闭环）
    # (1) 事件定位 / 去重
    batch_id = Column(String(100), nullable=False, comment="一次模型推理批次号：把同一批输出聚合在一起")
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
    status = Column(String(20), nullable=False, default="pending", comment="状态：pending/applied/rejected/failed")
    apply_time = Column(DateTime, nullable=True, comment="实际下发时间")
    apply_result = Column(Text, nullable=True, comment="下发结果：成功/失败原因")
    operator = Column(String(100), nullable=True, comment="操作者：人/系统")
    rollback = Column(Boolean, nullable=False, default=False, comment="是否回滚")
    
    # (5) 便于排查的冗余字段
    device_name = Column(String(100), nullable=True, comment="设备名称：来自现有key，冗余存储减少join")
    point_name = Column(String(100), nullable=True, comment="点位名称：来自现有key，冗余存储")
    remark = Column(String(200), nullable=True, comment="点位备注：来自现有key，可选冗余")
    
    # 时间戳
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")


class EnhancedDecisionAnalysisResult(Base):
    """增强决策分析结果存储表"""
    __tablename__ = "enhanced_decision_analysis_results"
    
    __table_args__ = (
        Index('idx_room_analysis_time', 'room_id', 'analysis_datetime'),
        Index('idx_analysis_datetime', 'analysis_datetime'),
        Index('idx_room_id', 'room_id'),
        Index('idx_status', 'status'),
        Index('idx_output_format', 'output_format'),
        Index('idx_created_at', 'created_at'),
        {"comment": "增强决策分析结果存储表，支持监控点配置和完整决策数据"}
    )
    
    # 主键和基本信息
    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)"
    )
    
    room_id = Column(String(10), nullable=False, comment="库房编号")
    analysis_datetime = Column(DateTime, nullable=False, comment="分析时间")
    status = Column(String(20), nullable=False, default="success", comment="分析状态: success, failed, warning")
    output_format = Column(String(20), nullable=False, comment="输出格式: monitoring, enhanced, both")
    
    # 核心策略信息
    core_objective = Column(Text, nullable=True, comment="核心决策目标")
    priority_ranking = Column(JSON, nullable=True, comment="优先级排序列表")
    key_risk_points = Column(JSON, nullable=True, comment="关键风险点列表")
    
    # 设备推荐数据 (完整的JSON结构)
    device_recommendations = Column(
        JSON,
        nullable=True,
        comment="设备推荐配置: 包含所有设备类型的详细参数调整建议"
    )
    
    # 监控点配置数据 (符合monitoring_points_config.json格式)
    monitoring_points_config = Column(
        JSON,
        nullable=True,
        comment="监控点配置数据: 符合monitoring_points_config.json格式，包含change/old/new/level字段"
    )
    
    # 多图像分析信息
    multi_image_analysis = Column(
        JSON,
        nullable=True,
        comment="多图像分析结果: {total_images, confidence_score, view_consistency, aggregation_method}"
    )
    
    # 相似案例信息
    similar_cases_info = Column(
        JSON,
        nullable=True,
        comment="相似历史案例信息: {case_count, avg_similarity, case_details}"
    )
    
    # 环境数据统计
    env_data_stats = Column(
        JSON,
        nullable=True,
        comment="环境数据统计: {temperature, humidity, co2, data_points_count}"
    )
    
    # 设备变更记录统计
    device_changes_stats = Column(
        JSON,
        nullable=True,
        comment="设备变更记录统计: {total_changes, changes_by_type, recent_changes_count}"
    )
    
    # 处理性能指标
    processing_time = Column(Float, nullable=False, default=0.0, comment="处理耗时(秒)")
    analysis_time = Column(Float, nullable=True, comment="分析耗时(秒)")
    save_time = Column(Float, nullable=True, comment="保存耗时(秒)")
    memory_usage_mb = Column(Float, nullable=True, comment="内存使用量(MB)")
    
    # 数据源信息
    data_sources_count = Column(Integer, nullable=True, comment="数据源数量")
    total_records_processed = Column(Integer, nullable=True, comment="处理的总记录数")
    multi_image_count = Column(Integer, nullable=True, comment="多图像数量")
    
    # 质量指标
    warnings_count = Column(Integer, nullable=False, default=0, comment="警告数量")
    errors_count = Column(Integer, nullable=False, default=0, comment="错误数量")
    
    # 输出文件信息
    output_file_path = Column(Text, nullable=True, comment="输出文件路径")
    output_file_size = Column(BigInteger, nullable=True, comment="输出文件大小(字节)")
    
    # LLM相关信息
    llm_model_used = Column(String(100), nullable=True, comment="使用的LLM模型")
    llm_response_time = Column(Float, nullable=True, comment="LLM响应时间(秒)")
    llm_token_count = Column(Integer, nullable=True, comment="LLM令牌数量")
    llm_fallback_used = Column(Boolean, nullable=False, default=False, comment="是否使用了LLM后备策略")
    
    # 完整的原始输出数据 (用于备份和调试)
    raw_output_data = Column(
        JSON,
        nullable=True,
        comment="完整的原始输出数据: 包含所有分析结果的完整JSON结构"
    )
    
    # 错误和警告信息
    error_messages = Column(JSON, nullable=True, comment="错误信息列表")
    warning_messages = Column(JSON, nullable=True, comment="警告信息列表")
    
    # 时间戳
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")
    
    @staticmethod
    def from_analysis_result(result_data: dict, room_id: str, analysis_datetime: datetime, 
                           output_format: str = "monitoring") -> 'EnhancedDecisionAnalysisResult':
        """
        从分析结果数据创建数据库记录
        
        Args:
            result_data: 分析结果数据字典
            room_id: 库房编号
            analysis_datetime: 分析时间
            output_format: 输出格式
            
        Returns:
            EnhancedDecisionAnalysisResult实例
        """
        record = EnhancedDecisionAnalysisResult()
        
        # 基本信息
        record.room_id = room_id
        record.analysis_datetime = analysis_datetime
        record.output_format = output_format
        record.status = result_data.get("status", "success")
        
        # 处理不同格式的数据
        if output_format == "both" and "enhanced_decision" in result_data:
            enhanced_data = result_data["enhanced_decision"]
            monitoring_data = result_data.get("monitoring_points", {})
        elif output_format == "enhanced":
            enhanced_data = result_data
            monitoring_data = {}
        else:  # monitoring format
            enhanced_data = {}
            monitoring_data = result_data
        
        # 提取策略信息
        if "strategy" in enhanced_data:
            strategy = enhanced_data["strategy"]
            record.core_objective = strategy.get("core_objective")
            record.priority_ranking = strategy.get("priority_ranking", [])
            record.key_risk_points = strategy.get("key_risk_points", [])
        
        # 设备推荐数据
        record.device_recommendations = enhanced_data.get("device_recommendations")
        
        # 监控点配置数据
        record.monitoring_points_config = monitoring_data if monitoring_data else None
        
        # 多图像分析信息
        if "multi_image_analysis" in enhanced_data:
            record.multi_image_analysis = enhanced_data["multi_image_analysis"]
        
        # 元数据信息
        metadata = result_data.get("metadata", {})
        record.processing_time = metadata.get("processing_time", 0.0)
        record.total_records_processed = metadata.get("total_records", 0)
        
        # 完整原始数据
        record.raw_output_data = result_data
        
        return record
    
    def to_monitoring_points_format(self) -> dict:
        """
        转换为监控点配置格式
        
        Returns:
            监控点配置格式的字典
        """
        if self.monitoring_points_config:
            return self.monitoring_points_config
        
        # 如果没有监控点配置，尝试从设备推荐生成
        if self.device_recommendations:
            # 这里可以实现从设备推荐转换为监控点格式的逻辑
            pass
        
        return {
            "room_id": self.room_id,
            "devices": {},
            "metadata": {
                "generated_at": self.created_at.isoformat() if self.created_at else None,
                "room_id": self.room_id,
                "source": "enhanced_decision_analysis",
                "total_points": 0
            }
        }
    
    def get_summary_stats(self) -> dict:
        """
        获取分析结果的摘要统计信息
        
        Returns:
            摘要统计信息字典
        """
        stats = {
            "room_id": self.room_id,
            "analysis_datetime": self.analysis_datetime.isoformat() if self.analysis_datetime else None,
            "status": self.status,
            "processing_time": self.processing_time,
            "multi_image_count": self.multi_image_count,
            "warnings_count": self.warnings_count,
            "errors_count": self.errors_count,
            "llm_fallback_used": self.llm_fallback_used
        }
        
        # 添加监控点统计
        if self.monitoring_points_config:
            config = self.monitoring_points_config
            if isinstance(config, dict) and "devices" in config:
                total_points = 0
                changes_required = 0
                
                for device_type, device_list in config["devices"].items():
                    if isinstance(device_list, list):
                        for device in device_list:
                            points = device.get("point_list", [])
                            total_points += len(points)
                            changes_required += sum(1 for p in points if p.get("change", False))
                
                stats.update({
                    "total_monitoring_points": total_points,
                    "changes_required": changes_required,
                    "change_percentage": (changes_required / total_points * 100) if total_points > 0 else 0
                })
        
        return stats


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
                logger.info("[Migration] Adding collection_ip field to mushroom_embedding table...")
                
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
                logger.info("[Migration] Successfully added collection_ip field and indexes")
                
                # 更新现有记录
                update_existing_collection_ip_data()
                
            else:
                logger.info("[Migration] collection_ip field already exists, skipping migration")
                
    except Exception as e:
        logger.error(f"[Migration] Failed to add collection_ip field: {e}")
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
            records = session.query(MushroomImageEmbedding).filter(
                MushroomImageEmbedding.collection_ip.is_(None)
            ).all()
            
            logger.info(f"[Migration] Found {len(records)} records to update")
            
            updated_count = 0
            failed_count = 0
            
            for record in records:
                try:
                    # 从image_path中提取IP地址
                    ip_address = MushroomImageEmbedding.extract_collection_ip_from_path(record.image_path)
                    
                    if ip_address:
                        record.collection_ip = ip_address
                        updated_count += 1
                        
                        # 每100条记录提交一次
                        if updated_count % 100 == 0:
                            session.commit()
                            logger.info(f"[Migration] Updated {updated_count} records so far...")
                    else:
                        failed_count += 1
                        logger.debug(f"[Migration] Could not extract IP from: {record.image_path}")
                        
                except Exception as e:
                    failed_count += 1
                    logger.warning(f"[Migration] Error processing record {record.id}: {e}")
            
            # 最终提交
            session.commit()
            
            logger.info(f"[Migration] Collection IP update completed:")
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
            records_with_ip = session.query(MushroomImageEmbedding).filter(
                MushroomImageEmbedding.collection_ip.isnot(None)
            ).count()
            
            # 统计无IP地址的记录数
            records_without_ip = session.query(MushroomImageEmbedding).filter(
                MushroomImageEmbedding.collection_ip.is_(None)
            ).count()
            
            # 统计不同IP地址的数量
            unique_ips = session.query(MushroomImageEmbedding.collection_ip).distinct().filter(
                MushroomImageEmbedding.collection_ip.isnot(None)
            ).count()
            
            # 按IP地址分组统计
            ip_counts = session.query(
                MushroomImageEmbedding.collection_ip,
                func.count(MushroomImageEmbedding.id).label('count')
            ).filter(
                MushroomImageEmbedding.collection_ip.isnot(None)
            ).group_by(MushroomImageEmbedding.collection_ip).all()
            
            validation_result = {
                'total_records': total_records,
                'records_with_ip': records_with_ip,
                'records_without_ip': records_without_ip,
                'unique_ips': unique_ips,
                'coverage_percentage': (records_with_ip / total_records * 100) if total_records > 0 else 0,
                'ip_distribution': {ip: count for ip, count in ip_counts}
            }
            
            logger.info(f"[Validation] Collection IP field validation results:")
            logger.info(f"  - Total records: {validation_result['total_records']}")
            logger.info(f"  - Records with IP: {validation_result['records_with_ip']}")
            logger.info(f"  - Records without IP: {validation_result['records_without_ip']}")
            logger.info(f"  - Unique IP addresses: {validation_result['unique_ips']}")
            logger.info(f"  - Coverage: {validation_result['coverage_percentage']:.2f}%")
            
            if validation_result['ip_distribution']:
                logger.info("  - IP distribution:")
                for ip, count in validation_result['ip_distribution'].items():
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

    # 4. 创建推理结果表
    create_inference_tables()

    # 5. 添加collection_ip字段（如果不存在）
    try:
        add_collection_ip_field()
    except Exception as e:
        logger.error(f"[0.1.3] Failed to add collection_ip field: {str(e)}")

    # 6. 创建向量索引
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


def test_enhanced_decision_analysis_table():
    """
    测试增强决策分析结果表的功能
    
    包括创建示例数据、查询和验证功能
    """
    logger.info("[Test] Starting enhanced decision analysis table test...")
    
    try:
        # 1. 创建示例数据
        sample_record = create_enhanced_decision_analysis_sample_data()
        
        if sample_record:
            # 2. 测试查询功能
            results = query_enhanced_decision_analysis_results(
                room_id="611",
                limit=10
            )
            
            logger.info(f"[Test] Query returned {len(results)} records")
            
            # 3. 测试转换功能
            if results:
                monitoring_format = results[0].to_monitoring_points_format()
                logger.info(f"[Test] Monitoring format conversion successful: {len(monitoring_format.get('devices', {}))} device types")
            
            logger.info("[Test] Enhanced decision analysis table test completed successfully")
            return True
        else:
            logger.error("[Test] Enhanced decision analysis table test failed - no sample record created")
            return False
            
    except Exception as e:
        logger.error(f"[Test] Enhanced decision analysis table test failed: {e}")
        return False


def create_enhanced_decision_analysis_sample_data():
    """
    创建增强决策分析结果表的示例数据
    
    用于测试和验证新表结构的功能
    """
    try:
        from datetime import datetime
        from sqlalchemy.orm import sessionmaker
        
        logger.info("[Sample Data] Creating sample enhanced decision analysis data...")
        
        # 创建数据库会话
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            # 示例数据 - 基于实际的JSON输出结构
            sample_data = {
                "room_id": "611",
                "devices": {
                    "air_cooler": [
                        {
                            "device_name": "TD1_Q611MDCH01",
                            "device_alias": "air_cooler_611",
                            "point_list": [
                                {
                                    "point_alias": "on_off",
                                    "point_name": "OnOff",
                                    "remark": "冷风机开关",
                                    "change_type": "digital_on_off",
                                    "threshold": None,
                                    "enum_mapping": {"0": "关闭", "1": "开启"},
                                    "change": True,
                                    "old": 1,
                                    "new": 0,
                                    "level": "medium"
                                },
                                {
                                    "point_alias": "temp_set",
                                    "point_name": "TemSet",
                                    "remark": "温度设定(分辨率0.1)",
                                    "change_type": "analog_value",
                                    "threshold": 0.5,
                                    "enum_mapping": None,
                                    "change": False,
                                    "old": 18.5,
                                    "new": 18.5,
                                    "level": "low"
                                }
                            ]
                        }
                    ]
                },
                "metadata": {
                    "generated_at": "2026-01-23T12:25:31.035938",
                    "room_id": "611",
                    "source": "enhanced_decision_analysis",
                    "total_points": 31
                }
            }
            
            # 创建示例记录
            sample_record = EnhancedDecisionAnalysisResult.from_analysis_result(
                result_data=sample_data,
                room_id="611",
                analysis_datetime=datetime.now(),
                output_format="monitoring"
            )
            
            # 添加额外的示例数据
            sample_record.core_objective = "保障当前Mushroom Room 611, Day 31.阶段的生理需求稳定性"
            sample_record.priority_ranking = ["生理需求稳定性", "降低单位包数能耗", "维持库房内环境参数空间一致性"]
            sample_record.key_risk_points = ["温度过低可能导致菌丝抑制", "湿度不均易引发局部霉变", "CO₂浓度异常影响出菇质量"]
            sample_record.multi_image_analysis = {
                "total_images_analyzed": 144,
                "confidence_score": 0.945,
                "view_consistency": "high",
                "aggregation_method": "weighted_average"
            }
            sample_record.processing_time = 29.26
            sample_record.multi_image_count = 144
            sample_record.data_sources_count = 3
            sample_record.warnings_count = 1
            sample_record.errors_count = 0
            
            # 保存到数据库
            session.add(sample_record)
            session.commit()
            
            logger.info(f"[Sample Data] Successfully created sample record with ID: {sample_record.id}")
            
            # 验证数据
            verification_record = session.query(EnhancedDecisionAnalysisResult).filter_by(
                id=sample_record.id
            ).first()
            
            if verification_record:
                logger.info("[Sample Data] Sample data verification successful")
                logger.info(f"  - Room ID: {verification_record.room_id}")
                logger.info(f"  - Analysis Time: {verification_record.analysis_datetime}")
                logger.info(f"  - Processing Time: {verification_record.processing_time}s")
                logger.info(f"  - Multi-image Count: {verification_record.multi_image_count}")
                logger.info(f"  - Status: {verification_record.status}")
                
                # 测试摘要统计功能
                summary_stats = verification_record.get_summary_stats()
                logger.info(f"  - Summary Stats: {summary_stats}")
                
                return verification_record
            else:
                logger.error("[Sample Data] Sample data verification failed")
                return None
                
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"[Sample Data] Failed to create sample data: {e}")
        raise


def store_enhanced_decision_analysis_result(result_data: dict, room_id: str, 
                                          analysis_datetime: datetime, 
                                          output_format: str = "monitoring",
                                          output_file_path: str = None) -> str:
    """
    存储增强决策分析结果到数据库
    
    Args:
        result_data: 分析结果数据字典
        room_id: 库房编号
        analysis_datetime: 分析时间
        output_format: 输出格式
        output_file_path: 输出文件路径
        
    Returns:
        记录ID (UUID字符串)
    """
    try:
        from sqlalchemy.orm import sessionmaker
        
        logger.info(f"[Storage] Storing enhanced decision analysis result for room {room_id}")
        
        # 创建数据库会话
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            # 创建记录
            record = EnhancedDecisionAnalysisResult.from_analysis_result(
                result_data=result_data,
                room_id=room_id,
                analysis_datetime=analysis_datetime,
                output_format=output_format
            )
            
            # 设置输出文件信息
            if output_file_path:
                record.output_file_path = output_file_path
                try:
                    from pathlib import Path
                    file_path = Path(output_file_path)
                    if file_path.exists():
                        record.output_file_size = file_path.stat().st_size
                except Exception as e:
                    logger.warning(f"[Storage] Could not get file size: {e}")
            
            # 保存到数据库
            session.add(record)
            session.commit()
            
            record_id = str(record.id)
            logger.info(f"[Storage] Successfully stored record with ID: {record_id}")
            
            return record_id
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"[Storage] Failed to store enhanced decision analysis result: {e}")
        raise


def query_enhanced_decision_analysis_results(room_id: str = None, 
                                           start_date: datetime = None,
                                           end_date: datetime = None,
                                           status: str = None,
                                           limit: int = 100) -> list:
    """
    查询增强决策分析结果
    
    Args:
        room_id: 库房编号过滤
        start_date: 开始日期过滤
        end_date: 结束日期过滤
        status: 状态过滤
        limit: 结果数量限制
        
    Returns:
        查询结果列表
    """
    try:
        from sqlalchemy.orm import sessionmaker
        
        logger.info(f"[Query] Querying enhanced decision analysis results")
        
        # 创建数据库会话
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            # 构建查询
            query = session.query(EnhancedDecisionAnalysisResult)
            
            # 应用过滤条件
            if room_id:
                query = query.filter(EnhancedDecisionAnalysisResult.room_id == room_id)
            
            if start_date:
                query = query.filter(EnhancedDecisionAnalysisResult.analysis_datetime >= start_date)
            
            if end_date:
                query = query.filter(EnhancedDecisionAnalysisResult.analysis_datetime <= end_date)
            
            if status:
                query = query.filter(EnhancedDecisionAnalysisResult.status == status)
            
            # 按分析时间降序排列
            query = query.order_by(EnhancedDecisionAnalysisResult.analysis_datetime.desc())
            
            # 应用限制
            if limit:
                query = query.limit(limit)
            
            results = query.all()
            
            logger.info(f"[Query] Found {len(results)} records")
            
            return results
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"[Query] Failed to query enhanced decision analysis results: {e}")
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
                            "operator": "system"
                        }
                        static_configs.append(config)
        
        return static_configs
        
    except Exception as e:
        logger.error(f"[Static Config] Failed to extract static config from JSON: {e}")
        return []


def extract_dynamic_results_from_json(json_data: dict, batch_id: str, 
                                     analysis_time: datetime) -> list:
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
            
            logger.info(f"[Dynamic Results] Processing monitoring_points format for room {room_id}")
            
        elif "enhanced_decision" in json_data:
            # enhanced格式 - 需要从device_recommendations转换
            enhanced_data = json_data["enhanced_decision"]
            room_id = enhanced_data.get("room_id")
            
            logger.warning(f"[Dynamic Results] Enhanced format detected, but no monitoring_points found")
            return []  # enhanced格式的device_recommendations不适合直接转换为动态结果
            
        elif "devices" in json_data:
            # 纯monitoring格式
            room_id = json_data.get("room_id")
            devices = json_data.get("devices", {})
            
            logger.info(f"[Dynamic Results] Processing pure monitoring format for room {room_id}")
            
        else:
            logger.warning("[Dynamic Results] Unknown JSON format, no suitable data structure found")
            return []
        
        if not room_id:
            logger.error("[Dynamic Results] No room_id found in JSON data")
            return []
        
        # 处理设备和点位数据
        for device_type, device_list in devices.items():
            if not isinstance(device_list, list):
                logger.warning(f"[Dynamic Results] Device list for {device_type} is not a list, skipping")
                continue
                
            for device in device_list:
                device_name = device.get("device_name")
                device_alias = device.get("device_alias")
                point_list = device.get("point_list", [])
                
                logger.debug(f"[Dynamic Results] Processing device {device_alias} with {len(point_list)} points")
                
                for point in point_list:
                    # 确保必要字段存在
                    point_alias = point.get("point_alias")
                    if not point_alias:
                        logger.warning(f"[Dynamic Results] Point missing point_alias, skipping")
                        continue
                    
                    result = {
                        "room_id": room_id,
                        "device_type": device_type,
                        "device_alias": device_alias,
                        "point_alias": point_alias,
                        "change": bool(point.get("change", False)),
                        "old": str(point.get("old")) if point.get("old") is not None else "0",
                        "new": str(point.get("new")) if point.get("new") is not None else "0",
                        "level": point.get("level", "low"),
                        "batch_id": batch_id,
                        "time": analysis_time,
                        "device_name": device_name,
                        "point_name": point.get("point_name"),
                        "remark": point.get("remark"),
                        "model_name": "enhanced_decision_analysis",
                        "status": "pending"
                    }
                    dynamic_results.append(result)
                    
                    logger.debug(f"[Dynamic Results] Added result for {device_type}.{point_alias}: change={result['change']}, old={result['old']}, new={result['new']}")
        
        logger.info(f"[Dynamic Results] Extracted {len(dynamic_results)} dynamic results from JSON")
        return dynamic_results
        
    except Exception as e:
        logger.error(f"[Dynamic Results] Failed to extract dynamic results from JSON: {e}")
        return []


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
        from sqlalchemy.dialects.postgresql import insert
        
        logger.info(f"[Static Config] Storing {len(configs)} static point configs...")
        
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            stored_count = 0
            
            for config in configs:
                # 检查是否已存在相同配置
                existing = session.query(DecisionAnalysisStaticConfig).filter_by(
                    room_id=config["room_id"],
                    device_alias=config["device_alias"],
                    point_alias=config["point_alias"]
                ).first()
                
                if existing:
                    # 更新现有配置
                    for key, value in config.items():
                        if hasattr(existing, key) and key not in ['id', 'created_at']:
                            setattr(existing, key, value)
                    existing.config_version += 1
                    existing.updated_at = func.now()
                else:
                    # 创建新配置
                    new_config = DecisionAnalysisStaticConfig(**config)
                    session.add(new_config)
                
                stored_count += 1
            
            session.commit()
            logger.info(f"[Static Config] Successfully stored {stored_count} static point configs")
            
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
        
        logger.info(f"[Dynamic Results] Storing {len(results)} dynamic point results...")
        
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            # 批量插入动态结果
            for result in results:
                new_result = DecisionAnalysisDynamicResult(**result)
                session.add(new_result)
            
            session.commit()
            logger.info(f"[Dynamic Results] Successfully stored {len(results)} dynamic point results")
            
            return len(results)
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"[Dynamic Results] Failed to store dynamic point results: {e}")
        raise


def store_decision_analysis_dynamic_results_only(json_data: dict, room_id: str, 
                                               analysis_time: datetime, 
                                               batch_id: str = None) -> dict:
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
        
        logger.info(f"[Dynamic Storage] Storing dynamic results only for room {room_id}, batch {batch_id}")
        
        # 仅提取并存储动态结果
        dynamic_results = extract_dynamic_results_from_json(json_data, batch_id, analysis_time)
        dynamic_count = store_decision_analysis_dynamic_results(dynamic_results) if dynamic_results else 0
        
        result_stats = {
            "batch_id": batch_id,
            "room_id": room_id,
            "analysis_time": analysis_time.isoformat(),
            "dynamic_results_stored": dynamic_count,
            "dynamic_results_count": dynamic_count,
            "change_count": len([r for r in dynamic_results if r.get("change", False)]) if dynamic_results else 0,
            "total_points_processed": len(dynamic_results) if dynamic_results else 0,
            "processing_time": 0.0,
            "static_configs_skipped": True  # 标记跳过了静态配置存储
        }
        
        logger.info(f"[Dynamic Storage] Dynamic results stored successfully:")
        logger.info(f"  - Batch ID: {batch_id}")
        logger.info(f"  - Dynamic results: {dynamic_count}")
        logger.info(f"  - Changes: {result_stats['change_count']}")
        logger.info(f"  - Static configs: skipped (optimization)")
        
        return result_stats
        
    except Exception as e:
        logger.error(f"[Dynamic Storage] Failed to store dynamic results: {e}")
        raise


def store_decision_analysis_results(json_data: dict, room_id: str, 
                              analysis_time: datetime, 
                              batch_id: str = None) -> dict:
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
        
        logger.info(f"[IoT Storage] Storing IoT analysis results for room {room_id}, batch {batch_id}")
        
        # 1. 提取并存储静态配置
        static_configs = extract_static_config_from_json(json_data)
        static_count = store_decision_analysis_static_configs(static_configs) if static_configs else 0
        
        # 2. 提取并存储动态结果
        dynamic_results = extract_dynamic_results_from_json(json_data, batch_id, analysis_time)
        dynamic_count = store_decision_analysis_dynamic_results(dynamic_results) if dynamic_results else 0
        
        result_stats = {
            "batch_id": batch_id,
            "room_id": room_id,
            "analysis_time": analysis_time.isoformat(),
            "static_configs_stored": static_count,
            "dynamic_results_stored": dynamic_count,
            "dynamic_results_count": dynamic_count,  # 添加这个字段用于显示
            "change_count": len([r for r in dynamic_results if r.get("change", False)]) if dynamic_results else 0,  # 计算变更数量
            "total_points_processed": len(static_configs) if static_configs else 0,
            "processing_time": 0.0  # 这里可以添加处理时间统计
        }
        
        logger.info(f"[IoT Storage] IoT analysis results stored successfully:")
        logger.info(f"  - Batch ID: {batch_id}")
        logger.info(f"  - Static configs: {static_count}")
        logger.info(f"  - Dynamic results: {dynamic_count}")
        
        return result_stats
        
    except Exception as e:
        logger.error(f"[IoT Storage] Failed to store IoT analysis results: {e}")
        raise


def query_decision_analysis_static_configs(room_id: str = None, device_type: str = None, 
                              device_alias: str = None, is_active: bool = True,
                              limit: int = 1000) -> list:
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
        
        logger.info(f"[Static Config Query] Querying static point configs")
        
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            query = session.query(DecisionAnalysisStaticConfig)
            
            if room_id:
                query = query.filter(DecisionAnalysisStaticConfig.room_id == room_id)
            if device_type:
                query = query.filter(DecisionAnalysisStaticConfig.device_type == device_type)
            if device_alias:
                query = query.filter(DecisionAnalysisStaticConfig.device_alias == device_alias)
            if is_active is not None:
                query = query.filter(DecisionAnalysisStaticConfig.is_active == is_active)
            
            query = query.order_by(
                DecisionAnalysisStaticConfig.room_id,
                DecisionAnalysisStaticConfig.device_type,
                DecisionAnalysisStaticConfig.device_alias,
                DecisionAnalysisStaticConfig.point_alias
            )
            
            if limit:
                query = query.limit(limit)
            
            results = query.all()
            logger.info(f"[Static Config Query] Found {len(results)} static point configs")
            
            return results
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"[Static Config Query] Failed to query static point configs: {e}")
        raise


def query_decision_analysis_dynamic_results(room_id: str = None, batch_id: str = None,
                               device_alias: str = None, point_alias: str = None,
                               change_only: bool = False, status: str = None,
                               start_time: datetime = None, end_time: datetime = None,
                               limit: int = 1000) -> list:
    """
    查询动态点位结果
    
    Args:
        room_id: 库房编号过滤
        batch_id: 批次ID过滤
        device_alias: 设备别名过滤
        point_alias: 点位别名过滤
        change_only: 是否只查询有变更的记录
        status: 状态过滤
        start_time: 开始时间过滤
        end_time: 结束时间过滤
        limit: 结果数量限制
        
    Returns:
        查询结果列表
    """
    try:
        from sqlalchemy.orm import sessionmaker
        
        logger.info(f"[Dynamic Results Query] Querying dynamic point results")
        
        Session = sessionmaker(bind=pgsql_engine)
        session = Session()
        
        try:
            query = session.query(DecisionAnalysisDynamicResult)
            
            if room_id:
                query = query.filter(DecisionAnalysisDynamicResult.room_id == room_id)
            if batch_id:
                query = query.filter(DecisionAnalysisDynamicResult.batch_id == batch_id)
            if device_alias:
                query = query.filter(DecisionAnalysisDynamicResult.device_alias == device_alias)
            if point_alias:
                query = query.filter(DecisionAnalysisDynamicResult.point_alias == point_alias)
            if change_only:
                query = query.filter(DecisionAnalysisDynamicResult.change == True)
            if status:
                query = query.filter(DecisionAnalysisDynamicResult.status == status)
            if start_time:
                query = query.filter(DecisionAnalysisDynamicResult.time >= start_time)
            if end_time:
                query = query.filter(DecisionAnalysisDynamicResult.time <= end_time)
            
            query = query.order_by(DecisionAnalysisDynamicResult.time.desc())
            
            if limit:
                query = query.limit(limit)
            
            results = query.all()
            logger.info(f"[Dynamic Results Query] Found {len(results)} dynamic point results")
            
            return results
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"[Dynamic Results Query] Failed to query dynamic point results: {e}")
        raise


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
        
        with open(latest_file, 'r', encoding='utf-8') as f:
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
        storage_result = store_decision_analysis_results(json_data, room_id, analysis_time)
        logger.info(f"[Test] Storage result: {storage_result}")
        
        # 3. 测试查询功能
        static_configs = query_decision_analysis_static_configs(room_id=room_id, limit=10)
        logger.info(f"[Test] Found {len(static_configs)} static configs")
        
        dynamic_results = query_decision_analysis_dynamic_results(
            room_id=room_id, 
            batch_id=storage_result["batch_id"],
            limit=10
        )
        logger.info(f"[Test] Found {len(dynamic_results)} dynamic results")
        
        # 4. 测试变更查询
        change_results = query_decision_analysis_dynamic_results(
            room_id=room_id,
            change_only=True,
            limit=10
        )
        logger.info(f"[Test] Found {len(change_results)} change results")
        
        logger.info("[Test] IoT tables test completed successfully")
        return True
        
    except Exception as e:
        logger.error(f"[Test] IoT tables test failed: {e}")
        return False


if __name__ == "__main__":
    create_tables()
    
    # 可选：测试增强决策分析表功能
    # test_enhanced_decision_analysis_table()
    
    # 可选：测试IoT表功能
    # test_iot_tables()

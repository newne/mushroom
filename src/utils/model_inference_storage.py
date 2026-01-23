"""
Model Inference Results Storage Module

This module provides functionality to store model inference results in the database,
including decision analysis outputs, risk assessments, and operational recommendations.
"""

import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

from loguru import logger
from sqlalchemy import (
    Column, String, DateTime, func, Index, Float, Integer, Text, Boolean, JSON
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import declarative_base, sessionmaker

from global_const.global_const import pgsql_engine

Base = declarative_base()


class ModelInferenceResult(Base):
    """
    模型推理结果存储表
    
    存储决策分析模型的完整推理结果，包括输入参数、输出结果、
    风险评估、置信度评分和操作建议等信息。
    """
    __tablename__ = "model_inference_results"
    
    __table_args__ = (
        # 索引定义
        Index('idx_inference_room_time', 'room_id', 'inference_time'),  # 房间+推理时间查询
        Index('idx_inference_datetime', 'inference_time'),  # 时间范围查询
        Index('idx_inference_model_version', 'model_version'),  # 模型版本查询
        Index('idx_inference_status', 'inference_status'),  # 状态查询
        Index('idx_inference_confidence', 'overall_confidence_score'),  # 置信度查询
        Index('idx_inference_room_growth', 'room_id', 'growth_day'),  # 房间+生长天数查询
        Index('idx_inference_analysis_type', 'analysis_type'),  # 分析类型查询
        {"comment": "模型推理结果存储表（决策分析、风险评估、操作建议）"}
    )
    
    # === 基本信息字段 ===
    id = Column(
        PgUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        comment="主键ID (UUID4)"
    )
    
    inference_time = Column(
        DateTime, 
        nullable=False, 
        default=datetime.utcnow,
        comment="推理执行时间"
    )
    
    room_id = Column(
        String(10), 
        nullable=False, 
        comment="库房编号"
    )
    
    analysis_type = Column(
        String(50),
        nullable=False,
        default="enhanced_decision_analysis",
        comment="分析类型 (enhanced_decision_analysis, basic_analysis, etc.)"
    )
    
    model_version = Column(
        String(50), 
        nullable=False, 
        comment="模型版本标识"
    )
    
    inference_status = Column(
        String(20),
        nullable=False,
        comment="推理状态 (success, partial, error, fallback)"
    )
    
    # === 输入参数字段 ===
    input_parameters = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="推理输入参数: {analysis_datetime, time_window, growth_day, etc.}"
    )
    
    environmental_context = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="环境上下文: {temperature, humidity, co2, growth_day, batch_info}"
    )
    
    device_current_state = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="设备当前状态: {air_cooler, fresh_air_fan, humidifier, grow_light}"
    )
    
    # === 推理输出字段 ===
    raw_inference_output = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="原始推理输出: 完整的模型输出结果"
    )
    
    device_recommendations = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="设备参数建议: {air_cooler, fresh_air_fan, humidifier, grow_light}"
    )
    
    control_strategy = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="控制策略: {core_objective, priority_ranking, key_risk_points}"
    )
    
    monitoring_points = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="监控要点: {key_time_periods, warning_thresholds, emergency_measures}"
    )
    
    # === 多图像分析字段 ===
    multi_image_analysis = Column(
        JSON,
        nullable=True,
        comment="多图像分析结果: {total_images, quality_scores, aggregation_method, confidence}"
    )
    
    image_analysis_metadata = Column(
        JSON,
        nullable=True,
        comment="图像分析元数据: {image_paths, quality_scores, processing_time}"
    )
    
    # === 置信度和风险评估字段 ===
    overall_confidence_score = Column(
        Float,
        nullable=False,
        default=0.0,
        comment="整体置信度评分 (0.0-1.0)"
    )
    
    risk_assessment_summary = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="风险评估汇总: {overall_risk, critical_risks, mitigation_strategies}"
    )
    
    parameter_confidence_scores = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="参数置信度评分: {air_cooler: {tem_set: 0.95, ...}, ...}"
    )
    
    # === 决策支持信息字段 ===
    priority_recommendations = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="优先级建议: {immediate: [], within_hour: [], within_day: [], routine: []}"
    )
    
    operational_guidance = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="操作指导: {step_by_step_actions, precautions, expected_outcomes}"
    )
    
    similar_cases_analysis = Column(
        JSON,
        nullable=True,
        comment="相似案例分析: {similar_cases_count, avg_similarity, case_references}"
    )
    
    # === 性能和质量指标 ===
    processing_time_seconds = Column(
        Float,
        nullable=False,
        default=0.0,
        comment="推理处理时间 (秒)"
    )
    
    data_quality_score = Column(
        Float,
        nullable=True,
        comment="输入数据质量评分 (0.0-1.0)"
    )
    
    model_performance_metrics = Column(
        JSON,
        nullable=True,
        comment="模型性能指标: {llm_response_time, clip_matching_time, template_rendering_time}"
    )
    
    # === 生长阶段和批次信息 ===
    growth_day = Column(
        Integer,
        nullable=True,
        comment="生长天数"
    )
    
    batch_info = Column(
        JSON,
        nullable=True,
        comment="批次信息: {in_date, in_num, batch_id}"
    )
    
    growth_stage = Column(
        String(50),
        nullable=True,
        comment="生长阶段 (germination, vegetative, reproductive, harvest)"
    )
    
    # === 警告和错误信息 ===
    warnings = Column(
        JSON,
        nullable=False,
        default=lambda: [],
        comment="警告信息列表"
    )
    
    errors = Column(
        JSON,
        nullable=False,
        default=lambda: [],
        comment="错误信息列表"
    )
    
    validation_results = Column(
        JSON,
        nullable=True,
        comment="验证结果: {parameter_validation, constraint_checks, sanity_checks}"
    )
    
    # === 应用状态字段 ===
    application_status = Column(
        String(20),
        nullable=False,
        default="pending",
        comment="应用状态 (pending, applied, rejected, expired)"
    )
    
    applied_at = Column(
        DateTime,
        nullable=True,
        comment="应用时间"
    )
    
    applied_by = Column(
        String(100),
        nullable=True,
        comment="应用操作员"
    )
    
    effectiveness_feedback = Column(
        JSON,
        nullable=True,
        comment="效果反馈: {outcome_rating, actual_vs_predicted, lessons_learned}"
    )
    
    # === 审计字段 ===
    created_at = Column(
        DateTime, 
        server_default=func.now(), 
        comment="创建时间"
    )
    
    updated_at = Column(
        DateTime, 
        server_default=func.now(), 
        onupdate=func.now(), 
        comment="更新时间"
    )
    
    created_by = Column(
        String(100),
        nullable=True,
        comment="创建者"
    )
    
    metadata_info = Column(
        JSON,
        nullable=False,
        default=lambda: {},
        comment="元数据信息: {data_sources, processing_pipeline, version_info}"
    )


class InferenceResultStorage:
    """
    模型推理结果存储服务类
    
    提供推理结果的存储、查询和管理功能
    """
    
    def __init__(self, db_engine=None):
        """
        初始化存储服务
        
        Args:
            db_engine: 数据库引擎，默认使用全局配置的pgsql_engine
        """
        self.engine = db_engine or pgsql_engine
        self.Session = sessionmaker(bind=self.engine)
        
        logger.info("[ModelInferenceStorage] Initialized inference result storage service")
    
    def store_inference_result(
        self,
        room_id: str,
        inference_output: Any,
        model_version: str = "enhanced_v1.0",
        analysis_type: str = "enhanced_decision_analysis",
        input_params: Optional[Dict] = None,
        processing_time: float = 0.0,
        created_by: Optional[str] = None
    ) -> str:
        """
        存储推理结果到数据库
        
        Args:
            room_id: 房间ID
            inference_output: 推理输出结果 (EnhancedDecisionOutput对象)
            model_version: 模型版本
            analysis_type: 分析类型
            input_params: 输入参数
            processing_time: 处理时间
            created_by: 创建者
            
        Returns:
            str: 存储记录的ID
        """
        session = self.Session()
        
        try:
            # 解析推理输出
            parsed_data = self._parse_inference_output(inference_output)
            
            # 创建存储记录
            inference_record = ModelInferenceResult(
                room_id=room_id,
                analysis_type=analysis_type,
                model_version=model_version,
                inference_status=parsed_data.get("status", "success"),
                
                # 输入参数
                input_parameters=input_params or {},
                environmental_context=parsed_data.get("environmental_context", {}),
                device_current_state=parsed_data.get("device_current_state", {}),
                
                # 推理输出
                raw_inference_output=parsed_data.get("raw_output", {}),
                device_recommendations=parsed_data.get("device_recommendations", {}),
                control_strategy=parsed_data.get("control_strategy", {}),
                monitoring_points=parsed_data.get("monitoring_points", {}),
                
                # 多图像分析
                multi_image_analysis=parsed_data.get("multi_image_analysis"),
                image_analysis_metadata=parsed_data.get("image_analysis_metadata"),
                
                # 置信度和风险评估
                overall_confidence_score=parsed_data.get("overall_confidence_score", 0.0),
                risk_assessment_summary=parsed_data.get("risk_assessment_summary", {}),
                parameter_confidence_scores=parsed_data.get("parameter_confidence_scores", {}),
                
                # 决策支持信息
                priority_recommendations=parsed_data.get("priority_recommendations", {}),
                operational_guidance=parsed_data.get("operational_guidance", {}),
                similar_cases_analysis=parsed_data.get("similar_cases_analysis"),
                
                # 性能指标
                processing_time_seconds=processing_time,
                data_quality_score=parsed_data.get("data_quality_score"),
                model_performance_metrics=parsed_data.get("model_performance_metrics", {}),
                
                # 生长信息
                growth_day=parsed_data.get("growth_day"),
                batch_info=parsed_data.get("batch_info"),
                growth_stage=parsed_data.get("growth_stage"),
                
                # 警告和错误
                warnings=parsed_data.get("warnings", []),
                errors=parsed_data.get("errors", []),
                validation_results=parsed_data.get("validation_results"),
                
                # 审计信息
                created_by=created_by,
                metadata_info=parsed_data.get("metadata_info", {})
            )
            
            session.add(inference_record)
            session.commit()
            
            record_id = str(inference_record.id)
            
            logger.info(
                f"[ModelInferenceStorage] Successfully stored inference result: "
                f"ID={record_id}, Room={room_id}, Status={inference_record.inference_status}"
            )
            
            return record_id
            
        except Exception as e:
            session.rollback()
            logger.error(f"[ModelInferenceStorage] Failed to store inference result: {e}")
            raise
        finally:
            session.close()
    
    def _parse_inference_output(self, inference_output: Any) -> Dict[str, Any]:
        """
        解析推理输出对象，提取各个字段的数据
        
        Args:
            inference_output: 推理输出对象
            
        Returns:
            Dict: 解析后的数据字典
        """
        try:
            # 如果是EnhancedDecisionOutput对象
            if hasattr(inference_output, 'status'):
                return self._parse_enhanced_decision_output(inference_output)
            
            # 如果是字典格式
            elif isinstance(inference_output, dict):
                return self._parse_dict_output(inference_output)
            
            # 其他格式，尝试转换为字典
            else:
                return {"raw_output": str(inference_output)}
                
        except Exception as e:
            logger.warning(f"[ModelInferenceStorage] Failed to parse inference output: {e}")
            return {"raw_output": str(inference_output)}
    
    def _parse_enhanced_decision_output(self, output) -> Dict[str, Any]:
        """解析EnhancedDecisionOutput对象"""
        
        def serialize_parameter_adjustment(param_adj):
            """序列化参数调整对象"""
            if param_adj is None:
                return None
            
            return {
                "current_value": param_adj.current_value,
                "recommended_value": param_adj.recommended_value,
                "action": param_adj.action,
                "change_reason": param_adj.change_reason,
                "priority": param_adj.priority,
                "urgency": param_adj.urgency,
                "risk_assessment": {
                    "adjustment_risk": param_adj.risk_assessment.adjustment_risk if param_adj.risk_assessment else None,
                    "no_action_risk": param_adj.risk_assessment.no_action_risk if param_adj.risk_assessment else None,
                    "impact_scope": param_adj.risk_assessment.impact_scope if param_adj.risk_assessment else None,
                } if param_adj.risk_assessment else None
            }
        
        # 解析设备建议
        device_recommendations = {}
        if hasattr(output, 'device_recommendations'):
            dr = output.device_recommendations
            
            # 冷风机
            if hasattr(dr, 'air_cooler'):
                ac = dr.air_cooler
                device_recommendations["air_cooler"] = {
                    "tem_set": serialize_parameter_adjustment(ac.tem_set),
                    "tem_diff_set": serialize_parameter_adjustment(ac.tem_diff_set),
                    "cyc_on_off": serialize_parameter_adjustment(ac.cyc_on_off),
                    "cyc_on_time": serialize_parameter_adjustment(ac.cyc_on_time),
                    "cyc_off_time": serialize_parameter_adjustment(ac.cyc_off_time),
                    "ar_on_off": serialize_parameter_adjustment(ac.ar_on_off),
                    "hum_on_off": serialize_parameter_adjustment(ac.hum_on_off),
                    "rationale": ac.rationale if hasattr(ac, 'rationale') else []
                }
            
            # 新风机
            if hasattr(dr, 'fresh_air_fan'):
                faf = dr.fresh_air_fan
                device_recommendations["fresh_air_fan"] = {
                    "model": serialize_parameter_adjustment(faf.model),
                    "control": serialize_parameter_adjustment(faf.control),
                    "co2_on": serialize_parameter_adjustment(faf.co2_on),
                    "co2_off": serialize_parameter_adjustment(faf.co2_off),
                    "on": serialize_parameter_adjustment(faf.on),
                    "off": serialize_parameter_adjustment(faf.off),
                    "rationale": faf.rationale if hasattr(faf, 'rationale') else []
                }
            
            # 加湿器
            if hasattr(dr, 'humidifier'):
                hum = dr.humidifier
                device_recommendations["humidifier"] = {
                    "model": serialize_parameter_adjustment(hum.model),
                    "on": serialize_parameter_adjustment(hum.on),
                    "off": serialize_parameter_adjustment(hum.off),
                    "left_right_strategy": hum.left_right_strategy if hasattr(hum, 'left_right_strategy') else "",
                    "rationale": hum.rationale if hasattr(hum, 'rationale') else []
                }
            
            # 补光灯
            if hasattr(dr, 'grow_light'):
                gl = dr.grow_light
                device_recommendations["grow_light"] = {
                    "model": serialize_parameter_adjustment(gl.model),
                    "on_mset": serialize_parameter_adjustment(gl.on_mset),
                    "off_mset": serialize_parameter_adjustment(gl.off_mset),
                    "on_off_1": serialize_parameter_adjustment(gl.on_off_1),
                    "choose_1": serialize_parameter_adjustment(gl.choose_1),
                    "on_off_2": serialize_parameter_adjustment(gl.on_off_2),
                    "choose_2": serialize_parameter_adjustment(gl.choose_2),
                    "on_off_3": serialize_parameter_adjustment(gl.on_off_3),
                    "choose_3": serialize_parameter_adjustment(gl.choose_3),
                    "on_off_4": serialize_parameter_adjustment(gl.on_off_4),
                    "choose_4": serialize_parameter_adjustment(gl.choose_4),
                    "rationale": gl.rationale if hasattr(gl, 'rationale') else []
                }
        
        # 解析控制策略
        control_strategy = {}
        if hasattr(output, 'strategy'):
            strategy = output.strategy
            control_strategy = {
                "core_objective": strategy.core_objective if hasattr(strategy, 'core_objective') else "",
                "priority_ranking": strategy.priority_ranking if hasattr(strategy, 'priority_ranking') else [],
                "key_risk_points": strategy.key_risk_points if hasattr(strategy, 'key_risk_points') else []
            }
        
        # 解析监控点
        monitoring_points = {}
        if hasattr(output, 'monitoring_points'):
            mp = output.monitoring_points
            monitoring_points = {
                "key_time_periods": mp.key_time_periods if hasattr(mp, 'key_time_periods') else [],
                "warning_thresholds": mp.warning_thresholds if hasattr(mp, 'warning_thresholds') else {},
                "emergency_measures": mp.emergency_measures if hasattr(mp, 'emergency_measures') else []
            }
        
        # 解析多图像分析
        multi_image_analysis = None
        if hasattr(output, 'multi_image_analysis') and output.multi_image_analysis:
            mia = output.multi_image_analysis
            multi_image_analysis = {
                "total_images_analyzed": mia.total_images_analyzed,
                "image_quality_scores": mia.image_quality_scores,
                "aggregation_method": mia.aggregation_method,
                "confidence_score": mia.confidence_score,
                "view_consistency": mia.view_consistency,
                "key_observations": mia.key_observations
            }
        
        # 计算整体置信度
        overall_confidence = 0.0
        if multi_image_analysis:
            overall_confidence = multi_image_analysis.get("confidence_score", 0.0)
        
        # 提取优先级建议
        priority_recommendations = self._extract_priority_recommendations(device_recommendations)
        
        # 提取风险评估汇总
        risk_assessment_summary = self._extract_risk_assessment_summary(device_recommendations)
        
        # 提取参数置信度
        parameter_confidence_scores = self._extract_parameter_confidence_scores(device_recommendations)
        
        # 解析元数据
        metadata_info = {}
        if hasattr(output, 'metadata'):
            metadata = output.metadata
            metadata_info = {
                "data_sources": metadata.data_sources if hasattr(metadata, 'data_sources') else {},
                "similar_cases_count": metadata.similar_cases_count if hasattr(metadata, 'similar_cases_count') else 0,
                "avg_similarity_score": metadata.avg_similarity_score if hasattr(metadata, 'avg_similarity_score') else 0.0,
                "llm_model": metadata.llm_model if hasattr(metadata, 'llm_model') else "",
                "llm_response_time": metadata.llm_response_time if hasattr(metadata, 'llm_response_time') else 0.0,
                "total_processing_time": metadata.total_processing_time if hasattr(metadata, 'total_processing_time') else 0.0,
                "multi_image_count": getattr(metadata, 'multi_image_count', 0),
                "image_aggregation_method": getattr(metadata, 'image_aggregation_method', 'single_image')
            }
        
        return {
            "status": output.status,
            "device_recommendations": device_recommendations,
            "control_strategy": control_strategy,
            "monitoring_points": monitoring_points,
            "multi_image_analysis": multi_image_analysis,
            "overall_confidence_score": overall_confidence,
            "priority_recommendations": priority_recommendations,
            "risk_assessment_summary": risk_assessment_summary,
            "parameter_confidence_scores": parameter_confidence_scores,
            "warnings": metadata_info.get("warnings", []) if hasattr(output, 'metadata') and hasattr(output.metadata, 'warnings') else [],
            "errors": metadata_info.get("errors", []) if hasattr(output, 'metadata') and hasattr(output.metadata, 'errors') else [],
            "metadata_info": metadata_info,
            "model_performance_metrics": {
                "llm_response_time": metadata_info.get("llm_response_time", 0.0),
                "total_processing_time": metadata_info.get("total_processing_time", 0.0),
                "multi_image_count": metadata_info.get("multi_image_count", 0)
            },
            "similar_cases_analysis": {
                "similar_cases_count": metadata_info.get("similar_cases_count", 0),
                "avg_similarity": metadata_info.get("avg_similarity_score", 0.0)
            } if metadata_info.get("similar_cases_count", 0) > 0 else None,
            "raw_output": {
                "status": output.status,
                "room_id": output.room_id,
                "analysis_time": output.analysis_time.isoformat() if hasattr(output, 'analysis_time') else None
            }
        }
    
    def _parse_dict_output(self, output: Dict) -> Dict[str, Any]:
        """解析字典格式的输出"""
        return {
            "raw_output": output,
            "device_recommendations": output.get("device_recommendations", {}),
            "control_strategy": output.get("strategy", {}),
            "monitoring_points": output.get("monitoring_points", {}),
            "multi_image_analysis": output.get("multi_image_analysis"),
            "overall_confidence_score": output.get("confidence_score", 0.0),
            "warnings": output.get("warnings", []),
            "errors": output.get("errors", []),
            "metadata_info": output.get("metadata", {})
        }
    
    def _extract_priority_recommendations(self, device_recommendations: Dict) -> Dict[str, List]:
        """从设备建议中提取优先级建议"""
        priority_recs = {
            "immediate": [],
            "within_hour": [],
            "within_day": [],
            "routine": []
        }
        
        for device_type, device_config in device_recommendations.items():
            for param_name, param_config in device_config.items():
                if isinstance(param_config, dict) and "urgency" in param_config:
                    urgency = param_config.get("urgency", "routine")
                    action_item = {
                        "device_type": device_type,
                        "parameter": param_name,
                        "action": param_config.get("action", "maintain"),
                        "current_value": param_config.get("current_value"),
                        "recommended_value": param_config.get("recommended_value"),
                        "reason": param_config.get("change_reason", "")
                    }
                    
                    if urgency in priority_recs:
                        priority_recs[urgency].append(action_item)
        
        return priority_recs
    
    def _extract_risk_assessment_summary(self, device_recommendations: Dict) -> Dict[str, Any]:
        """从设备建议中提取风险评估汇总"""
        risk_levels = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        critical_risks = []
        mitigation_strategies = []
        
        for device_type, device_config in device_recommendations.items():
            for param_name, param_config in device_config.items():
                if isinstance(param_config, dict) and "risk_assessment" in param_config:
                    risk_assessment = param_config.get("risk_assessment", {})
                    if risk_assessment:
                        adj_risk = risk_assessment.get("adjustment_risk", "low")
                        no_action_risk = risk_assessment.get("no_action_risk", "low")
                        
                        # 统计风险级别
                        if adj_risk in risk_levels:
                            risk_levels[adj_risk] += 1
                        if no_action_risk in risk_levels:
                            risk_levels[no_action_risk] += 1
                        
                        # 收集关键风险
                        if adj_risk in ["high", "critical"] or no_action_risk in ["high", "critical"]:
                            critical_risks.append({
                                "device_type": device_type,
                                "parameter": param_name,
                                "adjustment_risk": adj_risk,
                                "no_action_risk": no_action_risk,
                                "impact_scope": risk_assessment.get("impact_scope", "")
                            })
        
        # 计算整体风险级别
        if risk_levels["critical"] > 0:
            overall_risk = "critical"
        elif risk_levels["high"] > 0:
            overall_risk = "high"
        elif risk_levels["medium"] > 0:
            overall_risk = "medium"
        else:
            overall_risk = "low"
        
        return {
            "overall_risk": overall_risk,
            "risk_distribution": risk_levels,
            "critical_risks": critical_risks,
            "mitigation_strategies": mitigation_strategies
        }
    
    def _extract_parameter_confidence_scores(self, device_recommendations: Dict) -> Dict[str, Dict]:
        """从设备建议中提取参数置信度评分"""
        confidence_scores = {}
        
        for device_type, device_config in device_recommendations.items():
            device_scores = {}
            for param_name, param_config in device_config.items():
                if isinstance(param_config, dict):
                    # 基于优先级和紧急度计算置信度
                    priority = param_config.get("priority", "low")
                    urgency = param_config.get("urgency", "routine")
                    
                    # 简单的置信度计算逻辑
                    base_score = 0.7
                    if priority == "critical":
                        base_score = 0.95
                    elif priority == "high":
                        base_score = 0.85
                    elif priority == "medium":
                        base_score = 0.75
                    
                    if urgency == "immediate":
                        base_score = min(0.98, base_score + 0.1)
                    elif urgency == "within_hour":
                        base_score = min(0.95, base_score + 0.05)
                    
                    device_scores[param_name] = base_score
            
            if device_scores:
                confidence_scores[device_type] = device_scores
        
        return confidence_scores
    
    def get_inference_results(
        self,
        room_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        model_version: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        查询推理结果
        
        Args:
            room_id: 房间ID过滤
            start_time: 开始时间过滤
            end_time: 结束时间过滤
            model_version: 模型版本过滤
            status: 状态过滤
            limit: 结果数量限制
            
        Returns:
            List[Dict]: 查询结果列表
        """
        session = self.Session()
        
        try:
            query = session.query(ModelInferenceResult)
            
            # 应用过滤条件
            if room_id:
                query = query.filter(ModelInferenceResult.room_id == room_id)
            
            if start_time:
                query = query.filter(ModelInferenceResult.inference_time >= start_time)
            
            if end_time:
                query = query.filter(ModelInferenceResult.inference_time <= end_time)
            
            if model_version:
                query = query.filter(ModelInferenceResult.model_version == model_version)
            
            if status:
                query = query.filter(ModelInferenceResult.inference_status == status)
            
            # 按时间倒序排列
            query = query.order_by(ModelInferenceResult.inference_time.desc())
            
            # 限制结果数量
            results = query.limit(limit).all()
            
            # 转换为字典格式
            result_list = []
            for result in results:
                result_dict = {
                    "id": str(result.id),
                    "inference_time": result.inference_time.isoformat(),
                    "room_id": result.room_id,
                    "analysis_type": result.analysis_type,
                    "model_version": result.model_version,
                    "inference_status": result.inference_status,
                    "overall_confidence_score": result.overall_confidence_score,
                    "processing_time_seconds": result.processing_time_seconds,
                    "growth_day": result.growth_day,
                    "application_status": result.application_status,
                    "created_at": result.created_at.isoformat()
                }
                result_list.append(result_dict)
            
            logger.info(f"[ModelInferenceStorage] Retrieved {len(result_list)} inference results")
            
            return result_list
            
        except Exception as e:
            logger.error(f"[ModelInferenceStorage] Failed to query inference results: {e}")
            raise
        finally:
            session.close()
    
    def update_application_status(
        self,
        inference_id: str,
        status: str,
        applied_by: Optional[str] = None,
        feedback: Optional[Dict] = None
    ) -> bool:
        """
        更新推理结果的应用状态
        
        Args:
            inference_id: 推理结果ID
            status: 新状态 (applied, rejected, expired)
            applied_by: 操作员
            feedback: 效果反馈
            
        Returns:
            bool: 更新是否成功
        """
        session = self.Session()
        
        try:
            result = session.query(ModelInferenceResult).filter(
                ModelInferenceResult.id == inference_id
            ).first()
            
            if not result:
                logger.warning(f"[ModelInferenceStorage] Inference result not found: {inference_id}")
                return False
            
            result.application_status = status
            result.applied_by = applied_by
            
            if status == "applied":
                result.applied_at = datetime.utcnow()
            
            if feedback:
                result.effectiveness_feedback = feedback
            
            session.commit()
            
            logger.info(
                f"[ModelInferenceStorage] Updated application status: "
                f"ID={inference_id}, Status={status}"
            )
            
            return True
            
        except Exception as e:
            session.rollback()
            logger.error(f"[ModelInferenceStorage] Failed to update application status: {e}")
            return False
        finally:
            session.close()


def create_inference_tables():
    """
    创建模型推理结果相关表
    """
    try:
        # 创建表结构
        Base.metadata.create_all(bind=pgsql_engine, checkfirst=True)
        logger.info("[ModelInferenceStorage] Inference result tables created/verified successfully")
        
    except Exception as e:
        logger.error(f"[ModelInferenceStorage] Failed to create inference tables: {e}")
        raise


# 创建全局存储服务实例
inference_storage = InferenceResultStorage()


if __name__ == "__main__":
    # 创建表
    create_inference_tables()
    
    # 测试存储功能
    logger.info("Model inference storage tables created successfully")
"""
Decision Analysis Inference Persistence Module

This module provides integration between the decision analysis system and
the model inference results storage, enabling automatic persistence of
inference results to the database.
"""

from datetime import datetime
from typing import Dict, Optional, Any

from loguru import logger

from utils.model_inference_storage import inference_storage


class DecisionAnalysisInferencePersistence:
    """
    决策分析推理结果持久化服务
    
    负责将决策分析的推理结果自动存储到数据库中，
    包括输入参数、输出结果、置信度评估等信息。
    """
    
    def __init__(self, storage_service=None):
        """
        初始化持久化服务
        
        Args:
            storage_service: 存储服务实例，默认使用全局实例
        """
        self.storage = storage_service or inference_storage
        logger.info("[DecisionAnalysisInferencePersistence] Initialized persistence service")
    
    def persist_enhanced_decision_result(
        self,
        room_id: str,
        enhanced_decision_output: Any,
        analysis_datetime: datetime,
        input_parameters: Optional[Dict] = None,
        processing_time: float = 0.0,
        model_version: str = "enhanced_v1.0",
        created_by: Optional[str] = None
    ) -> str:
        """
        持久化增强决策分析结果
        
        Args:
            room_id: 房间ID
            enhanced_decision_output: 增强决策输出对象
            analysis_datetime: 分析时间
            input_parameters: 输入参数
            processing_time: 处理时间
            model_version: 模型版本
            created_by: 创建者
            
        Returns:
            str: 存储记录的ID
        """
        try:
            # 准备输入参数
            if input_parameters is None:
                input_parameters = {}
            
            # 添加分析时间到输入参数
            input_parameters.update({
                "analysis_datetime": analysis_datetime.isoformat(),
                "analysis_type": "enhanced_decision_analysis"
            })
            
            # 存储推理结果
            record_id = self.storage.store_inference_result(
                room_id=room_id,
                inference_output=enhanced_decision_output,
                model_version=model_version,
                analysis_type="enhanced_decision_analysis",
                input_params=input_parameters,
                processing_time=processing_time,
                created_by=created_by
            )
            
            logger.info(
                f"[DecisionAnalysisInferencePersistence] Successfully persisted enhanced decision result: "
                f"Room={room_id}, RecordID={record_id}"
            )
            
            return record_id
            
        except Exception as e:
            logger.error(
                f"[DecisionAnalysisInferencePersistence] Failed to persist enhanced decision result: "
                f"Room={room_id}, Error={e}"
            )
            raise
    
    def persist_basic_decision_result(
        self,
        room_id: str,
        decision_output: Any,
        analysis_datetime: datetime,
        input_parameters: Optional[Dict] = None,
        processing_time: float = 0.0,
        model_version: str = "basic_v1.0",
        created_by: Optional[str] = None
    ) -> str:
        """
        持久化基础决策分析结果
        
        Args:
            room_id: 房间ID
            decision_output: 决策输出对象
            analysis_datetime: 分析时间
            input_parameters: 输入参数
            processing_time: 处理时间
            model_version: 模型版本
            created_by: 创建者
            
        Returns:
            str: 存储记录的ID
        """
        try:
            # 准备输入参数
            if input_parameters is None:
                input_parameters = {}
            
            # 添加分析时间到输入参数
            input_parameters.update({
                "analysis_datetime": analysis_datetime.isoformat(),
                "analysis_type": "basic_decision_analysis"
            })
            
            # 存储推理结果
            record_id = self.storage.store_inference_result(
                room_id=room_id,
                inference_output=decision_output,
                model_version=model_version,
                analysis_type="basic_decision_analysis",
                input_params=input_parameters,
                processing_time=processing_time,
                created_by=created_by
            )
            
            logger.info(
                f"[DecisionAnalysisInferencePersistence] Successfully persisted basic decision result: "
                f"Room={room_id}, RecordID={record_id}"
            )
            
            return record_id
            
        except Exception as e:
            logger.error(
                f"[DecisionAnalysisInferencePersistence] Failed to persist basic decision result: "
                f"Room={room_id}, Error={e}"
            )
            raise
    
    def get_recent_inference_results(
        self,
        room_id: str,
        limit: int = 10,
        analysis_type: Optional[str] = None
    ) -> list:
        """
        获取最近的推理结果
        
        Args:
            room_id: 房间ID
            limit: 结果数量限制
            analysis_type: 分析类型过滤
            
        Returns:
            list: 推理结果列表
        """
        try:
            results = self.storage.get_inference_results(
                room_id=room_id,
                limit=limit
            )
            
            # 如果指定了分析类型，进行过滤
            if analysis_type:
                results = [r for r in results if r.get("analysis_type") == analysis_type]
            
            logger.info(
                f"[DecisionAnalysisInferencePersistence] Retrieved {len(results)} recent results for room {room_id}"
            )
            
            return results
            
        except Exception as e:
            logger.error(
                f"[DecisionAnalysisInferencePersistence] Failed to get recent results: "
                f"Room={room_id}, Error={e}"
            )
            return []
    
    def mark_result_as_applied(
        self,
        inference_id: str,
        applied_by: str,
        feedback: Optional[Dict] = None
    ) -> bool:
        """
        标记推理结果为已应用
        
        Args:
            inference_id: 推理结果ID
            applied_by: 应用操作员
            feedback: 效果反馈
            
        Returns:
            bool: 操作是否成功
        """
        try:
            success = self.storage.update_application_status(
                inference_id=inference_id,
                status="applied",
                applied_by=applied_by,
                feedback=feedback
            )
            
            if success:
                logger.info(
                    f"[DecisionAnalysisInferencePersistence] Marked result as applied: "
                    f"ID={inference_id}, AppliedBy={applied_by}"
                )
            
            return success
            
        except Exception as e:
            logger.error(
                f"[DecisionAnalysisInferencePersistence] Failed to mark result as applied: "
                f"ID={inference_id}, Error={e}"
            )
            return False
    
    def mark_result_as_rejected(
        self,
        inference_id: str,
        rejected_by: str,
        reason: Optional[str] = None
    ) -> bool:
        """
        标记推理结果为已拒绝
        
        Args:
            inference_id: 推理结果ID
            rejected_by: 拒绝操作员
            reason: 拒绝原因
            
        Returns:
            bool: 操作是否成功
        """
        try:
            feedback = {"rejection_reason": reason} if reason else None
            
            success = self.storage.update_application_status(
                inference_id=inference_id,
                status="rejected",
                applied_by=rejected_by,
                feedback=feedback
            )
            
            if success:
                logger.info(
                    f"[DecisionAnalysisInferencePersistence] Marked result as rejected: "
                    f"ID={inference_id}, RejectedBy={rejected_by}"
                )
            
            return success
            
        except Exception as e:
            logger.error(
                f"[DecisionAnalysisInferencePersistence] Failed to mark result as rejected: "
                f"ID={inference_id}, Error={e}"
            )
            return False


# 创建全局持久化服务实例
decision_persistence = DecisionAnalysisInferencePersistence()


def create_persistence_integration():
    """
    创建持久化集成，确保相关表已创建
    """
    try:
        from utils.model_inference_storage import create_inference_tables
        create_inference_tables()
        logger.info("[DecisionAnalysisInferencePersistence] Persistence integration created successfully")
        
    except Exception as e:
        logger.error(f"[DecisionAnalysisInferencePersistence] Failed to create persistence integration: {e}")
        raise


if __name__ == "__main__":
    # 测试持久化集成
    create_persistence_integration()
    logger.info("Decision analysis inference persistence integration ready")
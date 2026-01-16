"""
Verify Task 9 Performance Requirements

This script verifies:
1. Total processing time < 35 seconds (excluding LLM call)
2. Decision output format is correct
3. All components integrate properly
"""

import sys
import time
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger

# Configure logger
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level="INFO"
)


def verify_performance_metrics():
    """Verify performance requirements"""
    logger.info("=" * 80)
    logger.info("TASK 9 VERIFICATION: Performance Metrics")
    logger.info("=" * 80)
    
    try:
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        # Get template path
        template_path = Path(__file__).parent.parent / "src" / "configs" / "decision_prompt.jinja"
        
        # Initialize DecisionAnalyzer
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        
        # Test with a valid room
        room_id = "611"
        analysis_datetime = datetime.now()
        
        logger.info(f"Running performance test for room {room_id}")
        
        # Run analysis
        start_time = time.time()
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        total_time = time.time() - start_time
        
        # Calculate processing time excluding LLM
        processing_time_without_llm = result.metadata.total_processing_time - result.metadata.llm_response_time
        
        logger.info("")
        logger.info("Performance Metrics:")
        logger.info(f"  Total Processing Time: {result.metadata.total_processing_time:.2f}s")
        logger.info(f"  LLM Response Time: {result.metadata.llm_response_time:.2f}s")
        logger.info(f"  Processing Time (excluding LLM): {processing_time_without_llm:.2f}s")
        logger.info("")
        
        # Verify requirement: Total processing time < 35 seconds (excluding LLM)
        if processing_time_without_llm < 35:
            logger.success(f"✓ Performance requirement met: {processing_time_without_llm:.2f}s < 35s")
            performance_ok = True
        else:
            logger.error(f"✗ Performance requirement NOT met: {processing_time_without_llm:.2f}s >= 35s")
            performance_ok = False
        
        return performance_ok, result
        
    except Exception as e:
        logger.error(f"✗ Performance test failed: {e}")
        logger.exception(e)
        return False, None


def verify_output_format(result):
    """Verify decision output format"""
    logger.info("=" * 80)
    logger.info("TASK 9 VERIFICATION: Output Format")
    logger.info("=" * 80)
    
    try:
        # Check top-level structure
        assert hasattr(result, 'status'), "Missing 'status' field"
        assert hasattr(result, 'room_id'), "Missing 'room_id' field"
        assert hasattr(result, 'analysis_time'), "Missing 'analysis_time' field"
        assert hasattr(result, 'strategy'), "Missing 'strategy' field"
        assert hasattr(result, 'device_recommendations'), "Missing 'device_recommendations' field"
        assert hasattr(result, 'monitoring_points'), "Missing 'monitoring_points' field"
        assert hasattr(result, 'metadata'), "Missing 'metadata' field"
        
        logger.success("✓ Top-level structure is correct")
        
        # Check strategy structure
        assert hasattr(result.strategy, 'core_objective'), "Missing 'core_objective' in strategy"
        assert hasattr(result.strategy, 'priority_ranking'), "Missing 'priority_ranking' in strategy"
        assert hasattr(result.strategy, 'key_risk_points'), "Missing 'key_risk_points' in strategy"
        assert isinstance(result.strategy.priority_ranking, list), "priority_ranking should be a list"
        assert isinstance(result.strategy.key_risk_points, list), "key_risk_points should be a list"
        
        logger.success("✓ Strategy structure is correct")
        
        # Check device recommendations structure
        assert hasattr(result.device_recommendations, 'air_cooler'), "Missing 'air_cooler' in device_recommendations"
        assert hasattr(result.device_recommendations, 'fresh_air_fan'), "Missing 'fresh_air_fan' in device_recommendations"
        assert hasattr(result.device_recommendations, 'humidifier'), "Missing 'humidifier' in device_recommendations"
        assert hasattr(result.device_recommendations, 'grow_light'), "Missing 'grow_light' in device_recommendations"
        
        logger.success("✓ Device recommendations structure is correct")
        
        # Check air cooler parameters
        ac = result.device_recommendations.air_cooler
        assert hasattr(ac, 'tem_set'), "Missing 'tem_set' in air_cooler"
        assert hasattr(ac, 'tem_diff_set'), "Missing 'tem_diff_set' in air_cooler"
        assert hasattr(ac, 'cyc_on_off'), "Missing 'cyc_on_off' in air_cooler"
        assert hasattr(ac, 'cyc_on_time'), "Missing 'cyc_on_time' in air_cooler"
        assert hasattr(ac, 'cyc_off_time'), "Missing 'cyc_off_time' in air_cooler"
        assert hasattr(ac, 'ar_on_off'), "Missing 'ar_on_off' in air_cooler"
        assert hasattr(ac, 'hum_on_off'), "Missing 'hum_on_off' in air_cooler"
        assert hasattr(ac, 'rationale'), "Missing 'rationale' in air_cooler"
        assert isinstance(ac.rationale, list), "air_cooler rationale should be a list"
        
        logger.success("✓ Air cooler parameters are correct")
        
        # Check fresh air fan parameters
        faf = result.device_recommendations.fresh_air_fan
        assert hasattr(faf, 'model'), "Missing 'model' in fresh_air_fan"
        assert hasattr(faf, 'control'), "Missing 'control' in fresh_air_fan"
        assert hasattr(faf, 'co2_on'), "Missing 'co2_on' in fresh_air_fan"
        assert hasattr(faf, 'co2_off'), "Missing 'co2_off' in fresh_air_fan"
        assert hasattr(faf, 'on'), "Missing 'on' in fresh_air_fan"
        assert hasattr(faf, 'off'), "Missing 'off' in fresh_air_fan"
        assert hasattr(faf, 'rationale'), "Missing 'rationale' in fresh_air_fan"
        assert isinstance(faf.rationale, list), "fresh_air_fan rationale should be a list"
        
        logger.success("✓ Fresh air fan parameters are correct")
        
        # Check humidifier parameters
        hum = result.device_recommendations.humidifier
        assert hasattr(hum, 'model'), "Missing 'model' in humidifier"
        assert hasattr(hum, 'on'), "Missing 'on' in humidifier"
        assert hasattr(hum, 'off'), "Missing 'off' in humidifier"
        assert hasattr(hum, 'left_right_strategy'), "Missing 'left_right_strategy' in humidifier"
        assert hasattr(hum, 'rationale'), "Missing 'rationale' in humidifier"
        assert isinstance(hum.rationale, list), "humidifier rationale should be a list"
        
        logger.success("✓ Humidifier parameters are correct")
        
        # Check grow light parameters
        gl = result.device_recommendations.grow_light
        assert hasattr(gl, 'model'), "Missing 'model' in grow_light"
        assert hasattr(gl, 'on_mset'), "Missing 'on_mset' in grow_light"
        assert hasattr(gl, 'off_mset'), "Missing 'off_mset' in grow_light"
        assert hasattr(gl, 'on_off_1'), "Missing 'on_off_1' in grow_light"
        assert hasattr(gl, 'choose_1'), "Missing 'choose_1' in grow_light"
        assert hasattr(gl, 'on_off_2'), "Missing 'on_off_2' in grow_light"
        assert hasattr(gl, 'choose_2'), "Missing 'choose_2' in grow_light"
        assert hasattr(gl, 'on_off_3'), "Missing 'on_off_3' in grow_light"
        assert hasattr(gl, 'choose_3'), "Missing 'choose_3' in grow_light"
        assert hasattr(gl, 'on_off_4'), "Missing 'on_off_4' in grow_light"
        assert hasattr(gl, 'choose_4'), "Missing 'choose_4' in grow_light"
        assert hasattr(gl, 'rationale'), "Missing 'rationale' in grow_light"
        assert isinstance(gl.rationale, list), "grow_light rationale should be a list"
        
        logger.success("✓ Grow light parameters are correct")
        
        # Check monitoring points structure
        assert hasattr(result.monitoring_points, 'key_time_periods'), "Missing 'key_time_periods' in monitoring_points"
        assert hasattr(result.monitoring_points, 'warning_thresholds'), "Missing 'warning_thresholds' in monitoring_points"
        assert hasattr(result.monitoring_points, 'emergency_measures'), "Missing 'emergency_measures' in monitoring_points"
        assert isinstance(result.monitoring_points.key_time_periods, list), "key_time_periods should be a list"
        assert isinstance(result.monitoring_points.warning_thresholds, dict), "warning_thresholds should be a dict"
        assert isinstance(result.monitoring_points.emergency_measures, list), "emergency_measures should be a list"
        
        logger.success("✓ Monitoring points structure is correct")
        
        # Check metadata structure
        assert hasattr(result.metadata, 'data_sources'), "Missing 'data_sources' in metadata"
        assert hasattr(result.metadata, 'similar_cases_count'), "Missing 'similar_cases_count' in metadata"
        assert hasattr(result.metadata, 'avg_similarity_score'), "Missing 'avg_similarity_score' in metadata"
        assert hasattr(result.metadata, 'llm_model'), "Missing 'llm_model' in metadata"
        assert hasattr(result.metadata, 'llm_response_time'), "Missing 'llm_response_time' in metadata"
        assert hasattr(result.metadata, 'total_processing_time'), "Missing 'total_processing_time' in metadata"
        assert hasattr(result.metadata, 'warnings'), "Missing 'warnings' in metadata"
        assert hasattr(result.metadata, 'errors'), "Missing 'errors' in metadata"
        assert isinstance(result.metadata.data_sources, dict), "data_sources should be a dict"
        assert isinstance(result.metadata.warnings, list), "warnings should be a list"
        assert isinstance(result.metadata.errors, list), "errors should be a list"
        
        logger.success("✓ Metadata structure is correct")
        
        logger.success("✓ All output format checks passed")
        
        return True
        
    except AssertionError as e:
        logger.error(f"✗ Output format verification failed: {e}")
        return False
    except Exception as e:
        logger.error(f"✗ Output format verification error: {e}")
        logger.exception(e)
        return False


def verify_component_integration():
    """Verify all components are properly integrated"""
    logger.info("=" * 80)
    logger.info("TASK 9 VERIFICATION: Component Integration")
    logger.info("=" * 80)
    
    try:
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        from decision_analysis.data_extractor import DataExtractor
        from decision_analysis.clip_matcher import CLIPMatcher
        from decision_analysis.template_renderer import TemplateRenderer
        from decision_analysis.llm_client import LLMClient
        from decision_analysis.output_handler import OutputHandler
        
        logger.info("Checking component imports...")
        logger.success("✓ All components can be imported")
        
        # Get template path
        template_path = Path(__file__).parent.parent / "src" / "configs" / "decision_prompt.jinja"
        
        # Initialize DecisionAnalyzer
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        
        logger.info("Checking component initialization...")
        
        # Verify all components are initialized
        assert analyzer.data_extractor is not None, "DataExtractor not initialized"
        assert isinstance(analyzer.data_extractor, DataExtractor), "data_extractor is not a DataExtractor instance"
        logger.success("✓ DataExtractor initialized")
        
        assert analyzer.clip_matcher is not None, "CLIPMatcher not initialized"
        assert isinstance(analyzer.clip_matcher, CLIPMatcher), "clip_matcher is not a CLIPMatcher instance"
        logger.success("✓ CLIPMatcher initialized")
        
        assert analyzer.template_renderer is not None, "TemplateRenderer not initialized"
        assert isinstance(analyzer.template_renderer, TemplateRenderer), "template_renderer is not a TemplateRenderer instance"
        logger.success("✓ TemplateRenderer initialized")
        
        assert analyzer.llm_client is not None, "LLMClient not initialized"
        assert isinstance(analyzer.llm_client, LLMClient), "llm_client is not a LLMClient instance"
        logger.success("✓ LLMClient initialized")
        
        assert analyzer.output_handler is not None, "OutputHandler not initialized"
        assert isinstance(analyzer.output_handler, OutputHandler), "output_handler is not an OutputHandler instance"
        logger.success("✓ OutputHandler initialized")
        
        logger.success("✓ All components are properly integrated")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Component integration verification failed: {e}")
        logger.exception(e)
        return False


def main():
    """Run all Task 9 verifications"""
    logger.info("Starting Task 9 Verification...")
    logger.info("")
    
    results = []
    
    # Verification 1: Component Integration
    results.append(("Component Integration", verify_component_integration()))
    
    # Verification 2: Performance Metrics
    performance_ok, result = verify_performance_metrics()
    results.append(("Performance Metrics", performance_ok))
    
    # Verification 3: Output Format (only if we have a result)
    if result is not None:
        results.append(("Output Format", verify_output_format(result)))
    else:
        logger.warning("Skipping output format verification (no result available)")
        results.append(("Output Format", False))
    
    # Summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("TASK 9 VERIFICATION SUMMARY")
    logger.info("=" * 80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASSED" if result else "✗ FAILED"
        logger.info(f"{test_name}: {status}")
    
    logger.info("")
    logger.info(f"Total: {passed}/{total} verifications passed")
    
    if passed == total:
        logger.success("=" * 80)
        logger.success("TASK 9 CHECKPOINT: ALL VERIFICATIONS PASSED ✓")
        logger.success("=" * 80)
        logger.success("")
        logger.success("Summary:")
        logger.success("  ✓ All components integrate properly")
        logger.success("  ✓ Performance requirement met (< 35s excluding LLM)")
        logger.success("  ✓ Decision output format is correct")
        logger.success("")
        return 0
    else:
        logger.error("=" * 80)
        logger.error(f"TASK 9 CHECKPOINT: {total - passed} VERIFICATION(S) FAILED ✗")
        logger.error("=" * 80)
        return 1


if __name__ == "__main__":
    sys.exit(main())

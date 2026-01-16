"""
Test script for DecisionAnalyzer main controller

This script tests the complete decision analysis workflow including:
- Component initialization
- Data extraction
- CLIP matching
- Template rendering
- LLM calling
- Output validation

Usage:
    python scripts/test_decision_analyzer.py
"""

import sys
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


def test_decision_analyzer_initialization():
    """Test DecisionAnalyzer initialization"""
    logger.info("=" * 80)
    logger.info("TEST 1: DecisionAnalyzer Initialization")
    logger.info("=" * 80)
    
    try:
        from global_const.global_const import settings, static_settings, pgsql_engine
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        
        # Get template path
        template_path = Path(__file__).parent.parent / "src" / "configs" / "decision_prompt.jinja"
        
        if not template_path.exists():
            logger.error(f"Template file not found: {template_path}")
            return False
        
        # Initialize DecisionAnalyzer
        analyzer = DecisionAnalyzer(
            db_engine=pgsql_engine,
            settings=settings,
            static_config=static_settings,
            template_path=str(template_path)
        )
        
        # Verify all components are initialized
        assert analyzer.data_extractor is not None, "DataExtractor not initialized"
        assert analyzer.clip_matcher is not None, "CLIPMatcher not initialized"
        assert analyzer.template_renderer is not None, "TemplateRenderer not initialized"
        assert analyzer.llm_client is not None, "LLMClient not initialized"
        assert analyzer.output_handler is not None, "OutputHandler not initialized"
        
        logger.success("✓ DecisionAnalyzer initialized successfully")
        logger.success("✓ All components initialized")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Initialization failed: {e}")
        logger.exception(e)
        return False


def test_decision_analysis_workflow():
    """Test complete decision analysis workflow"""
    logger.info("=" * 80)
    logger.info("TEST 2: Complete Decision Analysis Workflow")
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
        
        # Test parameters
        room_id = "611"
        analysis_datetime = datetime.now()
        
        logger.info(f"Testing analysis for room {room_id} at {analysis_datetime}")
        
        # Run analysis
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        
        # Verify result structure
        assert result is not None, "Result is None"
        assert result.status in ["success", "error"], f"Invalid status: {result.status}"
        assert result.room_id == room_id, f"Room ID mismatch: {result.room_id} != {room_id}"
        assert result.strategy is not None, "Strategy is None"
        assert result.device_recommendations is not None, "Device recommendations is None"
        assert result.monitoring_points is not None, "Monitoring points is None"
        assert result.metadata is not None, "Metadata is None"
        
        # Log results
        logger.info(f"Analysis Status: {result.status}")
        logger.info(f"Core Objective: {result.strategy.core_objective}")
        logger.info(f"Data Sources: {result.metadata.data_sources}")
        logger.info(f"Similar Cases: {result.metadata.similar_cases_count}")
        logger.info(f"Avg Similarity: {result.metadata.avg_similarity_score:.2f}%")
        logger.info(f"LLM Response Time: {result.metadata.llm_response_time:.2f}s")
        logger.info(f"Total Processing Time: {result.metadata.total_processing_time:.2f}s")
        logger.info(f"Warnings: {len(result.metadata.warnings)}")
        logger.info(f"Errors: {len(result.metadata.errors)}")
        
        # Log warnings and errors
        if result.metadata.warnings:
            logger.warning("Warnings:")
            for warning in result.metadata.warnings:
                logger.warning(f"  - {warning}")
        
        if result.metadata.errors:
            logger.error("Errors:")
            for error in result.metadata.errors:
                logger.error(f"  - {error}")
        
        # Verify device recommendations
        logger.info("\nDevice Recommendations:")
        logger.info(f"  Air Cooler: temp_set={result.device_recommendations.air_cooler.tem_set}°C, "
                   f"temp_diff={result.device_recommendations.air_cooler.tem_diff_set}°C")
        logger.info(f"  Fresh Air Fan: mode={result.device_recommendations.fresh_air_fan.model}, "
                   f"CO2 on/off={result.device_recommendations.fresh_air_fan.co2_on}/{result.device_recommendations.fresh_air_fan.co2_off}ppm")
        logger.info(f"  Humidifier: mode={result.device_recommendations.humidifier.model}, "
                   f"on/off={result.device_recommendations.humidifier.on}/{result.device_recommendations.humidifier.off}%")
        logger.info(f"  Grow Light: mode={result.device_recommendations.grow_light.model}, "
                   f"on/off={result.device_recommendations.grow_light.on_mset}/{result.device_recommendations.grow_light.off_mset}min")
        
        logger.success("✓ Decision analysis workflow completed successfully")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Workflow test failed: {e}")
        logger.exception(e)
        return False


def test_error_handling():
    """Test error handling with invalid inputs"""
    logger.info("=" * 80)
    logger.info("TEST 3: Error Handling")
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
        
        # Test with non-existent room (should handle gracefully)
        room_id = "999"  # Non-existent room
        analysis_datetime = datetime.now()
        
        logger.info(f"Testing error handling with non-existent room {room_id}")
        
        # Run analysis (should not crash)
        result = analyzer.analyze(
            room_id=room_id,
            analysis_datetime=analysis_datetime
        )
        
        # Should return a result even with errors
        assert result is not None, "Result is None"
        assert result.room_id == room_id, f"Room ID mismatch"
        
        # Should have warnings or errors
        total_issues = len(result.metadata.warnings) + len(result.metadata.errors)
        assert total_issues > 0, "Expected warnings or errors for non-existent room"
        
        logger.info(f"Warnings: {len(result.metadata.warnings)}")
        logger.info(f"Errors: {len(result.metadata.errors)}")
        
        logger.success("✓ Error handling works correctly")
        
        return True
        
    except Exception as e:
        logger.error(f"✗ Error handling test failed: {e}")
        logger.exception(e)
        return False


def main():
    """Run all tests"""
    logger.info("Starting DecisionAnalyzer tests...")
    logger.info("")
    
    results = []
    
    # Test 1: Initialization
    results.append(("Initialization", test_decision_analyzer_initialization()))
    
    # Test 2: Complete workflow
    results.append(("Complete Workflow", test_decision_analysis_workflow()))
    
    # Test 3: Error handling
    results.append(("Error Handling", test_error_handling()))
    
    # Summary
    logger.info("")
    logger.info("=" * 80)
    logger.info("TEST SUMMARY")
    logger.info("=" * 80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✓ PASSED" if result else "✗ FAILED"
        logger.info(f"{test_name}: {status}")
    
    logger.info("")
    logger.info(f"Total: {passed}/{total} tests passed")
    
    if passed == total:
        logger.success("All tests passed! ✓")
        return 0
    else:
        logger.error(f"{total - passed} test(s) failed ✗")
        return 1


if __name__ == "__main__":
    sys.exit(main())

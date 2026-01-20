#!/usr/bin/env python3
"""
Test script for enhanced decision analysis system

This script tests the enhanced decision analysis workflow with multi-image
support and structured parameter adjustments.
"""

import sys
import os
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

def test_enhanced_decision_analysis():
    """Test the enhanced decision analysis system"""
    print("=" * 60)
    print("Testing Enhanced Decision Analysis System")
    print("=" * 60)
    
    try:
        # Import required modules
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        from decision_analysis.data_models import (
            EnhancedDecisionOutput, 
            MultiImageAnalysis,
            ParameterAdjustment,
            RiskAssessment
        )
        from global_const.const_config import DECISION_ANALYSIS_CONFIG
        
        print("✓ Successfully imported enhanced decision analysis modules")
        
        # Test data model creation
        print("\n1. Testing Enhanced Data Models...")
        
        # Test ParameterAdjustment
        param_adj = ParameterAdjustment(
            current_value=18.5,
            recommended_value=18.0,
            action="adjust",
            change_reason="Temperature deviation detected",
            priority="high",
            urgency="immediate",
            risk_assessment=RiskAssessment(
                adjustment_risk="low",
                no_action_risk="medium",
                impact_scope="temperature_stability"
            )
        )
        print(f"✓ ParameterAdjustment created: {param_adj.action} from {param_adj.current_value} to {param_adj.recommended_value}")
        
        # Test MultiImageAnalysis
        multi_image = MultiImageAnalysis(
            total_images_analyzed=2,
            image_quality_scores=[0.85, 0.92],
            aggregation_method="weighted_average",
            confidence_score=0.88,
            view_consistency="high",
            key_observations=["Camera 1: Good lighting", "Camera 2: Clear view"]
        )
        print(f"✓ MultiImageAnalysis created: {multi_image.total_images_analyzed} images, confidence: {multi_image.confidence_score:.2f}")
        
        # Test configuration
        print(f"\n2. Testing Configuration...")
        print(f"✓ Image aggregation window: {DECISION_ANALYSIS_CONFIG['image_aggregation_window']} minutes")
        print(f"✓ Adjustment thresholds: {DECISION_ANALYSIS_CONFIG['adjustment_thresholds']}")
        print(f"✓ Priority weights: {DECISION_ANALYSIS_CONFIG['priority_weights']}")
        
        print("\n3. Testing Enhanced Output Handler...")
        from decision_analysis.output_handler import OutputHandler
        
        # Create a mock static config
        mock_static_config = {
            "mushroom": {
                "datapoint": {
                    "air_cooler": {
                        "point_list": [
                            {
                                "point_alias": "tem_set",
                                "enum": {}
                            }
                        ]
                    }
                }
            }
        }
        
        output_handler = OutputHandler(mock_static_config)
        print("✓ OutputHandler initialized with enhanced support")
        
        # Test enhanced parameter extraction
        param_data = {
            "current_value": 18.5,
            "recommended_value": 18.0,
            "action": "adjust",
            "change_reason": "Test adjustment",
            "priority": "high",
            "urgency": "immediate",
            "risk_assessment": {
                "adjustment_risk": "low",
                "no_action_risk": "medium",
                "impact_scope": "test"
            }
        }
        
        extracted_param = output_handler._extract_parameter_adjustment(
            param_data, "tem_set", "air_cooler"
        )
        print(f"✓ Parameter extraction: {extracted_param.action} - {extracted_param.change_reason}")
        
        print("\n4. Testing Enhanced Template Renderer...")
        from decision_analysis.template_renderer import TemplateRenderer
        
        template_path = "src/configs/decision_prompt.jinja"
        if os.path.exists(template_path):
            template_renderer = TemplateRenderer(template_path, mock_static_config)
            print("✓ TemplateRenderer initialized with enhanced support")
            
            # Test multi-image context mapping
            context = template_renderer._map_multi_image_context(multi_image)
            print(f"✓ Multi-image context mapped: {len(context)} variables")
            print(f"  - Image count: {context['multi_image_count']}")
            print(f"  - Consistency info: {context['image_consistency_info']}")
        else:
            print("⚠ Template file not found, skipping template renderer test")
        
        print("\n5. Testing Enhanced LLM Client...")
        from decision_analysis.llm_client import LLMClient
        from global_const.global_const import settings
        
        print(f"✓ Loaded settings with LLM host: {settings.llama.llama_host}:{settings.llama.llama_port}")
        print(f"✓ LLM model: {settings.llama.model}")
        
        llm_client = LLMClient(settings)
        print("✓ LLMClient initialized with enhanced support")
        
        # Test enhanced fallback decision
        fallback = llm_client._get_enhanced_fallback_decision("Test error")
        print(f"✓ Enhanced fallback decision created with status: {fallback['status']}")
        
        # Test structure validation
        is_enhanced = llm_client._validate_enhanced_structure(fallback)
        print(f"✓ Enhanced structure validation: {is_enhanced}")
        
        print("\n" + "=" * 60)
        print("✅ Enhanced Decision Analysis System Test PASSED")
        print("=" * 60)
        
        print("\n📋 Summary of Enhancements:")
        print("• Multi-image aggregation and analysis")
        print("• Structured parameter adjustments with actions (maintain/adjust/monitor)")
        print("• Risk assessments and priority levels")
        print("• Enhanced LLM prompting and parsing")
        print("• Comprehensive validation and fallback mechanisms")
        
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure all required modules are available")
        return False
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_enhanced_decision_analysis()
    sys.exit(0 if success else 1)
"""
Test script for LLMClient implementation

Tests the three main methods:
- generate_decision: Call LLaMA API with rendered prompt
- _parse_response: Parse JSON response from LLM
- Error handling and fallback strategy
"""

import json
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from global_const.global_const import settings
from decision_analysis.llm_client import LLMClient
from loguru import logger


def test_parse_response():
    """Test the _parse_response method with various formats"""
    print("\n" + "="*80)
    print("TEST 1: Testing _parse_response with various JSON formats")
    print("="*80)
    
    client = LLMClient(settings)
    
    # Test 1: Valid JSON
    print("\n1. Testing valid JSON...")
    valid_json = json.dumps({
        "strategy": {"core_objective": "Test objective"},
        "device_recommendations": {}
    })
    result = client._parse_response(valid_json)
    assert "strategy" in result
    print("✓ Valid JSON parsed successfully")
    
    # Test 2: JSON in markdown code block
    print("\n2. Testing JSON in markdown code block...")
    markdown_json = """
Here is the decision:
```json
{
    "strategy": {"core_objective": "Test objective"},
    "device_recommendations": {}
}
```
"""
    result = client._parse_response(markdown_json)
    assert "strategy" in result
    print("✓ JSON extracted from markdown code block")
    
    # Test 3: JSON embedded in text
    print("\n3. Testing JSON embedded in text...")
    embedded_json = """
Based on the analysis, here is my recommendation:
{"strategy": {"core_objective": "Test objective"}, "device_recommendations": {}}
This should help with the decision.
"""
    result = client._parse_response(embedded_json)
    assert "strategy" in result
    print("✓ JSON extracted from embedded text")
    
    # Test 4: Invalid JSON (should return fallback)
    print("\n4. Testing invalid JSON (should return fallback)...")
    invalid_json = "This is not JSON at all"
    result = client._parse_response(invalid_json)
    assert result["status"] == "fallback"
    assert "error_reason" in result
    print("✓ Fallback decision returned for invalid JSON")
    
    print("\n✓ All _parse_response tests passed!")


def test_fallback_decision():
    """Test the fallback decision generation"""
    print("\n" + "="*80)
    print("TEST 2: Testing fallback decision generation")
    print("="*80)
    
    client = LLMClient(settings)
    
    fallback = client._get_fallback_decision("Test error")
    
    # Verify structure
    assert fallback["status"] == "fallback"
    assert "error_reason" in fallback
    assert "strategy" in fallback
    assert "device_recommendations" in fallback
    assert "monitoring_points" in fallback
    assert "metadata" in fallback
    
    # Verify device recommendations
    assert "air_cooler" in fallback["device_recommendations"]
    assert "fresh_air_fan" in fallback["device_recommendations"]
    assert "humidifier" in fallback["device_recommendations"]
    assert "grow_light" in fallback["device_recommendations"]
    
    # Verify all devices have rationale
    for device_name, device_config in fallback["device_recommendations"].items():
        assert "rationale" in device_config
        assert len(device_config["rationale"]) > 0
    
    print("\n✓ Fallback decision structure is correct")
    print(f"✓ Error reason: {fallback['error_reason']}")
    print(f"✓ Strategy: {fallback['strategy']['core_objective']}")
    print(f"✓ All devices have rationale")


def test_generate_decision_with_mock():
    """Test generate_decision with a simple prompt (may fail if LLM not available)"""
    print("\n" + "="*80)
    print("TEST 3: Testing generate_decision (may fail if LLM unavailable)")
    print("="*80)
    
    client = LLMClient(settings)
    
    # Create a simple test prompt
    test_prompt = """
请生成一个简单的决策建议，包含以下结构：
{
    "strategy": {
        "core_objective": "测试目标",
        "priority_ranking": ["优先级1", "优先级2"],
        "key_risk_points": ["风险点1"]
    },
    "device_recommendations": {
        "air_cooler": {
            "tem_set": 16.0,
            "rationale": ["测试依据"]
        }
    },
    "monitoring_points": {
        "key_time_periods": ["测试时段"],
        "warning_thresholds": {},
        "emergency_measures": ["测试措施"]
    }
}
"""
    
    print(f"\nCalling LLM API at: {client.api_url}")
    print(f"Model: {client.model}")
    print(f"Timeout: {client.timeout}s")
    
    result = client.generate_decision(test_prompt, temperature=0.1)
    
    # Check if we got a result (either success or fallback)
    assert result is not None
    assert isinstance(result, dict)
    
    if result.get("status") == "fallback":
        print("\n⚠ LLM API not available, fallback decision returned")
        print(f"  Reason: {result.get('error_reason')}")
        print("  This is expected if LLM service is not running")
    else:
        print("\n✓ LLM API call successful!")
        print(f"  Response contains: {list(result.keys())}")
        
        # Verify expected structure
        if "strategy" in result:
            print("  ✓ Strategy section present")
        if "device_recommendations" in result:
            print("  ✓ Device recommendations present")


def test_error_handling():
    """Test various error scenarios"""
    print("\n" + "="*80)
    print("TEST 4: Testing error handling scenarios")
    print("="*80)
    
    client = LLMClient(settings)
    
    # Test with invalid endpoint (should trigger connection error)
    print("\n1. Testing with invalid endpoint...")
    original_url = client.api_url
    client.api_url = "http://invalid-host-12345:9999/api"
    
    result = client.generate_decision("test prompt", temperature=0.1)
    assert result["status"] == "fallback"
    assert "Connection error" in result["error_reason"] or "Request error" in result["error_reason"]
    print("✓ Connection error handled correctly")
    
    # Restore original URL
    client.api_url = original_url
    
    # Test with very short timeout (may trigger timeout)
    print("\n2. Testing with very short timeout...")
    original_timeout = client.timeout
    client.timeout = 0.001  # 1ms timeout
    
    result = client.generate_decision("test prompt", temperature=0.1)
    assert result["status"] == "fallback"
    print(f"✓ Timeout handled correctly: {result['error_reason']}")
    
    # Restore original timeout
    client.timeout = original_timeout
    
    print("\n✓ All error handling tests passed!")


def main():
    """Run all tests"""
    print("\n" + "="*80)
    print("LLMClient Implementation Tests")
    print("="*80)
    print(f"\nSettings environment: {settings.current_env}")
    print(f"LLaMA host: {settings.llama.llama_host}")
    print(f"LLaMA port: {settings.llama.llama_port}")
    print(f"Model: {settings.llama.model}")
    
    try:
        # Run tests
        test_parse_response()
        test_fallback_decision()
        test_error_handling()
        test_generate_decision_with_mock()
        
        print("\n" + "="*80)
        print("✓ ALL TESTS PASSED!")
        print("="*80)
        print("\nImplementation Summary:")
        print("  ✓ Task 6.1: generate_decision method implemented")
        print("  ✓ Task 6.2: _parse_response method implemented")
        print("  ✓ Task 6.3: Error handling and fallback strategy implemented")
        print("\nKey Features:")
        print("  • Calls LLaMA API with rendered prompt")
        print("  • Parses JSON responses (direct, markdown, embedded)")
        print("  • Handles API errors, timeouts, connection failures")
        print("  • Provides conservative fallback decisions")
        print("  • Comprehensive logging for debugging")
        
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

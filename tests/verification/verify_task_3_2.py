#!/usr/bin/env python3
"""
Verification script for Task 3.2: 相似度分数计算和置信度标记

This script demonstrates and verifies:
1. Distance to similarity conversion (0-100%)
2. Confidence level marking (low/medium/high)
3. Edge cases and boundary conditions

Requirements: 4.4, 4.6
"""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from decision_analysis.clip_matcher import CLIPMatcher
from unittest.mock import Mock


def test_distance_to_similarity():
    """Test distance to similarity conversion"""
    print("=" * 80)
    print("Testing Distance to Similarity Conversion")
    print("=" * 80)
    
    matcher = CLIPMatcher(Mock())
    
    test_cases = [
        (0.0, 100.0, "Identical vectors"),
        (0.5, 56.25, "Close vectors"),
        (1.0, 25.0, "Moderate distance"),
        (1.5, 6.25, "Large distance"),
        (2.0, 0.0, "Opposite vectors"),
        (-1.0, 100.0, "Negative distance (clamped to 0)"),
        (5.0, 0.0, "Very large distance (clamped to 2.0)"),
    ]
    
    print("\nDistance -> Similarity Conversion:")
    print(f"{'Distance':<12} {'Expected':<12} {'Actual':<12} {'Status':<10} {'Description'}")
    print("-" * 80)
    
    all_passed = True
    for distance, expected, description in test_cases:
        actual = matcher._distance_to_similarity(distance)
        # Allow small tolerance for floating point comparison
        passed = abs(actual - expected) < 0.5
        status = "✓ PASS" if passed else "✗ FAIL"
        all_passed = all_passed and passed
        
        print(f"{distance:<12.2f} {expected:<12.2f} {actual:<12.2f} {status:<10} {description}")
    
    print("\n" + ("✓ All distance conversion tests passed!" if all_passed else "✗ Some tests failed!"))
    return all_passed


def test_confidence_level():
    """Test confidence level calculation"""
    print("\n" + "=" * 80)
    print("Testing Confidence Level Marking")
    print("=" * 80)
    
    matcher = CLIPMatcher(Mock())
    
    test_cases = [
        (100.0, "high", "Perfect match"),
        (80.0, "high", "High similarity"),
        (61.0, "high", "Just above high threshold"),
        (60.0, "medium", "At medium-high boundary"),
        (40.0, "medium", "Medium similarity"),
        (20.0, "medium", "At medium-low boundary"),
        (19.9, "low", "Just below low threshold"),
        (10.0, "low", "Low similarity"),
        (0.0, "low", "No similarity"),
    ]
    
    print("\nSimilarity Score -> Confidence Level:")
    print(f"{'Similarity':<15} {'Expected':<12} {'Actual':<12} {'Status':<10} {'Description'}")
    print("-" * 80)
    
    all_passed = True
    for similarity, expected, description in test_cases:
        actual = matcher._calculate_confidence_level(similarity)
        passed = actual == expected
        status = "✓ PASS" if passed else "✗ FAIL"
        all_passed = all_passed and passed
        
        print(f"{similarity:<15.1f} {expected:<12} {actual:<12} {status:<10} {description}")
    
    print("\n" + ("✓ All confidence level tests passed!" if all_passed else "✗ Some tests failed!"))
    return all_passed


def test_requirements_compliance():
    """Test compliance with requirements 4.4 and 4.6"""
    print("\n" + "=" * 80)
    print("Testing Requirements Compliance")
    print("=" * 80)
    
    matcher = CLIPMatcher(Mock())
    
    print("\nRequirement 4.4: Similarity score range (0-100%)")
    print("-" * 80)
    
    # Test various distances to ensure similarity is always in 0-100 range
    test_distances = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, -1.0, 5.0]
    all_in_range = True
    
    for distance in test_distances:
        similarity = matcher._distance_to_similarity(distance)
        in_range = 0.0 <= similarity <= 100.0
        status = "✓" if in_range else "✗"
        all_in_range = all_in_range and in_range
        print(f"  {status} Distance {distance:>5.2f} -> Similarity {similarity:>6.2f}% (in range: {in_range})")
    
    print(f"\n{'✓ PASS' if all_in_range else '✗ FAIL'}: All similarity scores in 0-100% range")
    
    print("\nRequirement 4.6: Confidence level thresholds")
    print("-" * 80)
    print("  - Low confidence: similarity < 20%")
    print("  - Medium confidence: 20% ≤ similarity ≤ 60%")
    print("  - High confidence: similarity > 60%")
    
    # Test boundary conditions
    boundary_tests = [
        (19.9, "low", "Just below 20%"),
        (20.0, "medium", "At 20% boundary"),
        (60.0, "medium", "At 60% boundary"),
        (60.1, "high", "Just above 60%"),
    ]
    
    all_correct = True
    for similarity, expected, description in boundary_tests:
        actual = matcher._calculate_confidence_level(similarity)
        correct = actual == expected
        status = "✓" if correct else "✗"
        all_correct = all_correct and correct
        print(f"  {status} {similarity:>5.1f}% -> {actual:<8} (expected: {expected:<8}) - {description}")
    
    print(f"\n{'✓ PASS' if all_correct else '✗ FAIL'}: All confidence level thresholds correct")
    
    return all_in_range and all_correct


def main():
    """Run all verification tests"""
    print("\n" + "=" * 80)
    print("Task 3.2 Verification: 相似度分数计算和置信度标记")
    print("=" * 80)
    print("\nThis script verifies the implementation of:")
    print("  1. _distance_to_similarity() method")
    print("  2. _calculate_confidence_level() method")
    print("\nRequirements:")
    print("  - 4.4: Calculate and return similarity score (0-100%)")
    print("  - 4.6: Mark low confidence when similarity < 20%")
    print("=" * 80)
    
    # Run all tests
    test1_passed = test_distance_to_similarity()
    test2_passed = test_confidence_level()
    test3_passed = test_requirements_compliance()
    
    # Summary
    print("\n" + "=" * 80)
    print("VERIFICATION SUMMARY")
    print("=" * 80)
    print(f"Distance to Similarity Conversion: {'✓ PASS' if test1_passed else '✗ FAIL'}")
    print(f"Confidence Level Marking:          {'✓ PASS' if test2_passed else '✗ FAIL'}")
    print(f"Requirements Compliance:           {'✓ PASS' if test3_passed else '✗ FAIL'}")
    print("=" * 80)
    
    if test1_passed and test2_passed and test3_passed:
        print("\n✓ Task 3.2 implementation is COMPLETE and VERIFIED!")
        print("\nImplementation details:")
        print("  - _distance_to_similarity(): Converts L2 distance (0-2) to similarity (0-100%)")
        print("    Formula: similarity = 100 * (1 - distance/2)^2")
        print("  - _calculate_confidence_level(): Maps similarity to confidence levels")
        print("    - high: > 60%")
        print("    - medium: 20-60%")
        print("    - low: < 20%")
        return 0
    else:
        print("\n✗ Some tests failed. Please review the implementation.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

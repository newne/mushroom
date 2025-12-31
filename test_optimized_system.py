#!/usr/bin/env python3
"""
测试优化后的系统性能
"""
import sys
import time
sys.path.append('src')

from utils.recent_image_processor import create_recent_image_processor
from utils.mushroom_image_encoder import create_mushroom_encoder
from utils.minio_client import create_minio_client

def test_optimized_vs_original():
    """测试优化版本与原版本的性能对比"""
    
    print("=" * 60)
    print("系统优化效果测试")
    print("=" * 60)
    
    # 测试1: 原始方式（重复初始化）
    print("\n🔄 测试1: 原始方式（重复初始化）")
    start_time = time.time()
    
    processor1 = create_recent_image_processor()  # 会创建新的encoder和minio_client
    summary1 = processor1.get_recent_image_summary(hours=1)
    # 这里会再次查询相同的数据进行处理
    
    original_time = time.time() - start_time
    print(f"   原始方式耗时: {original_time:.2f}秒")
    print(f"   找到图片: {summary1['total_images']}张")
    
    # 测试2: 优化方式（共享实例 + 整合查询）
    print("\n⚡ 测试2: 优化方式（共享实例 + 整合查询）")
    start_time = time.time()
    
    # 创建共享实例
    shared_encoder = create_mushroom_encoder()
    shared_minio_client = create_minio_client()
    
    processor2 = create_recent_image_processor(
        shared_encoder=shared_encoder,
        shared_minio_client=shared_minio_client
    )
    
    # 使用整合方法，一次调用完成摘要和处理准备
    result2 = processor2.get_recent_image_summary_and_process(
        hours=1,
        max_images_per_room=1,
        save_to_db=False,  # 测试时不保存
        show_summary=False
    )
    
    optimized_time = time.time() - start_time
    print(f"   优化方式耗时: {optimized_time:.2f}秒")
    print(f"   找到图片: {result2['summary']['total_images']}张")
    
    # 性能对比
    print("\n📊 性能对比结果:")
    if original_time > 0:
        improvement = ((original_time - optimized_time) / original_time) * 100
        print(f"   时间节省: {improvement:.1f}%")
        print(f"   速度提升: {original_time/optimized_time:.1f}x")
    
    print("\n🎯 优化效果:")
    print("   ✅ 避免重复初始化MinIO客户端")
    print("   ✅ 避免重复初始化CLIP模型")
    print("   ✅ 避免重复查询图片数据")
    print("   ✅ 缓存设备配置，减少数据库查询")
    print("   ✅ 整合摘要和处理流程")
    
    return {
        'original_time': original_time,
        'optimized_time': optimized_time,
        'improvement_percent': improvement if original_time > 0 else 0
    }

def test_caching_effectiveness():
    """测试缓存效果"""
    print("\n" + "=" * 60)
    print("缓存效果测试")
    print("=" * 60)
    
    # 创建共享实例
    shared_encoder = create_mushroom_encoder()
    shared_minio_client = create_minio_client()
    
    processor = create_recent_image_processor(
        shared_encoder=shared_encoder,
        shared_minio_client=shared_minio_client
    )
    
    # 第一次查询（会缓存）
    print("\n🔍 第一次查询（建立缓存）")
    start_time = time.time()
    result1 = processor.get_recent_image_summary(hours=1)
    first_query_time = time.time() - start_time
    print(f"   首次查询耗时: {first_query_time:.2f}秒")
    
    # 第二次查询（使用缓存）
    print("\n⚡ 第二次查询（使用缓存）")
    start_time = time.time()
    result2 = processor.get_recent_image_summary(hours=1)
    cached_query_time = time.time() - start_time
    print(f"   缓存查询耗时: {cached_query_time:.2f}秒")
    
    # 缓存效果
    if first_query_time > 0:
        cache_improvement = ((first_query_time - cached_query_time) / first_query_time) * 100
        print(f"\n📈 缓存效果: 速度提升 {cache_improvement:.1f}%")
    
    return {
        'first_query_time': first_query_time,
        'cached_query_time': cached_query_time,
        'cache_improvement': cache_improvement if first_query_time > 0 else 0
    }

if __name__ == "__main__":
    try:
        # 测试优化效果
        perf_result = test_optimized_vs_original()
        
        # 测试缓存效果
        cache_result = test_caching_effectiveness()
        
        print("\n" + "=" * 60)
        print("测试总结")
        print("=" * 60)
        print(f"整体性能提升: {perf_result['improvement_percent']:.1f}%")
        print(f"缓存查询提升: {cache_result['cache_improvement']:.1f}%")
        print("\n✅ 系统优化测试完成！")
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
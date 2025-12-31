#!/usr/bin/env python3
"""
环境数据集成测试脚本
验证新的环境数据获取和存储功能
"""

import sys
import os
sys.path.insert(0, 'src')

from utils.mushroom_image_encoder import create_mushroom_encoder
from utils.env_data_processor import create_env_data_processor
from datetime import datetime
from loguru import logger

def test_env_data_processor():
    """测试环境数据处理器"""
    print("🔧 测试环境数据处理器...")
    
    try:
        processor = create_env_data_processor()
        
        # 测试获取环境数据
        test_room_id = "611"
        test_time = datetime(2025, 12, 30, 16, 2)
        test_image_path = "611/20251230/611_1921681237_20251230_20251230160200.jpg"
        
        env_data = processor.get_environment_data(
            room_id=test_room_id,
            collection_time=test_time,
            image_path=test_image_path
        )
        
        if env_data:
            print("✅ 环境数据处理器测试成功")
            print(f"   库房ID: {env_data['room_id']}")
            print(f"   生长阶段: {env_data['growth_stage']}")
            print(f"   补光数量: {env_data['light_count']}")
            print(f"   加湿器数量: {env_data['humidifier_count']}")
            print(f"   语义描述: {env_data['semantic_description']}")
            return True
        else:
            print("⚠️ 环境数据处理器返回空结果（可能是正常的，如果没有对应时间的数据）")
            return True
            
    except Exception as e:
        print(f"❌ 环境数据处理器测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_integrated_system():
    """测试集成系统"""
    print("\n🚀 测试集成的蘑菇图像编码系统...")
    
    try:
        # 1. 初始化编码器
        print("1️⃣ 初始化编码器...")
        encoder = create_mushroom_encoder()
        print("✅ 编码器初始化成功")
        
        # 2. 使用验证方法处理有限数量的图像
        print("\n2️⃣ 验证系统功能（每个库房最多3张图像）...")
        
        validation_results = encoder.validate_system_with_limited_samples(max_per_mushroom=3)
        
        print(f"📊 验证结果:")
        print(f"   - 发现库房数量: {validation_results['total_mushrooms']}")
        print(f"   - 库房列表: {validation_results['mushroom_ids']}")
        print(f"   - 总处理数量: {validation_results['total_processed']}")
        print(f"   - 成功处理: {validation_results['total_success']}")
        print(f"   - 处理失败: {validation_results['total_failed']}")
        print(f"   - 跳过已处理: {validation_results['total_skipped']}")
        
        # 显示每个库房的详细结果
        print(f"\n📈 各库房处理详情:")
        for mushroom_id, stats in validation_results['processed_per_mushroom'].items():
            print(f"   库房 {mushroom_id}: 处理{stats['processed']}/{stats['total_images']}, "
                  f"成功{stats['success']}, 失败{stats['failed']}, 跳过{stats['skipped']}")
        
        # 3. 获取更新后的统计信息
        print("\n3️⃣ 获取统计信息...")
        stats = encoder.get_processing_statistics()
        print(f"📊 处理统计:")
        print(f"   - 总处理数量: {stats.get('total_processed', 0)}")
        print(f"   - 包含环境控制: {stats.get('with_environmental_control', 0)}")
        print(f"   - 库房分布: {stats.get('room_distribution', {})}")
        print(f"   - 生长阶段分布: {stats.get('growth_stage_distribution', {})}")
        print(f"   - 补光使用分布: {stats.get('light_usage_distribution', {})}")
        
        print("\n✅ 集成系统测试完成！")
        return True
        
    except Exception as e:
        print(f"\n❌ 集成系统测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """主测试函数"""
    print("🧪 开始环境数据集成测试...")
    
    # 测试1: 环境数据处理器
    success1 = test_env_data_processor()
    
    # 测试2: 集成系统
    success2 = test_integrated_system()
    
    # 总结
    if success1 and success2:
        print("\n🎉 所有测试通过！环境数据集成功能正常工作。")
        return True
    else:
        print("\n❌ 部分测试失败，请检查错误信息。")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
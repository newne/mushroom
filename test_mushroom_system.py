#!/usr/bin/env python3
"""
蘑菇图像处理系统测试脚本
验证整个系统的功能是否正常工作
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# 添加项目路径
project_root = Path(__file__).parent
sys.path.append(str(project_root))

from src.utils.mushroom_image_processor import create_mushroom_processor, MushroomImagePathParser
from src.utils.minio_service import create_minio_service
from loguru import logger


def test_path_parsing():
    """测试路径解析功能"""
    print("=" * 60)
    print("测试路径解析功能")
    print("=" * 60)
    
    parser = MushroomImagePathParser()
    
    # 测试用例
    test_cases = [
        {
            "path": "mogu/612/20251224/612_1921681235_20251218_20251224160000.jpg",
            "expected": {
                "mushroom_id": "612",
                "collection_ip": "1921681235",
                "collection_date": "20251218",
                "detailed_time": "20251224160000"
            }
        },
        {
            "path": "mogu/613/20251225/613_1921681236_20251219_20251225090000.jpg",
            "expected": {
                "mushroom_id": "613",
                "collection_ip": "1921681236",
                "collection_date": "20251219",
                "detailed_time": "20251225090000"
            }
        }
    ]
    
    all_passed = True
    
    for i, test_case in enumerate(test_cases):
        path = test_case["path"]
        expected = test_case["expected"]
        
        print(f"测试用例 {i+1}: {path}")
        
        # 测试完整路径解析
        image_info = parser.parse_path(path)
        
        if image_info:
            # 验证解析结果
            checks = [
                ("蘑菇库号", image_info.mushroom_id, expected["mushroom_id"]),
                ("采集IP", image_info.collection_ip, expected["collection_ip"]),
                ("采集日期", image_info.collection_date, expected["collection_date"]),
                ("详细时间", image_info.detailed_time, expected["detailed_time"])
            ]
            
            case_passed = True
            for check_name, actual, expected_val in checks:
                if actual == expected_val:
                    print(f"  ✅ {check_name}: {actual}")
                else:
                    print(f"  ❌ {check_name}: 期望 {expected_val}, 实际 {actual}")
                    case_passed = False
            
            if case_passed:
                print(f"  ✅ 测试用例 {i+1} 通过")
            else:
                print(f"  ❌ 测试用例 {i+1} 失败")
                all_passed = False
        else:
            print(f"  ❌ 路径解析失败")
            all_passed = False
        
        print("-" * 40)
    
    return all_passed


def test_minio_connection():
    """测试MinIO连接"""
    print("=" * 60)
    print("测试MinIO连接")
    print("=" * 60)
    
    try:
        service = create_minio_service()
        
        # 健康检查
        health = service.health_check()
        
        print(f"环境: {health['environment']}")
        print(f"端点: {health['endpoint']}")
        print(f"存储桶: {health['bucket']}")
        print(f"连接状态: {'✅' if health['connection'] else '❌'}")
        print(f"存储桶状态: {'✅' if health['bucket_exists'] else '❌'}")
        print(f"图片数量: {health['image_count']}")
        
        if health['errors']:
            print("错误信息:")
            for error in health['errors']:
                print(f"  - {error}")
        
        return health['healthy']
        
    except Exception as e:
        print(f"❌ MinIO连接测试失败: {e}")
        return False


def test_database_connection():
    """测试数据库连接"""
    print("=" * 60)
    print("测试数据库连接")
    print("=" * 60)
    
    try:
        processor = create_mushroom_processor()
        
        # 尝试获取统计信息
        stats = processor.get_processing_statistics()
        
        print(f"✅ 数据库连接成功")
        print(f"已处理图片数: {stats.get('total_processed', 0)}")
        
        mushroom_dist = stats.get('mushroom_distribution', {})
        if mushroom_dist:
            print("蘑菇库号分布:")
            for mushroom_id, count in mushroom_dist.items():
                print(f"  库号 {mushroom_id}: {count} 张")
        
        return True
        
    except Exception as e:
        print(f"❌ 数据库连接测试失败: {e}")
        return False


def test_image_discovery():
    """测试图像发现功能"""
    print("=" * 60)
    print("测试图像发现功能")
    print("=" * 60)
    
    try:
        processor = create_mushroom_processor()
        
        # 获取所有图像
        all_images = processor.get_mushroom_images()
        print(f"发现图像总数: {len(all_images)}")
        
        if all_images:
            # 显示前3个图像信息
            print("前3个图像信息:")
            for i, image_info in enumerate(all_images[:3]):
                print(f"  {i+1}. {image_info.file_name}")
                print(f"     蘑菇库号: {image_info.mushroom_id}")
                print(f"     采集时间: {image_info.collection_datetime}")
            
            # 测试过滤功能
            if len(all_images) > 0:
                first_mushroom_id = all_images[0].mushroom_id
                filtered_images = processor.get_mushroom_images(mushroom_id=first_mushroom_id)
                print(f"蘑菇库号 {first_mushroom_id} 的图像数: {len(filtered_images)}")
        
        return True
        
    except Exception as e:
        print(f"❌ 图像发现测试失败: {e}")
        return False


def test_image_processing():
    """测试图像处理功能"""
    print("=" * 60)
    print("测试图像处理功能")
    print("=" * 60)
    
    try:
        processor = create_mushroom_processor()
        
        # 获取图像列表
        images = processor.get_mushroom_images()
        
        if not images:
            print("⚠️ 没有找到图像文件，跳过处理测试")
            return True
        
        # 测试单个图像处理
        test_image = images[0]
        print(f"测试处理图像: {test_image.file_name}")
        
        success = processor.process_single_image(
            test_image,
            description=f"测试处理 - 蘑菇库号{test_image.mushroom_id}"
        )
        
        if success:
            print("✅ 单个图像处理成功")
        else:
            print("❌ 单个图像处理失败")
            return False
        
        # 验证数据库记录
        stats = processor.get_processing_statistics()
        processed_count = stats.get('total_processed', 0)
        print(f"数据库中已处理图像数: {processed_count}")
        
        return True
        
    except Exception as e:
        print(f"❌ 图像处理测试失败: {e}")
        return False


def create_test_image():
    """创建测试图像"""
    print("=" * 60)
    print("创建测试图像")
    print("=" * 60)
    
    try:
        # 检查本地测试图像
        test_image_path = project_root / "data" / "m1.jpg"
        
        if not test_image_path.exists():
            print(f"⚠️ 本地测试图像不存在: {test_image_path}")
            return False
        
        # 生成符合规范的文件名
        current_time = datetime.now()
        mushroom_id = "612"
        collection_ip = "1921681235"
        collection_date = current_time.strftime("%Y%m%d")
        detailed_time = current_time.strftime("%Y%m%d%H%M%S")
        
        # 构建MinIO路径
        filename = f"{mushroom_id}_{collection_ip}_{collection_date}_{detailed_time}.jpg"
        minio_path = f"mogu/{mushroom_id}/{collection_date}/{filename}"
        
        print(f"上传测试图像: {minio_path}")
        
        # 上传到MinIO
        service = create_minio_service()
        success = service.client.upload_image(str(test_image_path), minio_path)
        
        if success:
            print("✅ 测试图像上传成功")
            return True
        else:
            print("❌ 测试图像上传失败")
            return False
            
    except Exception as e:
        print(f"❌ 创建测试图像失败: {e}")
        return False


def run_comprehensive_test():
    """运行综合测试"""
    print("蘑菇图像处理系统综合测试")
    print(f"测试时间: {datetime.now()}")
    print(f"项目路径: {project_root}")
    print()
    
    test_results = []
    
    # 1. 路径解析测试
    result = test_path_parsing()
    test_results.append(("路径解析", result))
    
    # 2. MinIO连接测试
    result = test_minio_connection()
    test_results.append(("MinIO连接", result))
    
    # 3. 数据库连接测试
    result = test_database_connection()
    test_results.append(("数据库连接", result))
    
    # 4. 图像发现测试
    result = test_image_discovery()
    test_results.append(("图像发现", result))
    
    # 5. 如果没有图像，尝试创建测试图像
    processor = create_mushroom_processor()
    images = processor.get_mushroom_images()
    if not images:
        print("没有发现图像文件，尝试创建测试图像...")
        create_test_image()
    
    # 6. 图像处理测试
    result = test_image_processing()
    test_results.append(("图像处理", result))
    
    # 测试结果汇总
    print("=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    passed_count = 0
    total_count = len(test_results)
    
    for test_name, result in test_results:
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{test_name:12s}: {status}")
        if result:
            passed_count += 1
    
    print("-" * 40)
    print(f"总计: {passed_count}/{total_count} 测试通过")
    
    if passed_count == total_count:
        print("🎉 所有测试通过！系统运行正常。")
        print("\n使用说明:")
        print("1. 运行示例: python examples/mushroom_processing_example.py")
        print("2. 命令行工具: python scripts/mushroom_cli.py --help")
        print("3. 查看文档: docs/minio_setup_guide.md")
    else:
        print("⚠️ 部分测试失败，请检查系统配置。")
    
    return passed_count == total_count


def main():
    """主函数"""
    # 设置日志
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    try:
        success = run_comprehensive_test()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n测试被用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n测试执行失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
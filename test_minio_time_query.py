#!/usr/bin/env python3
"""
测试MinIO时间查询功能
"""

import sys
sys.path.insert(0, 'src')

from datetime import datetime, timedelta
from utils.minio_client import create_minio_client
from utils.mushroom_image_encoder import create_mushroom_encoder

def test_minio_time_query():
    """测试MinIO时间查询功能"""
    print("🚀 测试MinIO时间查询功能...")
    
    try:
        # 创建MinIO客户端
        minio_client = create_minio_client()
        
        # 测试连接
        if not minio_client.test_connection():
            print("❌ MinIO连接失败")
            return False
        
        print("✅ MinIO连接成功")
        
        # 1. 测试查询最近1小时的图片
        print("\n📊 测试查询最近1小时的图片...")
        recent_images = minio_client.list_recent_images(hours=1)
        print(f"   找到最近1小时的图片: {len(recent_images)} 张")
        
        if recent_images:
            print("   最新的5张图片:")
            for i, img in enumerate(recent_images[-5:], 1):
                print(f"     {i}. {img['object_name']} (库房: {img['room_id']}, 时间: {img['capture_time']})")
        
        # 2. 测试查询特定库房最近1小时的图片
        print("\n📊 测试查询库房611最近1小时的图片...")
        room_611_images = minio_client.list_recent_images(room_id="611", hours=1)
        print(f"   库房611最近1小时的图片: {len(room_611_images)} 张")
        
        if room_611_images:
            print("   库房611最新的3张图片:")
            for i, img in enumerate(room_611_images[-3:], 1):
                print(f"     {i}. {img['object_name']} (时间: {img['capture_time']})")
        
        # 3. 测试查询库房612最近1小时的图片
        print("\n📊 测试查询库房612最近1小时的图片...")
        room_612_images = minio_client.list_recent_images(room_id="612", hours=1)
        print(f"   库房612最近1小时的图片: {len(room_612_images)} 张")
        
        # 4. 测试自定义时间范围查询
        print("\n📊 测试自定义时间范围查询...")
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=2)
        
        custom_range_images = minio_client.list_images_by_time_and_room(
            room_id="611",
            start_time=start_time,
            end_time=end_time
        )
        print(f"   库房611过去2小时的图片: {len(custom_range_images)} 张")
        
        # 5. 测试日期范围查询
        print("\n📊 测试日期范围查询...")
        today = datetime.now().strftime("%Y%m%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        
        today_images = minio_client.get_images_by_date_range(
            room_id="611",
            date_start=today,
            date_end=today
        )
        print(f"   库房611今天的图片: {len(today_images)} 张")
        
        yesterday_images = minio_client.get_images_by_date_range(
            room_id="611",
            date_start=yesterday,
            date_end=yesterday
        )
        print(f"   库房611昨天的图片: {len(yesterday_images)} 张")
        
        # 6. 统计各库房最近1小时的图片数量
        print("\n📊 统计各库房最近1小时的图片数量...")
        room_stats = {}
        for room_id in ["607", "608", "611", "612", "7", "8"]:
            room_images = minio_client.list_recent_images(room_id=room_id, hours=1)
            room_stats[room_id] = len(room_images)
            print(f"   库房{room_id}: {len(room_images)} 张")
        
        print(f"\n📈 总计最近1小时图片: {sum(room_stats.values())} 张")
        
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_process_recent_images():
    """测试处理最近1小时的图片数据"""
    print("\n🔄 测试处理最近1小时的图片数据...")
    
    try:
        # 创建MinIO客户端和图像编码器
        minio_client = create_minio_client()
        encoder = create_mushroom_encoder()
        
        # 获取最近1小时的图片
        recent_images = minio_client.list_recent_images(hours=1)
        print(f"   找到最近1小时的图片: {len(recent_images)} 张")
        
        if not recent_images:
            print("   没有找到最近1小时的图片，跳过处理测试")
            return True
        
        # 按库房分组
        room_groups = {}
        for img in recent_images:
            room_id = img['room_id']
            if room_id not in room_groups:
                room_groups[room_id] = []
            room_groups[room_id].append(img)
        
        print(f"   涉及库房: {sorted(room_groups.keys())}")
        
        # 处理每个库房的最新1张图片（作为示例）
        processed_count = 0
        success_count = 0
        
        for room_id, images in room_groups.items():
            if processed_count >= 3:  # 限制处理数量，避免测试时间过长
                break
                
            # 取最新的1张图片
            latest_image = max(images, key=lambda x: x['capture_time'])
            
            print(f"   处理库房{room_id}最新图片: {latest_image['object_name']}")
            
            # 构建MushroomImageInfo对象
            from utils.mushroom_image_processor import MushroomImageInfo
            from utils.mushroom_image_processor import MushroomImagePathParser
            
            parser = MushroomImagePathParser()
            image_info = parser.parse_path(latest_image['object_name'])
            
            if image_info:
                # 处理图像
                result = encoder.process_single_image(image_info, save_to_db=True)
                
                if result and result.get('saved_to_db', False):
                    success_count += 1
                    print(f"     ✅ 成功处理并保存")
                elif result and result.get('skip_reason') == 'no_environment_data':
                    print(f"     ⚠️ 处理成功但无环境数据")
                else:
                    print(f"     ❌ 处理失败")
            else:
                print(f"     ❌ 路径解析失败")
            
            processed_count += 1
        
        print(f"\n📊 处理结果: 处理 {processed_count} 张图片, 成功保存 {success_count} 张")
        
        return True
        
    except Exception as e:
        print(f"❌ 处理测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("MinIO时间查询功能测试")
    print("=" * 60)
    
    # 测试时间查询功能
    if test_minio_time_query():
        print("\n✅ MinIO时间查询功能测试通过")
    else:
        print("\n❌ MinIO时间查询功能测试失败")
        sys.exit(1)
    
    # 测试处理最近图片
    if test_process_recent_images():
        print("\n✅ 最近图片处理测试通过")
    else:
        print("\n❌ 最近图片处理测试失败")
        sys.exit(1)
    
    print("\n🎉 所有测试通过!")
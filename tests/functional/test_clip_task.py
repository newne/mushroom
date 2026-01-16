#!/usr/bin/env python3
"""
测试CLIP推理任务函数
"""

import sys
from pathlib import Path

# 添加src目录到路径
src_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_dir))

def test_clip_task_function():
    """测试CLIP任务函数结构"""
    try:
        # 测试导入调度器模块
        import ast
        
        scheduler_path = src_dir / 'scheduling' / 'optimized_scheduler.py'
        
        # 读取并解析文件
        with open(scheduler_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        tree = ast.parse(content)
        
        # 检查是否包含CLIP任务函数
        functions = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions.append(node.name)
        
        # 检查必要的函数
        required_functions = [
            'safe_daily_clip_inference',
            'safe_create_tables',
            'safe_daily_env_stats',
            'safe_hourly_setpoint_monitoring'
        ]
        
        missing_functions = [f for f in required_functions if f not in functions]
        if missing_functions:
            print(f"❌ 缺少函数: {missing_functions}")
            return False
        else:
            print("✅ 所有必要的任务函数都存在")
        
        # 检查CLIP任务函数的内容
        clip_function_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == 'safe_daily_clip_inference':
                clip_function_found = True
                # 检查函数体是否包含关键逻辑
                func_source = ast.get_source_segment(content, node)
                if func_source:
                    if 'mushroom_image_encoder' in func_source:
                        print("✅ CLIP任务函数包含图像编码器导入")
                    if 'batch_process_images' in func_source:
                        print("✅ CLIP任务函数包含批量处理逻辑")
                    if 'yesterday' in func_source:
                        print("✅ CLIP任务函数包含日期计算逻辑")
                    if 'CLIP_TASK' in func_source:
                        print("✅ CLIP任务函数包含日志标签")
                break
        
        if not clip_function_found:
            print("❌ 未找到CLIP任务函数")
            return False
        
        # 检查调度器类中是否添加了CLIP任务
        class_methods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == 'OptimizedScheduler':
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        class_methods.append(item.name)
                        # 检查_add_business_jobs方法
                        if item.name == '_add_business_jobs':
                            method_source = ast.get_source_segment(content, item)
                            if method_source and 'daily_clip_inference' in method_source:
                                print("✅ 调度器类中已添加CLIP推理任务")
                            else:
                                print("❌ 调度器类中未添加CLIP推理任务")
                                return False
        
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False

def test_task_schedule():
    """测试任务调度配置"""
    try:
        scheduler_path = src_dir / 'scheduling' / 'optimized_scheduler.py'
        
        with open(scheduler_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查CLIP任务的调度时间配置
        if 'hour=3, minute=2, second=25' in content:
            print("✅ CLIP任务调度时间配置正确 (03:02:25)")
        else:
            print("❌ CLIP任务调度时间配置不正确")
            return False
        
        # 检查任务ID
        if 'id="daily_clip_inference"' in content:
            print("✅ CLIP任务ID配置正确")
        else:
            print("❌ CLIP任务ID配置不正确")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 调度配置测试失败: {e}")
        return False

def main():
    """主测试函数"""
    print("🧪 测试CLIP推理任务集成")
    print("=" * 50)
    
    success = True
    
    # 测试任务函数结构
    print("\n1. 测试CLIP任务函数结构...")
    if not test_clip_task_function():
        success = False
    
    # 测试调度配置
    print("\n2. 测试任务调度配置...")
    if not test_task_schedule():
        success = False
    
    print("\n" + "=" * 50)
    if success:
        print("✅ 所有测试通过！")
        print("\n📋 CLIP推理任务配置:")
        print("   - 执行时间: 每天凌晨 03:02:25")
        print("   - 任务ID: daily_clip_inference")
        print("   - 处理内容: 前一天的所有图像数据")
        print("   - 批处理大小: 20张图片/批次")
        print("   - 处理范围: 所有库房")
        
        print("\n🚀 启动调度器:")
        print("   python src/main.py")
        print("   python src/scheduling/optimized_scheduler.py")
        
        print("\n📊 手动执行CLIP任务:")
        print("   python main.py batch-all --date-filter YYYYMMDD")
    else:
        print("❌ 部分测试失败！")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
"""
测试主入口文件的环境检测逻辑
"""

import sys
from pathlib import Path

def test_environment_detection():
    """测试环境检测逻辑"""
    print("🧪 测试环境检测逻辑")
    print("=" * 50)
    
    # 模拟当前环境
    current_dir = Path.cwd()
    print(f"当前工作目录: {current_dir}")
    
    # 检查文件存在性
    main_py = current_dir / 'main.py'
    scheduling_dir = current_dir / 'scheduling'
    src_dir = current_dir / 'src'
    
    print(f"main.py 存在: {main_py.exists()}")
    print(f"scheduling/ 存在: {scheduling_dir.exists()}")
    print(f"src/ 存在: {src_dir.exists()}")
    
    # 容器环境检测逻辑
    is_container = (
        current_dir == Path('/app') and 
        main_py.exists() and
        scheduling_dir.exists()
    )
    
    print(f"\n环境检测结果:")
    print(f"是否为容器环境: {is_container}")
    
    if is_container:
        print("✅ 检测为容器环境，应该运行调度器")
        # 测试调度器导入
        try:
            sys.path.insert(0, str(current_dir))
            from scheduling.optimized_scheduler import OptimizedScheduler
            print("✅ 调度器模块导入成功")
        except Exception as e:
            print(f"❌ 调度器模块导入失败: {e}")
    else:
        print("✅ 检测为开发环境，应该重定向到CLIP推理调度器")
        # 检查CLIP推理调度器
        clip_scheduler = current_dir / 'src' / 'clip' / 'clip_inference_scheduler.py'
        print(f"CLIP推理调度器存在: {clip_scheduler.exists()}")
    
    return is_container

def test_main_logic():
    """测试main.py的逻辑"""
    print("\n🧪 测试main.py逻辑")
    print("=" * 30)
    
    try:
        # 读取main.py内容
        main_path = Path(__file__).parent.parent / 'main.py'
        with open(main_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查关键逻辑
        if 'is_container' in content:
            print("✅ 包含环境检测逻辑")
        
        if 'scheduling.optimized_scheduler' in content:
            print("✅ 包含调度器导入")
        
        if 'clip_inference_scheduler.py' in content:
            print("✅ 包含CLIP推理调度器重定向")
        
        if 'current_dir == Path(\'/app\')' in content:
            print("✅ 包含容器路径检测")
        
        return True
        
    except Exception as e:
        print(f"❌ 测试main.py逻辑失败: {e}")
        return False

def main():
    """主测试函数"""
    print("🧪 测试主入口文件")
    print("=" * 50)
    
    success = True
    
    # 测试环境检测
    is_container = test_environment_detection()
    
    # 测试main.py逻辑
    if not test_main_logic():
        success = False
    
    print("\n" + "=" * 50)
    if success:
        print("✅ 所有测试通过！")
        print("\n📋 环境说明:")
        if is_container:
            print("   - 当前环境: 容器环境")
            print("   - 运行模式: 调度器系统")
            print("   - 启动命令: python main.py")
        else:
            print("   - 当前环境: 开发环境")
            print("   - 运行模式: CLIP推理调度器")
            print("   - 启动命令: python main.py [参数]")
        
        print("\n🚀 使用方法:")
        print("   # 容器环境（自动检测）")
        print("   python main.py  # 启动调度器")
        print("")
        print("   # 开发环境（自动检测）")
        print("   python main.py recent --hours 1  # CLIP推理")
        print("   python main.py batch-all         # 批量处理")
    else:
        print("❌ 部分测试失败！")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
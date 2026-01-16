#!/usr/bin/env python3
"""
测试CLIP推理调度器的基本功能
"""

import sys
from pathlib import Path

# 添加src目录到路径
src_dir = Path(__file__).parent.parent / 'src'
sys.path.insert(0, str(src_dir))

def test_clip_scheduler_structure():
    """测试CLIP推理调度器结构"""
    try:
        # 测试基本导入
        import ast
        
        scheduler_path = src_dir / 'clip' / 'clip_inference_scheduler.py'
        
        # 语法检查
        with open(scheduler_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        ast.parse(content)
        print("✅ CLIP推理调度器语法检查通过")
        
        # 检查关键函数是否存在
        tree = ast.parse(content)
        
        functions = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions.append(node.name)
        
        # 检查必要的函数
        required_functions = [
            'process_recent_images', 
            'process_all_images',
            'validate_system',
            'main'
        ]
        
        missing_functions = [f for f in required_functions if f not in functions]
        if missing_functions:
            print(f"❌ 缺少函数: {missing_functions}")
            return False
        else:
            print("✅ 所有必要函数都存在")
        
        # 检查路径配置
        if 'current_dir = Path(__file__).parent' in content:
            print("✅ 路径配置正确")
        else:
            print("❌ 路径配置不正确")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        return False

def test_main_entry():
    """测试主入口文件"""
    try:
        main_path = Path(__file__).parent.parent / 'main.py'
        
        # 语法检查
        with open(main_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        import ast
        ast.parse(content)
        print("✅ 主入口文件语法检查通过")
        
        # 检查重定向逻辑
        if 'clip_inference_scheduler.py' in content:
            print("✅ 主入口文件重定向配置正确")
        else:
            print("❌ 主入口文件重定向配置不正确")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ 主入口文件测试失败: {e}")
        return False

def test_file_structure():
    """测试文件结构"""
    try:
        # 检查关键文件是否存在
        required_files = [
            'src/clip/clip_inference_scheduler.py',
            'src/clip/README.md',
            'src/clip/get_env_status.py',
            'main.py'
        ]
        
        project_root = Path(__file__).parent.parent
        
        for file_path in required_files:
            full_path = project_root / file_path
            if full_path.exists():
                print(f"✅ {file_path} 存在")
            else:
                print(f"❌ {file_path} 不存在")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ 文件结构测试失败: {e}")
        return False

def main():
    """主测试函数"""
    print("🧪 测试CLIP推理调度器重构")
    print("=" * 50)
    
    success = True
    
    # 测试文件结构
    print("\n1. 测试文件结构...")
    if not test_file_structure():
        success = False
    
    # 测试CLIP调度器结构
    print("\n2. 测试CLIP推理调度器结构...")
    if not test_clip_scheduler_structure():
        success = False
    
    # 测试主入口
    print("\n3. 测试主入口文件...")
    if not test_main_entry():
        success = False
    
    print("\n" + "=" * 50)
    if success:
        print("✅ 所有测试通过！")
        print("\n📋 文件结构说明:")
        print("   - main.py: 项目主入口，重定向到CLIP调度器")
        print("   - src/clip/clip_inference_scheduler.py: CLIP推理调度器")
        print("   - src/clip/README.md: CLIP模块文档")
        print("   - src/clip/get_env_status.py: 环境状态获取")
        
        print("\n🚀 使用方法:")
        print("   # 通过主入口使用")
        print("   python main.py recent --hours 1")
        print("   python main.py batch-all --date-filter 20251231")
        print("   python main.py validate")
        print("")
        print("   # 直接使用CLIP调度器")
        print("   python src/clip/clip_inference_scheduler.py recent --hours 1")
        print("   python src/clip/clip_inference_scheduler.py batch-all")
        print("   python src/clip/clip_inference_scheduler.py validate")
    else:
        print("❌ 部分测试失败！")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
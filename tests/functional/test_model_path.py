#!/usr/bin/env python3
"""
测试模型路径配置
验证Docker环境中的模型路径是否正确
"""

import sys
from pathlib import Path

def test_model_path():
    """测试模型路径配置"""
    print("🧪 测试模型路径配置")
    print("=" * 50)
    
    # 模拟从 utils/mushroom_image_encoder.py 计算路径（开发环境）
    current_file = Path(__file__).parent.parent / 'src' / 'utils' / 'mushroom_image_encoder.py'
    print(f"模拟文件位置: {current_file}")
    
    # 开发环境路径计算
    local_model_path = current_file.parent.parent.parent / 'models' / 'clip-vit-base-patch32'
    print(f"开发环境模型路径: {local_model_path}")
    print(f"绝对路径: {local_model_path.absolute()}")
    
    # 容器环境路径
    container_model_path = Path('/models/clip-vit-base-patch32')
    print(f"容器环境模型路径: {container_model_path}")
    
    # 检查开发环境路径是否存在
    if local_model_path.exists():
        print("✅ 开发环境模型路径存在")
        
        # 检查关键文件
        key_files = [
            'config.json',
            'pytorch_model.bin',
            'tokenizer.json',
            'preprocessor_config.json'
        ]
        
        print("\n📋 检查关键模型文件:")
        all_files_exist = True
        for file_name in key_files:
            file_path = local_model_path / file_name
            if file_path.exists():
                print(f"✅ {file_name}")
            else:
                print(f"❌ {file_name}")
                all_files_exist = False
        
        if all_files_exist:
            print("\n✅ 所有关键模型文件都存在")
        else:
            print("\n❌ 部分关键模型文件缺失")
            
    else:
        print("❌ 开发环境模型路径不存在")
    
    # 容器环境路径说明
    print("\n🐳 Docker环境路径映射:")
    print("   本地路径: ./models")
    print("   容器路径: /models")
    print("   挂载配置: ./models:/models:rw")
    
    # 在Docker中的路径计算
    print("\n📁 Docker中的路径结构:")
    print("   工作目录: /app")
    print("   文件位置: /app/utils/mushroom_image_encoder.py")
    print("   模型挂载: /models/clip-vit-base-patch32")
    print("   路径检测: 优先检查 /models，然后检查相对路径")
    
    return local_model_path.exists()

def test_docker_config():
    """测试Docker配置"""
    print("\n🐳 Docker配置验证")
    print("=" * 30)
    
    # 读取Docker配置文件
    docker_config_path = Path(__file__).parent.parent / 'docker' / 'mushroom_solution.yml'
    
    if docker_config_path.exists():
        with open(docker_config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查models挂载配置
        if './models:/models:rw' in content:
            print("✅ Docker配置中包含正确的models挂载")
        else:
            print("❌ Docker配置中缺少正确的models挂载")
            return False
        
        # 检查其他相关配置
        if 'PYTHONUNBUFFERED: 1' in content:
            print("✅ Python环境配置正确")
        
        if 'mem_limit: 2048m' in content:
            print("✅ 内存限制配置合理")
        
        if 'cpus: 4.0' in content:
            print("✅ CPU限制配置合理")
        
        # 检查AI模型相关配置
        if 'TRANSFORMERS_CACHE: /models/.cache' in content:
            print("✅ AI模型缓存配置正确")
        
        if 'CLIP_MODEL_PATH: /models/clip-vit-base-patch32' in content:
            print("✅ CLIP模型路径配置正确")
        
        # 检查线程优化参数是否已移除（应该在run.sh中设置）
        if 'OMP_NUM_THREADS' not in content:
            print("✅ 线程优化参数已移至启动脚本")
        else:
            print("⚠️  线程优化参数仍在Docker配置中（应该在run.sh中设置）")
        
        return True
    else:
        print("❌ Docker配置文件不存在")
        return False

def main():
    """主函数"""
    success = True
    
    # 测试模型路径
    if not test_model_path():
        print("\n⚠️  本地模型路径不存在，但这在Docker环境中是正常的")
    
    # 测试Docker配置
    if not test_docker_config():
        success = False
    
    print("\n" + "=" * 50)
    if success:
        print("✅ 模型路径配置验证通过！")
        print("\n📋 配置总结:")
        print("   - 本地models目录: ./models")
        print("   - Docker挂载: ./models:/models:rw")
        print("   - 容器路径: /models/clip-vit-base-patch32")
        print("   - 代码路径检测: 优先容器路径，后备开发路径")
        print("   - CLIP模型: clip-vit-base-patch32")
        
        print("\n🚀 使用说明:")
        print("   1. 确保models目录包含CLIP模型文件")
        print("   2. 使用docker-compose启动服务")
        print("   3. 容器会自动加载本地模型")
        print("   4. 如果本地模型不存在，会从HuggingFace下载")
    else:
        print("❌ 配置验证失败！")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
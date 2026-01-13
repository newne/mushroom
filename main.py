#!/usr/bin/env python3
"""
蘑菇图像处理系统主入口
重定向到CLIP推理调度器
"""

import sys
import subprocess
from pathlib import Path

def main():
    """主函数 - 重定向到CLIP推理调度器"""
    
    # 获取CLIP推理调度器路径
    clip_scheduler_path = Path(__file__).parent / 'src' / 'clip' / 'clip_inference_scheduler.py'
    
    if not clip_scheduler_path.exists():
        print("❌ 找不到CLIP推理调度器文件")
        sys.exit(1)
    
    # 重定向所有参数到CLIP推理调度器
    try:
        # 构建命令
        cmd = [sys.executable, str(clip_scheduler_path)] + sys.argv[1:]
        
        # 执行命令
        result = subprocess.run(cmd, check=False)
        sys.exit(result.returncode)
        
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断操作")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 执行失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
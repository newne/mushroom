#!/usr/bin/env python3
"""
增强决策分析系统部署验证脚本

快速验证增强决策分析系统在生产环境中的部署状态
"""

import sys
from pathlib import Path
from datetime import datetime

# 添加src到路径
sys.path.insert(0, str(Path(__file__).parent / "src"))

def verify_deployment():
    """验证部署状态"""
    print("🔍 增强决策分析系统部署验证")
    print("=" * 50)
    
    checks = []
    
    # 1. 检查虚拟环境
    try:
        import os
        venv_path = os.environ.get('VIRTUAL_ENV')
        if venv_path and '.venv' in venv_path:
            print("✅ UV虚拟环境: 已激活")
            checks.append(True)
        else:
            print("❌ UV虚拟环境: 未激活")
            checks.append(False)
    except Exception as e:
        print(f"❌ 环境检查失败: {e}")
        checks.append(False)
    
    # 2. 检查增强决策分析模块
    try:
        from decision_analysis.decision_analyzer import DecisionAnalyzer
        if hasattr(DecisionAnalyzer, 'analyze_enhanced'):
            print("✅ 增强决策分析器: 可用")
            checks.append(True)
        else:
            print("❌ 增强决策分析器: 方法缺失")
            checks.append(False)
    except Exception as e:
        print(f"❌ 决策分析器检查失败: {e}")
        checks.append(False)
    
    # 3. 检查增强任务模块
    try:
        from tasks import safe_enhanced_decision_analysis_10_00
        print("✅ 增强决策任务: 可用")
        checks.append(True)
    except Exception as e:
        print(f"❌ 增强决策任务检查失败: {e}")
        checks.append(False)
    
    # 4. 检查配置
    try:
        from global_const.const_config import DECISION_ANALYSIS_CONFIG
        if 'image_aggregation_window' in DECISION_ANALYSIS_CONFIG:
            print("✅ 增强配置: 已加载")
            checks.append(True)
        else:
            print("❌ 增强配置: 配置缺失")
            checks.append(False)
    except Exception as e:
        print(f"❌ 配置检查失败: {e}")
        checks.append(False)
    
    # 5. 检查数据库连接
    try:
        from global_const.global_const import pgsql_engine
        import sqlalchemy
        with pgsql_engine.connect() as conn:
            conn.execute(sqlalchemy.text("SELECT 1"))
        print("✅ 数据库连接: 正常")
        checks.append(True)
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        checks.append(False)
    
    # 6. 检查调度器配置
    try:
        from scheduling.optimized_scheduler import OptimizedScheduler
        scheduler = OptimizedScheduler()
        bg_scheduler = scheduler._init_scheduler()
        scheduler.scheduler = bg_scheduler
        scheduler._add_business_jobs()
        
        jobs = bg_scheduler.get_jobs()
        enhanced_jobs = [job for job in jobs if "enhanced_decision_analysis" in job.id]
        
        if len(enhanced_jobs) >= 3:
            print(f"✅ 调度器配置: {len(enhanced_jobs)}个增强任务")
            checks.append(True)
        else:
            print(f"❌ 调度器配置: 只有{len(enhanced_jobs)}个增强任务")
            checks.append(False)
        
        try:
            bg_scheduler.shutdown(wait=False)
        except:
            pass  # 调度器可能已经关闭
    except Exception as e:
        print(f"❌ 调度器配置检查失败: {e}")
        checks.append(False)
    
    # 汇总结果
    passed = sum(checks)
    total = len(checks)
    
    print("\n" + "=" * 50)
    print(f"验证结果: {passed}/{total} 项检查通过")
    
    if passed == total:
        print("\n🎉 部署验证成功！")
        print("✨ 增强决策分析系统已就绪")
        print("\n📋 系统功能:")
        print("  • 多图像综合分析")
        print("  • 结构化参数调整")
        print("  • 风险评估和优先级")
        print("  • 增强LLM提示解析")
        print("  • 完整调度器集成")
        print("\n🚀 系统可以投入生产使用！")
        return True
    else:
        print(f"\n⚠️  部署验证失败！")
        print(f"需要修复 {total - passed} 个问题后再投入使用")
        return False

if __name__ == "__main__":
    success = verify_deployment()
    sys.exit(0 if success else 1)
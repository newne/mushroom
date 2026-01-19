# 蘑菇种植智能调控系统 - 文档中心

本目录包含蘑菇种植智能调控系统的完整文档，已按功能分类并使用中文命名，便于快速查找和阅读。

## 📚 文档导航

### 核心文档（根目录）
按照阅读顺序编号，建议新用户按序阅读：

1. [00_系统概览.md](00_系统概览.md) - 系统整体介绍和核心功能
2. [01_系统架构流程图.md](01_系统架构流程图.md) - 系统架构和数据流程
3. [02_CLIP推理与全局工作流.md](02_CLIP推理与全局工作流.md) - CLIP模型推理详细流程
4. [03_使用指南.md](03_使用指南.md) - 系统使用方法和API接口
5. [04_项目结构说明.md](04_项目结构说明.md) - 完整的项目目录结构和文件说明
6. [05_蘑菇系统功能说明.md](05_蘑菇系统功能说明.md) - 蘑菇系统的详细功能说明
7. [06_系统优化总结.md](06_系统优化总结.md) - 系统性能优化和技术特性
8. [overview_optimization.mermaid](overview_optimization.mermaid) - 系统优化流程图（Mermaid格式）

### 开发文档（development/）
记录开发过程中的任务实现、问题修复和系统优化。

#### 综合文档
- [监控点提取完成说明.md](development/监控点提取完成说明.md)
- [项目结构清理总结.md](development/项目结构清理总结.md)
- [Prompt_API集成总结.md](development/Prompt_API集成总结.md)
- [设定点监控代码分析.md](development/设定点监控代码分析.md)

#### 任务实现（tasks/）
按任务编号排列，记录功能模块的实现过程：
- TASK_2.x - 数据提取和处理功能
- TASK_3.x - 系统集成和验证
- TASK_4 - 第一阶段检查点
- TASK_5 - 决策分析功能
- TASK_6 - 相似案例匹配
- TASK_7 - 提示词模板渲染
- TASK_8 - LLM集成
- TASK_9 - 第二阶段检查点
- TASK_10.x - 输出处理和验证
- TASK_11 - 文档完善
- TASK_12 - 最终检查点

#### 问题修复（fixes/）
按修复编号排列，记录各类问题的诊断和解决方案：
- FIX_01 - 完整修复总结
- FIX_02~04 - Docker相关问题修复
- FIX_05 - 最终修复总结
- FIX_06~10 - LLM JSON解析问题修复系列
- FIX_11 - Prompt API修复
- FIX_12 - Redis连接修复
- FIX_13~14 - 调度器相关修复

#### 系统优化（optimizations/）
按优化编号排列，记录性能优化和功能改进：
- OPT_01 - 日统计可视化
- OPT_02 - DataFrame工具优化
- OPT_03 - LLaMA JSON格式更新
- OPT_04 - 日志系统优化
- OPT_05 - 表结构优化

### 部署文档（deployment/）
系统部署、配置和运维相关文档。

1. [01_部署指南.md](deployment/01_部署指南.md) - 详细的部署步骤和配置说明
2. [02_部署检查清单.md](deployment/02_部署检查清单.md) - 部署前后的检查项目
3. [03_快速部署参考.md](deployment/03_快速部署参考.md) - 快速部署命令参考
4. [04_Docker部署说明.md](deployment/04_Docker部署说明.md) - Docker容器化部署详解

### 使用指南（guides/）
各功能模块的详细使用指南和最佳实践。

1. [01_日志查看指南.md](guides/01_日志查看指南.md) - 日志系统使用和问题排查
2. [02_Prompt_API快速参考.md](guides/02_Prompt_API快速参考.md) - Prompt API接口快速参考
3. [03_设定点监控迁移指南.md](guides/03_设定点监控迁移指南.md) - 设定点监控功能迁移指南
4. [04_MinIO配置指南.md](guides/04_MinIO配置指南.md) - MinIO对象存储配置
5. [05_蘑菇图像处理指南.md](guides/05_蘑菇图像处理指南.md) - 蘑菇图像处理功能详解
6. [06_设定点监控指南.md](guides/06_设定点监控指南.md) - 设定点监控功能使用

### 参考文档（reference/）
技术参考、API文档和配置说明。

- [缓存优化指南.md](reference/缓存优化指南.md) - 系统缓存机制和优化策略
- [日统计可视化指南.md](reference/日统计可视化指南.md) - 环境数据日统计可视化
- [设备监控点参考.md](reference/设备监控点参考.md) - 完整的设备监控点配置参考
- [监控点配置提取指南.md](reference/监控点配置提取指南.md) - 监控点配置提取工具使用
- [设备配置获取优化.md](reference/设备配置获取优化.md) - 设备配置查询性能优化
- [设备配置获取优化总结.md](reference/设备配置获取优化总结.md) - 优化效果总结
- [Prompt_API集成指南.md](reference/Prompt_API集成指南.md) - Prompt API集成技术文档

### 归档文档（archive/）
已过时或不再使用的历史文档。

## 🔍 快速查找

### 按功能查找
- **图像处理**: [05_蘑菇图像处理指南.md](guides/05_蘑菇图像处理指南.md)
- **决策分析**: [../src/decision_analysis/README.md](../src/decision_analysis/README.md)
- **环境监控**: [06_设定点监控指南.md](guides/06_设定点监控指南.md)
- **数据可视化**: [日统计可视化指南.md](reference/日统计可视化指南.md)
- **系统部署**: [deployment/](deployment/)

### 按问题类型查找
- **部署问题**: [deployment/](deployment/)
- **数据库问题**: [development/fixes/FIX_02_Docker数据库连接修复.md](development/fixes/FIX_02_Docker数据库连接修复.md)
- **LLM问题**: [development/fixes/](development/fixes/) (FIX_06~10)
- **性能问题**: [development/optimizations/](development/optimizations/)

## 📝 文档维护规范

### 新增文档
1. **开发任务**: 在 `development/tasks/` 创建 `TASK_XX_功能名称.md`
2. **问题修复**: 在 `development/fixes/` 创建 `FIX_XX_问题描述.md`
3. **系统优化**: 在 `development/optimizations/` 创建 `OPT_XX_优化内容.md`
4. **使用指南**: 在 `guides/` 创建编号文档，使用中文命名

### 文档规范
1. 使用Markdown格式
2. 包含清晰的标题层级
3. 添加必要的代码示例
4. 记录创建/更新日期
5. 使用中文命名，便于识别
6. 按编号排序，保持顺序清晰

### 文档归档
当文档内容过时或功能已废弃时，移至 `archive/` 目录，并在文档开头标注归档原因和日期。

## 🔗 相关资源

- **源代码**: [../src/](../src/)
- **测试代码**: [../tests/](../tests/)
- **示例代码**: [../examples/](../examples/)
- **脚本工具**: [../scripts/](../scripts/)

## 📞 获取帮助

遇到问题时，请按以下顺序查找解决方案：
1. 查看 [问题修复文档](development/fixes/)
2. 查看 [使用指南](guides/)
3. 查看 [部署文档](deployment/)
4. 联系开发团队

---

**文档最后更新**: 2026-01-13  
**文档版本**: v2.0（重组优化版）

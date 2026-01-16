# 文档目录

本目录包含蘑菇种植智能调控系统的所有文档。

## 目录结构

### development/ - 开发文档
包含开发过程中的任务实现、问题修复和系统优化文档。

- **tasks/** - 任务实现文档
  - 记录各个功能模块的实现过程
  - 包含需求分析、设计方案和实现细节
  
- **fixes/** - 问题修复文档
  - 记录系统运行中遇到的问题及解决方案
  - 包含数据库连接、调度器、LLM 解析等问题的修复
  
- **optimizations/** - 优化文档
  - 记录系统性能优化和代码重构
  - 包含数据库查询、日志系统、数据处理等优化

### deployment/ - 部署文档
包含系统部署相关的文档和检查清单。

- Docker 部署指南
- 部署检查清单
- 快速部署参考

### guides/ - 使用指南
包含系统使用和维护的指南文档。

- 日志查看指南
- 监控点配置指南
- 数据处理指南
- MinIO 设置指南

### archive/ - 归档文档
包含已过时或不再使用的文档。

## 主要文档

### 系统架构
- [系统概览](system_overview.md)
- [系统架构流程图](system_architecture_flowchart.md)
- [CLIP 推理和全局工作流](CLIP_INFERENCE_AND_GLOBAL_WORKFLOW.md)

### 功能模块
- [决策分析系统](../src/decision_analysis/README.md)
- [蘑菇图像处理](mushroom_processing_guide.md)
- [设备监控点参考](device_monitoring_points_reference.md)
- [设定点监控指南](setpoint_monitoring_guide.md)

### 优化和性能
- [系统优化总结](SYSTEM_OPTIMIZATION_SUMMARY.md)
- [缓存优化指南](cache_optimization_guide.md)
- [数据库查询优化](get_all_device_configs_optimization.md)

### API 集成
- [Prompt API 集成指南](prompt_api_integration_guide.md)

## 文档维护

- 新增功能时，请在相应目录下创建文档
- 修复问题时，请记录问题和解决方案
- 优化系统时，请记录优化前后的对比
- 定期将过时文档移至 archive/ 目录

## 文档规范

1. 使用 Markdown 格式
2. 文件名使用小写字母和下划线
3. 包含清晰的标题和目录
4. 添加代码示例和配置示例
5. 记录日期和版本信息

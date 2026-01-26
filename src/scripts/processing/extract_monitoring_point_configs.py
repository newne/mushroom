#!/usr/bin/env python3
"""
监控点配置提取工具

从 setpoint_monitor_config.json 和 static_config.json 中提取所有监控点的完整配置信息，
包括 point_alias、point_name、阈值、枚举值映射等。

使用方法:
    python scripts/extract_monitoring_point_configs.py
    python scripts/extract_monitoring_point_configs.py --output monitoring_points.json
    python scripts/extract_monitoring_point_configs.py --pretty
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any
import argparse

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

from loguru import logger


class MonitoringPointConfigExtractor:
    """监控点配置提取器"""
    
    def __init__(self, 
                 monitor_config_path: Path = None,
                 static_config_path: Path = None):
        """
        初始化配置提取器
        
        Args:
            monitor_config_path: setpoint_monitor_config.json 文件路径
            static_config_path: static_config.json 文件路径
        """
        self.monitor_config_path = monitor_config_path or BASE_DIR / "src/configs/setpoint_monitor_config.json"
        self.static_config_path = static_config_path or BASE_DIR / "src/configs/static_config.json"
        
        self.monitor_config = None
        self.static_config = None
        
        logger.info(f"[EXTRACT-001] 初始化配置提取器")
        logger.info(f"  - 监控配置文件: {self.monitor_config_path}")
        logger.info(f"  - 静态配置文件: {self.static_config_path}")
    
    def load_configs(self) -> bool:
        """
        加载配置文件
        
        Returns:
            是否加载成功
        """
        try:
            # 加载监控配置
            logger.info(f"[EXTRACT-002] 加载监控配置文件...")
            if not self.monitor_config_path.exists():
                logger.error(f"[EXTRACT-002] 监控配置文件不存在: {self.monitor_config_path}")
                return False
            
            with open(self.monitor_config_path, 'r', encoding='utf-8') as f:
                self.monitor_config = json.load(f)
            logger.info(f"[EXTRACT-002] 监控配置加载成功")
            
            # 加载静态配置
            logger.info(f"[EXTRACT-003] 加载静态配置文件...")
            if not self.static_config_path.exists():
                logger.error(f"[EXTRACT-003] 静态配置文件不存在: {self.static_config_path}")
                return False
            
            with open(self.static_config_path, 'r', encoding='utf-8') as f:
                self.static_config = json.load(f)
            logger.info(f"[EXTRACT-003] 静态配置加载成功")
            
            return True
            
        except json.JSONDecodeError as e:
            logger.error(f"[EXTRACT-004] JSON解析错误: {e}")
            return False
        except Exception as e:
            logger.error(f"[EXTRACT-004] 加载配置文件失败: {e}")
            return False
    
    def _get_change_type(self, point_alias: str, device_type: str) -> str:
        """
        推断监控点的变更检测类型
        
        Args:
            point_alias: 监控点别名
            device_type: 设备类型
            
        Returns:
            变更检测类型
        """
        # 从监控配置中获取阈值配置
        thresholds = self.monitor_config.get('thresholds', {}).get(device_type, {})
        
        # 如果有阈值配置，说明是模拟量
        if point_alias in thresholds:
            return "analog_value"
        
        # 根据命名模式推断
        if point_alias in ['mode', 'model', 'control', 'status']:
            return "enum_state"
        elif point_alias.endswith('_on_off') or point_alias.startswith('on_off'):
            return "digital_on_off"
        elif point_alias.startswith('choose'):
            return "enum_state"
        else:
            # 默认为模拟量
            return "analog_value"
    
    def _get_threshold(self, point_alias: str, device_type: str) -> Optional[float]:
        """
        获取监控点的阈值配置
        
        Args:
            point_alias: 监控点别名
            device_type: 设备类型
            
        Returns:
            阈值，如果没有配置则返回None
        """
        thresholds = self.monitor_config.get('thresholds', {}).get(device_type, {})
        return thresholds.get(point_alias)
    
    def _get_enum_mapping(self, point_config: Dict) -> Optional[Dict[str, str]]:
        """
        获取枚举值映射
        
        Args:
            point_config: 监控点配置
            
        Returns:
            枚举值映射字典，如果没有则返回None
        """
        # 检查是否有 enum 或 enmum 字段（配置文件中有拼写错误）
        enum_mapping = point_config.get('enum') or point_config.get('enmum')
        
        if enum_mapping:
            return enum_mapping
        
        return None
    
    def extract_monitoring_points(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        提取所有监控点配置
        
        Returns:
            按设备类型组织的监控点配置字典
        """
        if not self.monitor_config or not self.static_config:
            logger.error(f"[EXTRACT-005] 配置文件未加载，请先调用 load_configs()")
            return {}
        
        logger.info(f"[EXTRACT-005] 开始提取监控点配置...")
        
        result = {}
        
        # 获取监控配置中定义的设备类型
        device_types_config = self.monitor_config.get('device_types', {})
        
        for device_type, device_config in device_types_config.items():
            logger.info(f"[EXTRACT-006] 处理设备类型: {device_type}")
            
            # 获取该设备类型的监控点列表
            monitored_points = device_config.get('monitored_points', [])
            
            if not monitored_points:
                logger.warning(f"[EXTRACT-006] 设备类型 {device_type} 没有配置监控点")
                continue
            
            # 从静态配置中获取该设备类型的详细信息
            static_device_config = self.static_config.get('mushroom', {}).get('datapoint', {}).get(device_type)
            
            if not static_device_config:
                logger.warning(f"[EXTRACT-006] 静态配置中未找到设备类型: {device_type}")
                continue
            
            # 获取点位列表
            point_list = static_device_config.get('point_list', [])
            
            # 提取监控点配置
            device_monitoring_points = []
            
            for point_alias in monitored_points:
                # 在点位列表中查找对应的配置
                point_config = None
                for point in point_list:
                    if point.get('point_alias') == point_alias:
                        point_config = point
                        break
                
                if not point_config:
                    logger.warning(
                        f"[EXTRACT-007] 未找到监控点配置 | "
                        f"设备类型: {device_type}, point_alias: {point_alias}"
                    )
                    continue
                
                # 构建完整的监控点配置
                monitoring_point = {
                    'device_type': device_type,
                    'point_alias': point_alias,
                    'point_name': point_config.get('point_name'),
                    'remark': point_config.get('remark'),
                    'change_type': self._get_change_type(point_alias, device_type),
                    'threshold': self._get_threshold(point_alias, device_type),
                    'enum_mapping': self._get_enum_mapping(point_config)
                }
                
                device_monitoring_points.append(monitoring_point)
                
                logger.debug(
                    f"[EXTRACT-007] 提取监控点 | "
                    f"设备类型: {device_type}, "
                    f"point_alias: {point_alias}, "
                    f"point_name: {monitoring_point['point_name']}"
                )
            
            result[device_type] = device_monitoring_points
            
            logger.info(
                f"[EXTRACT-006] 设备类型 {device_type} 完成 | "
                f"监控点数量: {len(device_monitoring_points)}"
            )
        
        logger.info(
            f"[EXTRACT-008] 监控点配置提取完成 | "
            f"设备类型数: {len(result)}, "
            f"总监控点数: {sum(len(points) for points in result.values())}"
        )
        
        return result
    
    def generate_summary(self, monitoring_points: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        生成配置摘要
        
        Args:
            monitoring_points: 监控点配置字典
            
        Returns:
            摘要信息
        """
        summary = {
            'total_device_types': len(monitoring_points),
            'total_monitoring_points': sum(len(points) for points in monitoring_points.values()),
            'device_type_summary': {},
            'change_type_distribution': {},
            'threshold_configured_count': 0,
            'enum_configured_count': 0
        }
        
        for device_type, points in monitoring_points.items():
            summary['device_type_summary'][device_type] = {
                'monitoring_point_count': len(points),
                'point_aliases': [p['point_alias'] for p in points]
            }
            
            for point in points:
                # 统计变更类型分布
                change_type = point['change_type']
                summary['change_type_distribution'][change_type] = \
                    summary['change_type_distribution'].get(change_type, 0) + 1
                
                # 统计阈值配置
                if point['threshold'] is not None:
                    summary['threshold_configured_count'] += 1
                
                # 统计枚举值配置
                if point['enum_mapping']:
                    summary['enum_configured_count'] += 1
        
        return summary
    
    def save_to_file(self, 
                     monitoring_points: Dict[str, List[Dict[str, Any]]],
                     output_path: Path,
                     include_summary: bool = True,
                     pretty: bool = False) -> bool:
        """
        保存配置到文件
        
        Args:
            monitoring_points: 监控点配置字典
            output_path: 输出文件路径
            include_summary: 是否包含摘要信息
            pretty: 是否格式化输出
            
        Returns:
            是否保存成功
        """
        try:
            output_data = {
                'monitoring_points': monitoring_points
            }
            
            if include_summary:
                output_data['summary'] = self.generate_summary(monitoring_points)
            
            # 保存到文件
            with open(output_path, 'w', encoding='utf-8') as f:
                if pretty:
                    json.dump(output_data, f, ensure_ascii=False, indent=2)
                else:
                    json.dump(output_data, f, ensure_ascii=False)
            
            logger.info(f"[EXTRACT-009] 配置已保存到文件: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"[EXTRACT-009] 保存文件失败: {e}")
            return False


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='提取监控点配置信息',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 提取配置并输出到控制台
  python scripts/extract_monitoring_point_configs.py
  
  # 保存到指定文件
  python scripts/extract_monitoring_point_configs.py --output monitoring_points.json
  
  # 格式化输出
  python scripts/extract_monitoring_point_configs.py --pretty
  
  # 保存到文件并格式化
  python scripts/extract_monitoring_point_configs.py --output monitoring_points.json --pretty
        """
    )
    
    parser.add_argument(
        '--output', '-o',
        type=str,
        help='输出文件路径（如果不指定则输出到控制台）'
    )
    
    parser.add_argument(
        '--pretty', '-p',
        action='store_true',
        help='格式化输出JSON'
    )
    
    parser.add_argument(
        '--no-summary',
        action='store_true',
        help='不包含摘要信息'
    )
    
    parser.add_argument(
        '--monitor-config',
        type=str,
        help='监控配置文件路径（默认: src/configs/setpoint_monitor_config.json）'
    )
    
    parser.add_argument(
        '--static-config',
        type=str,
        help='静态配置文件路径（默认: src/configs/static_config.json）'
    )
    
    args = parser.parse_args()
    
    # 创建提取器
    monitor_config_path = Path(args.monitor_config) if args.monitor_config else None
    static_config_path = Path(args.static_config) if args.static_config else None
    
    extractor = MonitoringPointConfigExtractor(
        monitor_config_path=monitor_config_path,
        static_config_path=static_config_path
    )
    
    # 加载配置
    if not extractor.load_configs():
        logger.error("配置文件加载失败，程序退出")
        return 1
    
    # 提取监控点配置
    monitoring_points = extractor.extract_monitoring_points()
    
    if not monitoring_points:
        logger.error("未提取到任何监控点配置")
        return 1
    
    # 输出结果
    if args.output:
        # 保存到文件
        output_path = Path(args.output)
        success = extractor.save_to_file(
            monitoring_points,
            output_path,
            include_summary=not args.no_summary,
            pretty=args.pretty
        )
        
        if success:
            print(f"\n✅ 配置已保存到: {output_path}")
            
            # 显示摘要
            if not args.no_summary:
                summary = extractor.generate_summary(monitoring_points)
                print(f"\n📊 配置摘要:")
                print(f"  - 设备类型数: {summary['total_device_types']}")
                print(f"  - 总监控点数: {summary['total_monitoring_points']}")
                print(f"  - 配置阈值的监控点: {summary['threshold_configured_count']}")
                print(f"  - 配置枚举值的监控点: {summary['enum_configured_count']}")
                print(f"\n  变更类型分布:")
                for change_type, count in summary['change_type_distribution'].items():
                    print(f"    - {change_type}: {count}")
        else:
            return 1
    else:
        # 输出到控制台
        output_data = {
            'monitoring_points': monitoring_points
        }
        
        if not args.no_summary:
            output_data['summary'] = extractor.generate_summary(monitoring_points)
        
        if args.pretty:
            print(json.dumps(output_data, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(output_data, ensure_ascii=False))
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

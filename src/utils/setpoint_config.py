"""
设定点监控配置管理模块

功能说明：
- 统一管理设定点监控的配置信息
- 提供配置文件加载和验证功能
- 支持默认配置和动态配置切换
- 集中管理阈值、房间列表、设备类型等配置
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum

from global_const.global_const import static_settings
from utils.loguru_setting import logger


@dataclass
class SetpointThresholds:
    """设定点阈值配置数据类"""

    temperature: float = 0.1  # 温度变化阈值 (°C)
    temperature_diff: float = 0.1  # 温差变化阈值 (°C)
    time_minutes: float = 1.0  # 时间变化阈值 (分钟)
    co2_ppm: float = 50.0  # CO2浓度变化阈值 (ppm)
    humidity_percent: float = 1.0  # 湿度变化阈值 (%)
    light_minutes: float = 1.0  # 补光时间变化阈值 (分钟)
    count: float = 1.0  # 数量变化阈值 (个/天)


class ChangeType(Enum):
    """变更类型枚举"""

    DIGITAL_ON_OFF = "digital_on_off"  # 数字量开关变化 (0->1 或 1->0)
    ANALOG_VALUE = "analog_value"  # 模拟量数值变化
    ENUM_STATE = "enum_state"  # 枚举状态变化
    THRESHOLD_CROSS = "threshold_cross"  # 阈值穿越


class SetpointConfigManager:
    """设定点配置管理器"""

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化配置管理器

        Args:
            config_path: 配置文件路径，None 表示使用默认路径
        """
        self.config_path = config_path or self._get_default_config_path()
        self.config = self._load_config()
        self._validate_config()

        logger.info(
            f"Setpoint config manager initialized with config from: {self.config_path}"
        )

    def _get_default_config_path(self) -> Path:
        """获取默认配置文件路径"""
        return Path(__file__).parent.parent / "configs" / "setpoint_monitor_config.json"

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        try:
            if not self.config_path.exists():
                logger.warning(
                    f"Config file not found: {self.config_path}, using default config"
                )
                return self._get_default_config()

            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)

            logger.info(f"Successfully loaded config from: {self.config_path}")
            return config

        except Exception as e:
            logger.error(f"Failed to load config from {self.config_path}: {e}")
            logger.info("Using default configuration")
            return self._get_default_config()

    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            "default_rooms": ["607", "608", "611", "612"],
            "time_limits": {"max_batch_days": 30, "default_hours_back": 1},
            "database": {
                "table_name": "device_setpoint_changes",
                "batch_size": 1000,
                "required_fields": [
                    "room_id",
                    "device_type",
                    "device_name",
                    "point_name",
                    "change_time",
                    "previous_value",
                    "current_value",
                    "change_type",
                ],
            },
            "thresholds": {
                "air_cooler": {
                    "temp_set": 0.1,
                    "temp_diffset": 0.1,
                    "cyc_on_time": 1.0,
                    "cyc_off_time": 1.0,
                },
                "fresh_air_fan": {
                    "co2_on": 50.0,
                    "co2_off": 50.0,
                    "on": 1.0,
                    "off": 1.0,
                },
                "humidifier": {"on": 1.0, "off": 1.0},
                "grow_light": {"on_mset": 1.0, "off_mset": 1.0},
                "mushroom_info": {"in_num": 1.0, "in_day_num": 1.0},
            },
            "device_types": {
                "air_cooler": {
                    "monitored_points": [
                        "on_off",
                        "temp_set",
                        "temp_diffset",
                        "cyc_on_time",
                        "cyc_off_time",
                        "air_on_off",
                        "hum_on_off",
                        "cyc_on_off",
                    ]
                },
                "fresh_air_fan": {
                    "monitored_points": [
                        "mode",
                        "control",
                        "co2_on",
                        "co2_off",
                        "on",
                        "off",
                    ]
                },
                "humidifier": {"monitored_points": ["mode", "on", "off"]},
                "grow_light": {
                    "monitored_points": [
                        "model",
                        "on_mset",
                        "off_mset",
                        "on_off1",
                        "on_off2",
                        "on_off3",
                        "on_off4",
                        "choose1",
                        "choose2",
                        "choose3",
                        "choose4",
                    ]
                },
                "mushroom_info": {"monitored_points": ["in_num", "in_day_num"]},
            },
            "monitoring": {
                "enable_batch_monitoring": True,
                "enable_real_time_monitoring": True,
                "log_level": "INFO",
                "performance_monitoring": True,
            },
        }

    def _validate_config(self):
        """验证配置文件的完整性"""
        required_sections = [
            "default_rooms",
            "time_limits",
            "database",
            "thresholds",
            "device_types",
        ]

        for section in required_sections:
            if section not in self.config:
                logger.warning(f"Missing config section: {section}")

        # 验证房间列表
        if not isinstance(self.config.get("default_rooms"), list):
            logger.warning("Invalid default_rooms configuration, should be a list")

        # 验证阈值配置
        thresholds = self.config.get("thresholds", {})
        for device_type, device_thresholds in thresholds.items():
            if not isinstance(device_thresholds, dict):
                logger.warning(
                    f"Invalid threshold config for device type: {device_type}"
                )

    def get_default_rooms(self) -> List[str]:
        """
        获取默认房间列表

        优先级：
        1. 静态配置文件中的房间列表
        2. 配置文件中的默认房间列表
        3. 硬编码的备选房间列表

        Returns:
            List[str]: 房间编号列表
        """
        try:
            # 优先从静态配置获取
            rooms_cfg = getattr(static_settings.mushroom, "rooms", {})
            if rooms_cfg and hasattr(rooms_cfg, "keys"):
                rooms = list(rooms_cfg.keys())
                logger.debug(f"Got rooms from static config: {rooms}")
                return rooms
        except Exception as e:
            logger.debug(f"Failed to get rooms from static config: {e}")

        # 使用配置文件中的默认值
        default_rooms = self.config.get("default_rooms", ["607", "608", "611", "612"])
        logger.debug(f"Using default rooms from config: {default_rooms}")
        return default_rooms

    def get_threshold(self, device_type: str, point_alias: str) -> Optional[float]:
        """
        获取指定设备类型和测点的阈值

        Args:
            device_type: 设备类型 (如 'air_cooler')
            point_alias: 测点别名 (如 'temp_set')

        Returns:
            Optional[float]: 阈值，None 表示未配置
        """
        try:
            return self.config["thresholds"][device_type][point_alias]
        except KeyError:
            logger.debug(f"No threshold configured for {device_type}.{point_alias}")
            return None

    def get_monitored_points(self, device_type: str) -> List[str]:
        """
        获取指定设备类型需要监控的测点列表

        Args:
            device_type: 设备类型

        Returns:
            List[str]: 测点别名列表
        """
        try:
            return self.config["device_types"][device_type]["monitored_points"]
        except KeyError:
            logger.warning(
                f"No monitored points configured for device type: {device_type}"
            )
            return []

    def get_all_device_types(self) -> List[str]:
        """获取所有配置的设备类型"""
        return list(self.config.get("device_types", {}).keys())

    def get_database_config(self) -> Dict[str, Any]:
        """获取数据库相关配置"""
        return self.config.get(
            "database",
            {
                "table_name": "device_setpoint_changes",
                "batch_size": 1000,
                "required_fields": [
                    "room_id",
                    "device_type",
                    "device_name",
                    "point_name",
                    "change_time",
                    "previous_value",
                    "current_value",
                    "change_type",
                ],
            },
        )

    def get_time_limits(self) -> Dict[str, int]:
        """获取时间限制配置"""
        return self.config.get(
            "time_limits", {"max_batch_days": 30, "default_hours_back": 1}
        )

    def is_monitoring_enabled(self, monitoring_type: str = "batch_monitoring") -> bool:
        """
        检查指定类型的监控是否启用

        Args:
            monitoring_type: 监控类型 ('batch_monitoring' 或 'real_time_monitoring')

        Returns:
            bool: 是否启用
        """
        monitoring_config = self.config.get("monitoring", {})
        key = f"enable_{monitoring_type}"
        return monitoring_config.get(key, True)

    def get_change_type_config(self, change_type: str) -> Dict[str, Any]:
        """
        获取变更类型配置

        Args:
            change_type: 变更类型

        Returns:
            Dict[str, Any]: 变更类型配置信息
        """
        change_types = self.config.get("change_types", {})
        return change_types.get(change_type, {})

    def reload_config(self) -> bool:
        """
        重新加载配置文件

        Returns:
            bool: 重新加载是否成功
        """
        try:
            old_config = self.config.copy()
            self.config = self._load_config()
            self._validate_config()

            logger.info("Configuration reloaded successfully")

            # 检查关键配置是否有变化
            if old_config.get("default_rooms") != self.config.get("default_rooms"):
                logger.info("Default rooms configuration changed")

            if old_config.get("thresholds") != self.config.get("thresholds"):
                logger.info("Threshold configuration changed")

            return True

        except Exception as e:
            logger.error(f"Failed to reload configuration: {e}")
            return False

    def save_config(self, config_path: Optional[str] = None) -> bool:
        """
        保存当前配置到文件

        Args:
            config_path: 保存路径，None 表示使用当前配置文件路径

        Returns:
            bool: 保存是否成功
        """
        save_path = Path(config_path) if config_path else self.config_path

        try:
            # 确保目录存在
            save_path.parent.mkdir(parents=True, exist_ok=True)

            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)

            logger.info(f"Configuration saved to: {save_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to save configuration to {save_path}: {e}")
            return False

    def update_threshold(
        self, device_type: str, point_alias: str, threshold: float
    ) -> bool:
        """
        更新指定测点的阈值

        Args:
            device_type: 设备类型
            point_alias: 测点别名
            threshold: 新阈值

        Returns:
            bool: 更新是否成功
        """
        try:
            if "thresholds" not in self.config:
                self.config["thresholds"] = {}

            if device_type not in self.config["thresholds"]:
                self.config["thresholds"][device_type] = {}

            old_threshold = self.config["thresholds"][device_type].get(point_alias)
            self.config["thresholds"][device_type][point_alias] = threshold

            logger.info(
                f"Updated threshold for {device_type}.{point_alias}: {old_threshold} -> {threshold}"
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to update threshold for {device_type}.{point_alias}: {e}"
            )
            return False

    def get_config_summary(self) -> Dict[str, Any]:
        """
        获取配置摘要信息

        Returns:
            Dict[str, Any]: 配置摘要
        """
        summary = {
            "config_path": str(self.config_path),
            "default_rooms_count": len(self.get_default_rooms()),
            "device_types_count": len(self.get_all_device_types()),
            "total_monitored_points": 0,
            "monitoring_enabled": {
                "batch": self.is_monitoring_enabled("batch_monitoring"),
                "real_time": self.is_monitoring_enabled("real_time_monitoring"),
            },
        }

        # 统计监控点总数
        for device_type in self.get_all_device_types():
            points = self.get_monitored_points(device_type)
            summary["total_monitored_points"] += len(points)

        return summary


# 全局配置管理器实例
_config_manager = None


def get_setpoint_config_manager() -> SetpointConfigManager:
    """
    获取全局设定点配置管理器实例（单例模式）

    Returns:
        SetpointConfigManager: 配置管理器实例
    """
    global _config_manager
    if _config_manager is None:
        _config_manager = SetpointConfigManager()
    return _config_manager


def reload_setpoint_config() -> bool:
    """
    重新加载设定点配置

    Returns:
        bool: 重新加载是否成功
    """
    global _config_manager
    if _config_manager is not None:
        return _config_manager.reload_config()
    else:
        _config_manager = SetpointConfigManager()
        return True


if __name__ == "__main__":
    # 测试配置管理器
    print("🔧 测试设定点配置管理器")
    print("=" * 50)

    # 创建配置管理器
    config_manager = SetpointConfigManager()

    # 显示配置摘要
    summary = config_manager.get_config_summary()
    print(f"\n📋 配置摘要:")
    print(f"  配置文件: {summary['config_path']}")
    print(f"  默认房间数: {summary['default_rooms_count']}")
    print(f"  设备类型数: {summary['device_types_count']}")
    print(f"  监控点总数: {summary['total_monitored_points']}")
    print(f"  批量监控: {'启用' if summary['monitoring_enabled']['batch'] else '禁用'}")
    print(
        f"  实时监控: {'启用' if summary['monitoring_enabled']['real_time'] else '禁用'}"
    )

    # 显示房间列表
    rooms = config_manager.get_default_rooms()
    print(f"\n🏠 默认房间列表: {rooms}")

    # 显示设备类型和监控点
    print(f"\n🔧 设备类型和监控点:")
    for device_type in config_manager.get_all_device_types():
        points = config_manager.get_monitored_points(device_type)
        print(f"  {device_type}: {len(points)} 个监控点")
        for point in points[:3]:  # 显示前3个
            threshold = config_manager.get_threshold(device_type, point)
            threshold_info = f" (阈值: {threshold})" if threshold else ""
            print(f"    • {point}{threshold_info}")
        if len(points) > 3:
            print(f"    ... 还有 {len(points) - 3} 个监控点")

    # 测试阈值获取
    print(f"\n🎯 阈值测试:")
    test_cases = [
        ("air_cooler", "temp_set"),
        ("fresh_air_fan", "co2_on"),
        ("humidifier", "on"),
        ("grow_light", "on_mset"),
        ("unknown_device", "unknown_point"),
    ]

    for device_type, point_alias in test_cases:
        threshold = config_manager.get_threshold(device_type, point_alias)
        status = f"✅ {threshold}" if threshold is not None else "❌ 未配置"
        print(f"  {device_type}.{point_alias}: {status}")

    # 测试配置更新
    print(f"\n🔄 配置更新测试:")
    success = config_manager.update_threshold("air_cooler", "temp_set", 0.8)
    print(f"  更新阈值: {'成功' if success else '失败'}")

    new_threshold = config_manager.get_threshold("air_cooler", "temp_set")
    print(f"  新阈值: {new_threshold}")

    print(f"\n✅ 配置管理器测试完成")

"""鹿茸菇种植环境参数调节 Skill 引擎。

面向大模型场景的可复用能力模块，包含：
- 触发条件（growth_day/season/env）
- 输入输出规范（monitoring_points JSON）
- 执行步骤（匹配 -> 约束校正 -> 反馈）
- 约束/工具调用（DB读取最新环境统计）
- 评估反馈（命中与修正统计）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text

from global_const.global_const import pgsql_engine
from utils.loguru_setting import logger


@dataclass
class SkillContext:
    room_id: str
    analysis_time: datetime
    growth_day: Optional[int] = None
    season: Optional[str] = None
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    co2: Optional[float] = None
    realtime_temperature: Optional[float] = None
    realtime_humidity: Optional[float] = None
    realtime_co2: Optional[float] = None
    realtime_light_mode: Optional[float] = None
    realtime_light_on_mset: Optional[float] = None
    realtime_light_off_mset: Optional[float] = None
    realtime_data_time: Optional[datetime] = None
    recent_setpoint_change_count_6h: int = 0
    recent_setpoint_changes: List[Dict[str, Any]] = field(default_factory=list)


class CultivationSkillEngine:
    """鹿茸菇环境调节 Skill 引擎。"""

    def __init__(self, config_path: Path, enable_kb_prior: bool = True):
        self.config_path = Path(config_path)
        self.enable_kb_prior = bool(enable_kb_prior)
        self.skill_library: Dict[str, Any] = {}
        self.skills: List[Dict[str, Any]] = []
        self._load_skill_library()

    def io_contract(self) -> Dict[str, Any]:
        return {
            "trigger": [
                "growth_day",
                "season",
                "temperature",
                "humidity",
                "co2",
                "realtime_light",
                "recent_setpoint_changes",
            ],
            "input": {
                "room_id": "str",
                "analysis_time": "datetime",
                "monitoring_points": "dict",
            },
            "output": {
                "monitoring_points": "dict",
                "skill_feedback": "dict",
            },
            "steps": [
                "load_skill_library",
                "build_context_from_db",
                "match_trigger_conditions",
                "apply_action_constraints",
                "emit_feedback",
            ],
            "tools": [
                "postgresql:mushroom_env_daily_stats",
                "postgresql:mushroom_embedding",
                "postgresql:device_setpoint_changes",
            ],
        }

    def _load_cluster_kb_priors(
        self, context: SkillContext
    ) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """加载聚类知识库中与当前生长天接近的点位先验区间。"""
        if not self.enable_kb_prior:
            return {}

        target_day = context.growth_day
        priors: Dict[Tuple[str, str], Dict[str, Any]] = {}

        try:
            with pgsql_engine.connect() as conn:
                run_row = conn.execute(
                    text(
                        """
                        SELECT id
                        FROM control_strategy_kb_runs
                        WHERE kb_type = 'cluster'
                        ORDER BY is_active DESC, generated_at DESC
                        LIMIT 1
                        """
                    )
                ).fetchone()

                if not run_row:
                    return {}

                run_id = run_row[0]
                if target_day is None:
                    rows = conn.execute(
                        text(
                            """
                            SELECT device_type, point_key, value_median, value_p25, value_p75,
                                   sample_days, growth_window, preferred_change_type, preferred_hour
                            FROM control_strategy_kb_cluster_rules
                            WHERE run_id = :run_id
                            ORDER BY sample_days DESC NULLS LAST
                            LIMIT 200
                            """
                        ),
                        {"run_id": run_id},
                    ).fetchall()
                else:
                    rows = conn.execute(
                        text(
                            """
                            SELECT device_type, point_key, value_median, value_p25, value_p75,
                                   sample_days, growth_window, preferred_change_type, preferred_hour,
                                   ABS(COALESCE(growth_day_median, :target_day) - :target_day) AS day_gap
                            FROM control_strategy_kb_cluster_rules
                            WHERE run_id = :run_id
                              AND COALESCE(growth_day_min, :target_day) <= :target_day
                              AND COALESCE(growth_day_max, :target_day) >= :target_day
                            ORDER BY day_gap ASC, sample_days DESC NULLS LAST
                            LIMIT 200
                            """
                        ),
                        {"run_id": run_id, "target_day": int(target_day)},
                    ).fetchall()

            for row in rows:
                device_type = str(row[0]) if row[0] is not None else None
                point_key = str(row[1]) if row[1] is not None else None
                if not device_type or not point_key:
                    continue

                key = (
                    self._normalize_device_type(device_type),
                    self._normalize_point_key(point_key),
                )
                sample_days = int(row[5]) if row[5] is not None else 0

                # 若同点位有多条记录，保留样本天数更高的一条
                if key in priors and sample_days <= int(
                    priors[key].get("sample_days", 0)
                ):
                    continue

                priors[key] = {
                    "kb_value_median": self._to_float(row[2]),
                    "kb_low": self._to_float(row[3]),
                    "kb_high": self._to_float(row[4]),
                    "sample_days": sample_days,
                    "growth_window": row[6],
                    "preferred_change_type": row[7],
                    "preferred_hour": self._to_float(row[8]),
                }

        except Exception as exc:
            logger.warning(f"[SKILL] 加载聚类知识库先验失败: {exc}")
            return {}

        if priors:
            logger.info(f"[SKILL] 已加载聚类知识库先验点位: {len(priors)}")
        return priors

    def _load_skill_library(self) -> None:
        if not self.config_path.exists():
            logger.warning(f"[SKILL] skill库文件不存在: {self.config_path}")
            self.skill_library = {}
            self.skills = []
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as file:
                self.skill_library = json.load(file)
            self.skills = self.skill_library.get("skills", []) or []
            logger.info(
                f"[SKILL] 已加载skill库: version={self.skill_library.get('skill_library_version')}, count={len(self.skills)}"
            )
        except Exception as exc:
            logger.error(f"[SKILL] skill库加载失败: {exc}")
            self.skill_library = {}
            self.skills = []

    def build_context_from_db(
        self, room_id: str, analysis_time: datetime
    ) -> SkillContext:
        season = self._infer_season(analysis_time.month)
        context = SkillContext(
            room_id=room_id, analysis_time=analysis_time, season=season
        )

        try:
            with pgsql_engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT in_day_num, temp_median, humidity_median, co2_median
                        FROM mushroom_env_daily_stats
                        WHERE room_id = :room_id
                          AND stat_date <= :stat_date
                        ORDER BY stat_date DESC
                        LIMIT 1
                        """
                    ),
                    {"room_id": str(room_id), "stat_date": analysis_time.date()},
                ).fetchone()

                realtime_row = conn.execute(
                    text(
                        """
                        SELECT growth_day, env_sensor_status, light_config, collection_datetime
                        FROM mushroom_embedding
                        WHERE room_id = :room_id
                          AND collection_datetime <= :analysis_time
                        ORDER BY collection_datetime DESC
                        LIMIT 1
                        """
                    ),
                    {
                        "room_id": str(room_id),
                        "analysis_time": analysis_time,
                    },
                ).fetchone()

                window_start = analysis_time - timedelta(hours=6)
                recent_count_row = conn.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM device_setpoint_changes
                        WHERE room_id = :room_id
                          AND change_time >= :window_start
                          AND change_time <= :analysis_time
                        """
                    ),
                    {
                        "room_id": str(room_id),
                        "window_start": window_start,
                        "analysis_time": analysis_time,
                    },
                ).fetchone()

                recent_change_rows = conn.execute(
                    text(
                        """
                        SELECT device_type, point_name, previous_value, current_value, change_time
                        FROM device_setpoint_changes
                        WHERE room_id = :room_id
                          AND change_time >= :window_start
                          AND change_time <= :analysis_time
                        ORDER BY change_time DESC
                        LIMIT 8
                        """
                    ),
                    {
                        "room_id": str(room_id),
                        "window_start": window_start,
                        "analysis_time": analysis_time,
                    },
                ).fetchall()

            if row:
                context.growth_day = int(row[0]) if row[0] is not None else None
                context.temperature = float(row[1]) if row[1] is not None else None
                context.humidity = float(row[2]) if row[2] is not None else None
                context.co2 = float(row[3]) if row[3] is not None else None

            if realtime_row:
                realtime_growth_day = (
                    int(realtime_row[0]) if realtime_row[0] is not None else None
                )
                if realtime_growth_day is not None:
                    context.growth_day = realtime_growth_day

                env_status = self._to_dict(realtime_row[1])
                light_config = self._to_dict(realtime_row[2])

                context.realtime_data_time = realtime_row[3]
                context.realtime_temperature = self._pick_numeric(
                    env_status, ["temperature", "temp", "tem"]
                )
                context.realtime_humidity = self._pick_numeric(
                    env_status, ["humidity", "hum"]
                )
                context.realtime_co2 = self._pick_numeric(env_status, ["co2"])
                context.realtime_light_mode = self._pick_numeric(
                    light_config, ["model", "mode"]
                )
                context.realtime_light_on_mset = self._pick_numeric(
                    light_config, ["on_mset", "onmset"]
                )
                context.realtime_light_off_mset = self._pick_numeric(
                    light_config, ["off_mset", "offmset"]
                )

                # 实时数据优先于日统计，保证触发条件贴近现场状态
                if context.realtime_temperature is not None:
                    context.temperature = context.realtime_temperature
                if context.realtime_humidity is not None:
                    context.humidity = context.realtime_humidity
                if context.realtime_co2 is not None:
                    context.co2 = context.realtime_co2

            context.recent_setpoint_change_count_6h = (
                int(recent_count_row[0])
                if recent_count_row and recent_count_row[0]
                else 0
            )
            context.recent_setpoint_changes = [
                {
                    "device_type": str(change_row[0])
                    if change_row[0] is not None
                    else None,
                    "point_name": str(change_row[1])
                    if change_row[1] is not None
                    else None,
                    "previous_value": self._to_float(change_row[2]),
                    "current_value": self._to_float(change_row[3]),
                    "change_time": (
                        change_row[4].isoformat()
                        if hasattr(change_row[4], "isoformat")
                        else str(change_row[4])
                    ),
                }
                for change_row in (recent_change_rows or [])
            ]
        except Exception as exc:
            logger.warning(f"[SKILL] 读取上下文失败 room_id={room_id}: {exc}")

        return context

    def match_skills(self, context: SkillContext) -> List[Dict[str, Any]]:
        matched: List[Dict[str, Any]] = []

        for skill in self.skills:
            status = str(skill.get("status", "active")).lower()
            if status != "active":
                continue

            conditions = skill.get("applicable_conditions", {}) or {}
            if not self._match_growth_day(
                context.growth_day, conditions.get("growth_day_range")
            ):
                continue
            if not self._match_season(context.season, conditions.get("season")):
                continue
            if not self._match_env(context, conditions.get("env", {})):
                continue

            matched.append(skill)

        matched.sort(
            key=lambda item: self._priority_score(item.get("priority")), reverse=True
        )
        return matched

    def apply(
        self,
        decision_data: Dict[str, Any],
        context: SkillContext,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        matches = self.match_skills(context)
        kb_priors = self._load_cluster_kb_priors(context)

        feedback: Dict[str, Any] = {
            "skill_library_version": self.skill_library.get("skill_library_version"),
            "kb_prior_enabled": self.enable_kb_prior,
            "kb_prior_points": len(kb_priors),
            "kb_prior_used": 0,
            "trigger_context": {
                "growth_day": context.growth_day,
                "season": context.season,
                "temperature": context.temperature,
                "humidity": context.humidity,
                "co2": context.co2,
                "realtime": {
                    "data_time": (
                        context.realtime_data_time.isoformat()
                        if context.realtime_data_time
                        else None
                    ),
                    "temperature": context.realtime_temperature,
                    "humidity": context.realtime_humidity,
                    "co2": context.realtime_co2,
                    "light_mode": context.realtime_light_mode,
                    "light_on_mset": context.realtime_light_on_mset,
                    "light_off_mset": context.realtime_light_off_mset,
                },
                "setpoint_changes": {
                    "window_hours": 6,
                    "count": context.recent_setpoint_change_count_6h,
                    "latest": context.recent_setpoint_changes,
                },
            },
            "matched_skill_ids": [item.get("skill_id") for item in matches],
            "matched_count": len(matches),
            "constraint_corrections": 0,
            "correction_details": [],
        }

        if not matches:
            return decision_data, feedback

        monitoring_points = decision_data.get("monitoring_points", {})
        devices_map = monitoring_points.get("devices", {})
        if not isinstance(devices_map, dict):
            return decision_data, feedback

        # 按优先级最多执行前2个skill，避免过度改写
        for skill in matches[:2]:
            for action in skill.get("actions") or []:
                device_type = action.get("device_type")
                point_alias = action.get("point_alias")
                target_range = action.get("target_range")
                step = action.get("step")

                if (
                    not device_type
                    or not point_alias
                    or not isinstance(target_range, list)
                    or len(target_range) != 2
                ):
                    continue

                device_list = devices_map.get(device_type) or []
                if not isinstance(device_list, list):
                    continue

                for device in device_list:
                    point_list = device.get("point_list") or []
                    for point in point_list:
                        if point.get("point_alias") != point_alias:
                            continue

                        old_value = self._to_float(point.get("old"))
                        new_value = self._to_float(point.get("new"))
                        if new_value is None:
                            continue

                        low, high = float(target_range[0]), float(target_range[1])
                        kb_prior = kb_priors.get(
                            (
                                self._normalize_device_type(device_type),
                                self._normalize_point_key(point_alias),
                            )
                        )
                        merged_low, merged_high = self._merge_skill_and_kb_range(
                            skill_low=low,
                            skill_high=high,
                            kb_prior=kb_prior,
                        )
                        adjusted = self._clamp_and_step(new_value, low, high, step)
                        adjusted = self._clamp_and_step(
                            adjusted, merged_low, merged_high, step
                        )

                        if abs(adjusted - new_value) < 1e-9:
                            continue

                        point["new"] = self._preserve_numeric_type(
                            point.get("new"), adjusted
                        )
                        point["change"] = self._compute_change(
                            point, old_value, adjusted
                        )

                        reason = point.get("reason")
                        suffix = f"skill:{skill.get('skill_id')}"
                        if kb_prior:
                            suffix = f"{suffix}; kb_prior"
                            feedback["kb_prior_used"] += 1
                        point["reason"] = f"{reason}; {suffix}" if reason else suffix

                        feedback["constraint_corrections"] += 1
                        feedback["correction_details"].append(
                            {
                                "skill_id": skill.get("skill_id"),
                                "device_type": device_type,
                                "device_alias": device.get("device_alias"),
                                "point_alias": point_alias,
                                "from": new_value,
                                "to": adjusted,
                                "target_range": [merged_low, merged_high],
                                "skill_target_range": [low, high],
                                "kb_prior": kb_prior,
                            }
                        )

        decision_data["skill_feedback"] = feedback
        return decision_data, feedback

    @staticmethod
    def _infer_season(month: int) -> str:
        if month in {3, 4, 5}:
            return "spring"
        if month in {6, 7, 8}:
            return "summer"
        if month in {9, 10, 11}:
            return "autumn"
        return "winter"

    @staticmethod
    def _priority_score(priority: Optional[str]) -> int:
        mapping = {"high": 3, "medium": 2, "low": 1}
        return mapping.get(str(priority).lower(), 0)

    @staticmethod
    def _normalize_device_type(device_type: Any) -> str:
        raw = str(device_type or "").strip().lower()
        mapping = {
            "fresh_fan": "fresh_air_fan",
            "freshairfan": "fresh_air_fan",
            "fresh_air_fan": "fresh_air_fan",
            "aircooler": "air_cooler",
            "air_cooler": "air_cooler",
            "growlight": "grow_light",
            "grow_light": "grow_light",
            "humidifier": "humidifier",
        }
        key = raw.replace("-", "_").replace(" ", "_")
        return mapping.get(key, key)

    @staticmethod
    def _normalize_point_key(point_key: Any) -> str:
        raw = str(point_key or "").strip().lower()
        key = raw.replace("-", "_").replace(" ", "_")
        key = key.replace("__", "_")
        mapping = {
            "temp_diff_set": "temp_diffset",
            "tem_diff_set": "temp_diffset",
            "tempdiffset": "temp_diffset",
            "temp_set": "temp_set",
            "tem_set": "temp_set",
            "co2on": "co2_on",
            "co2_off": "co2_off",
            "co2off": "co2_off",
            "co2_on": "co2_on",
            "onoff": "on_off",
            "on_off": "on_off",
            "cyc_onoff": "cyc_on_off",
            "cyc_on_off": "cyc_on_off",
            "cyc_ontime": "cyc_on_time",
            "cyc_on_time": "cyc_on_time",
            "cyc_offtime": "cyc_off_time",
            "cyc_off_time": "cyc_off_time",
            "air_onoff": "air_on_off",
            "air_on_off": "air_on_off",
            "hum_onoff": "hum_on_off",
            "hum_on_off": "hum_on_off",
            "temp_diffset": "temp_diffset",
        }
        return mapping.get(key, key)

    @staticmethod
    def _match_growth_day(value: Optional[int], value_range: Any) -> bool:
        if value is None:
            return False
        if not isinstance(value_range, list) or len(value_range) != 2:
            return True
        return int(value_range[0]) <= int(value) <= int(value_range[1])

    @staticmethod
    def _match_season(value: Optional[str], seasons: Any) -> bool:
        if not value:
            return False
        if not isinstance(seasons, list) or not seasons:
            return True
        lowered = {str(item).lower() for item in seasons}
        return "all" in lowered or str(value).lower() in lowered

    @staticmethod
    def _match_env(context: SkillContext, env_rules: Dict[str, Any]) -> bool:
        def in_range(current: Optional[float], bounds: Any) -> bool:
            if current is None:
                return False
            if not isinstance(bounds, list) or len(bounds) != 2:
                return True
            return float(bounds[0]) <= float(current) <= float(bounds[1])

        return (
            in_range(context.temperature, env_rules.get("temperature"))
            and in_range(context.humidity, env_rules.get("humidity"))
            and in_range(context.co2, env_rules.get("co2"))
        )

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _to_dict(value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                return {}
        return {}

    @classmethod
    def _pick_numeric(cls, data: Dict[str, Any], keys: List[str]) -> Optional[float]:
        if not isinstance(data, dict):
            return None
        for key in keys:
            if key in data:
                value = cls._to_float(data.get(key))
                if value is not None:
                    return value
        return None

    @staticmethod
    def _clamp_and_step(value: float, low: float, high: float, step: Any) -> float:
        adjusted = min(max(float(value), low), high)
        try:
            step_value = float(step) if step is not None else None
        except Exception:
            step_value = None

        if not step_value or step_value <= 0:
            return adjusted

        base = low
        snapped = round((adjusted - base) / step_value) * step_value + base
        return min(max(snapped, low), high)

    @staticmethod
    def _merge_skill_and_kb_range(
        skill_low: float,
        skill_high: float,
        kb_prior: Optional[Dict[str, Any]],
    ) -> Tuple[float, float]:
        if not kb_prior:
            return float(skill_low), float(skill_high)

        kb_low = kb_prior.get("kb_low")
        kb_high = kb_prior.get("kb_high")
        sample_days = int(kb_prior.get("sample_days", 0) or 0)

        # 无有效KB区间时保持skill原范围
        if kb_low is None or kb_high is None or kb_low > kb_high:
            return float(skill_low), float(skill_high)

        kb_low_f = float(kb_low)
        kb_high_f = float(kb_high)

        # 样本不足时仅作温和收敛；样本充分时优先取交集
        if sample_days >= 8:
            merged_low = max(float(skill_low), kb_low_f)
            merged_high = min(float(skill_high), kb_high_f)
            if merged_low <= merged_high:
                return merged_low, merged_high

        # 无交集或样本不足：用两者中点折中，避免剧烈跳变
        center_skill = (float(skill_low) + float(skill_high)) / 2
        center_kb = (kb_low_f + kb_high_f) / 2
        center = (center_skill + center_kb) / 2
        half_width = min(
            (float(skill_high) - float(skill_low)) / 2,
            (kb_high_f - kb_low_f) / 2,
        )
        if half_width <= 0:
            return float(skill_low), float(skill_high)

        merged_low = center - half_width
        merged_high = center + half_width
        return merged_low, merged_high

    @staticmethod
    def _preserve_numeric_type(raw_value: Any, value: float) -> Any:
        if isinstance(raw_value, int):
            return int(round(value))
        return float(value)

    @staticmethod
    def _compute_change(
        point: Dict[str, Any], old_value: Optional[float], new_value: float
    ) -> bool:
        if old_value is None:
            return True
        threshold = point.get("threshold")
        try:
            if threshold is None:
                return abs(new_value - old_value) > 1e-9
            return abs(new_value - old_value) >= float(threshold)
        except Exception:
            return abs(new_value - old_value) > 1e-9

from loguru import logger


PARAMETER_MAPPINGS = {
    "air_cooler": {
        "tem_set": "temp_set",
        "tem_diff_set": "temp_diffset",
        "cyc_on_off": "cyc_on_off",
        "cyc_on_time": "cyc_on_time",
        "cyc_off_time": "cyc_off_time",
        "ar_on_off": "air_on_off",
        "hum_on_off": "hum_on_off",
        "on_off": "on_off",
    },
    "fresh_air_fan": {
        "model": "mode",
        "control": "control",
        "co2_on": "co2_on",
        "co2_off": "co2_off",
        "on": "on",
        "off": "off",
    },
    "humidifier": {"model": "mode", "on": "on", "off": "off"},
    "grow_light": {
        "model": "model",
        "on_mset": "on_mset",
        "off_mset": "off_mset",
        "on_off_1": "on_off1",
        "on_off_2": "on_off2",
        "on_off_3": "on_off3",
        "on_off_4": "on_off4",
        "choose_1": "choose1",
        "choose_2": "choose2",
        "choose_3": "choose3",
        "choose_4": "choose4",
    },
}


def map_parameter_to_point_alias(
    device_type: str,
    parameter_name: str,
    supported_points: set[str] | list[str] | tuple[str, ...],
) -> str | None:
    device_mapping = PARAMETER_MAPPINGS.get(device_type, {})
    point_alias = device_mapping.get(parameter_name)

    if point_alias:
        if point_alias in supported_points:
            return point_alias
        logger.debug(
            "[DecisionAnalyzer] Point alias '{}' not supported for device type '{}'",
            point_alias,
            device_type,
        )
        return None

    logger.debug(
        "[DecisionAnalyzer] No mapping found for parameter '{}' in device type '{}'",
        parameter_name,
        device_type,
    )
    return None
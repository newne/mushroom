"""运行环境相关工具。"""

import os


def str_to_bool(value: str) -> bool:
    """Convert string to boolean, treating 'true' (case insensitive) as True."""
    return str(value).lower() == "true"


def get_environment() -> str:
    """获取当前环境。"""
    return (
        "production" if str_to_bool(os.environ.get("prod", "false")) else "development"
    )


env = get_environment()

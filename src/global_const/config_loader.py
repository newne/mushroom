"""配置加载基础设施。"""

from dynaconf import Dynaconf
from loguru import logger

from .environment import env
from .paths import BASE_DIR

config_dir_path = BASE_DIR / "configs"
logger.info(f"[9.9.1] 已加载配置文件目录：{config_dir_path}")

settings = Dynaconf(
    root_path=str(BASE_DIR),
    envvar_prefix="mushroom_environments",
    environments=True,
    env=env,
    merge_enabled=True,
    settings_files=[
        str(config_dir_path / "settings.toml"),
        str(config_dir_path / ".secrets.toml"),
    ],
)

static_settings = Dynaconf(
    root_path=str(BASE_DIR),
    envvar_prefix="mushroom_environments",
    settings_files=[str(config_dir_path / "static_config.json")],
)

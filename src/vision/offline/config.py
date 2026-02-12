
import os
import re
import sys
from typing import Dict, Any
from pathlib import Path

# Try to import standard toml lib, fallback to manual parsing if necessary
# to avoid external dependencies if tomli is not installed
try:
    import tomllib as toml
except ImportError:
    try:
        import tomli as toml
    except ImportError:
        toml = None

class Config:
    def __init__(self, profile: str = None):
        self.settings_file = os.environ.get("LLAMA_SETTINGS_FILE", "src/configs/settings.toml")
        if profile:
            self.profile = profile
        else:
            self.profile = os.environ.get("LLAMA_PROFILE", "development.llama")
        self._full_data = self._load_full_data()
        self._config = self._extract_profile()
        
        # Auto-configure environment for MLflow/OpenAI SDKs
        os.environ["OPENAI_API_BASE"] = self.base_url
        os.environ["OPENAI_API_KEY"] = self.api_key

    def _load_full_data(self) -> Dict[str, Any]:
        path = Path(self.settings_file)
        if not path.exists():
            # Fallback to looking in current directory if src/configs path fails
            path = Path("settings.llama")
            if not path.exists():
                raise FileNotFoundError(f"Settings file not found at {self.settings_file} or settings.llama")

        if toml:
            with open(path, "rb") as f:
                data = toml.load(f)
        else:
            data = self._manual_toml_parse(path)
        return data

    def _extract_profile(self) -> Dict[str, Any]:
        # Extract profile data
        # Handle nested keys like [development.llama]
        parts = self.profile.split(".")
        section = self._full_data
        for part in parts:
            section = section.get(part, {})
        
        if not section:
            raise ValueError(f"Profile {self.profile} not found in {self.settings_file}")

        return section

    # Renamed from _load_config to match new structure
    def _load_config(self) -> Dict[str, Any]:
         return self._extract_profile()

    def _manual_toml_parse(self, path: Path) -> Dict[str, Any]:
        """Simple regex-based TOML parser for specific format to avoid dependencies"""
        data = {}
        current_section = None
        
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                
                # Section
                match = re.match(r"^\[(.*)\]$", line)
                if match:
                    section_name = match.group(1)
                    parts = section_name.split(".")
                    current = data
                    for part in parts[:-1]:
                        current = current.setdefault(part, {})
                    current_section = current.setdefault(parts[-1], {})
                    continue
                
                # Key-Value
                match = re.match(r"^(\w+)\s*=\s*(.*)$", line)
                if match and current_section is not None:
                    key = match.group(1)
                    value = match.group(2).strip('"\'')
                    # Basic type conversion
                    if value.lower() == "true": value = True
                    elif value.lower() == "false": value = False
                    elif value.isdigit(): value = int(value)
                    current_section[key] = value
                    
        return data

    @property
    def base_url(self) -> str:
        host = self._config.get("llama_host", "localhost")
        port = self._config.get("llama_port", "8000")
        template = self._config.get("llama_completions", "http://{0}:{1}/v1/chat/completions")
        # Extract base_url from completions url (remove /chat/completions for OpenAI client)
        # However, OpenAI client expects base_url to point to /v1 usually.
        # The user provided template: http://{0}:{1}/v1/chat/completions
        # OpenAI client base_url should be http://{0}:{1}/v1
        
        full_url = template.format(host, port)
        if "/chat/completions" in full_url:
            return full_url.replace("/chat/completions", "")
        return full_url

    @property
    def model(self) -> str:
        return self._config.get("model", "qwen3-vl-2b-instruct")

    @property
    def api_key(self) -> str:
        return self._config.get("api_key", "EMPTY")

    @property
    def mlflow_tracking_uri(self) -> str:
        # Infer environment from profile (e.g. development.llama_vl -> development)
        parts = self.profile.split(".")
        env = parts[0] if parts else "development"
        
        host = "localhost"
        port = "5000"
        
        try:
            mlflow_conf = self._full_data.get(env, {}).get("mlflow", {})
            host = mlflow_conf.get("host", "localhost")
            port = mlflow_conf.get("port", "5000")
        except:
            pass
            
        return f"http://{host}:{port}"

config = Config()

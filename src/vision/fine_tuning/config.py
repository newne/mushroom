"""
实验配置系统

提供 YAML 配置加载、覆盖与结构化配置对象。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


try:
    import yaml
except Exception as exc:  # pragma: no cover
    raise RuntimeError("缺少 PyYAML 依赖，请先安装 pyyaml") from exc


@dataclass
class ModelConfig:
    model_name_or_path: str = "openai/clip-vit-base-patch32"
    cache_dir: Optional[str] = None
    use_lora: bool = False
    lora_r: int = 8
    lora_alpha: int = 16
    lora_dropout: float = 0.1
    lora_target_modules: List[str] = field(default_factory=lambda: ["q_proj", "k_proj", "v_proj", "out_proj"])
    freeze_vision: bool = False
    freeze_text: bool = False
    freeze_projection: bool = False
    logit_scale_init: Optional[float] = None


@dataclass
class DataConfig:
    train_annotations: str = ""
    val_annotations: str = ""
    test_annotations: str = ""
    image_root: str = ""
    batch_size: int = 32
    num_workers: int = 4
    max_text_length: int = 77


@dataclass
class AugmentConfig:
    image_size: int = 224
    random_crop_scale: List[float] = field(default_factory=lambda: [0.8, 1.0])
    color_jitter: List[float] = field(default_factory=lambda: [0.2, 0.2, 0.2])
    hflip_prob: float = 0.5
    text_synonym_prob: float = 0.1
    text_max_replacements: int = 2
    synonym_map_path: Optional[str] = None


@dataclass
class OptimConfig:
    lr: float = 5e-6
    weight_decay: float = 0.01
    betas: List[float] = field(default_factory=lambda: [0.9, 0.98])


@dataclass
class SchedulerConfig:
    warmup_steps: int = 200
    total_steps: int = 10000
    cosine_min_lr: float = 1e-7


@dataclass
class TrainConfig:
    epochs: int = 10
    grad_accum_steps: int = 1
    amp: bool = True
    seed: int = 42
    output_dir: str = "outputs/clip_fine_tuning"
    log_interval: int = 10
    eval_interval: int = 1
    early_stopping_patience: int = 5
    resume_from: Optional[str] = None
    save_every: int = 1
    max_train_samples: Optional[int] = None
    max_val_samples: Optional[int] = None


@dataclass
class EvalConfig:
    batch_size: int = 64
    num_workers: int = 4
    recall_k: List[int] = field(default_factory=lambda: [1, 5, 10])
    zero_shot_prompts: List[str] = field(default_factory=lambda: ["a photo of {label}"])
    classification_labels: List[str] = field(default_factory=list)
    tsne_samples: int = 500
    complexity_num_runs: int = 30


@dataclass
class DistributedConfig:
    backend: str = "nccl"
    find_unused_parameters: bool = False


@dataclass
class VisualizationConfig:
    tsne_perplexity: int = 30
    tsne_random_state: int = 42
    attention_output_dir: str = "outputs/clip_fine_tuning/attention"
    tsne_output_dir: str = "outputs/clip_fine_tuning/tsne"


@dataclass
class ExperimentConfig:
    model: ModelConfig = field(default_factory=ModelConfig)
    data: DataConfig = field(default_factory=DataConfig)
    augment: AugmentConfig = field(default_factory=AugmentConfig)
    optim: OptimConfig = field(default_factory=OptimConfig)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    train: TrainConfig = field(default_factory=TrainConfig)
    eval: EvalConfig = field(default_factory=EvalConfig)
    distributed: DistributedConfig = field(default_factory=DistributedConfig)
    visualization: VisualizationConfig = field(default_factory=VisualizationConfig)


def load_experiment_config(path: str) -> ExperimentConfig:
    """加载 YAML 并构建 ExperimentConfig。"""
    config_dict = load_yaml_config(path)
    return build_experiment_config(config_dict)


def load_yaml_config(path: str) -> Dict[str, Any]:
    """读取 YAML 配置为字典。"""
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_experiment_config(config_dict: Dict[str, Any]) -> ExperimentConfig:
    """将字典配置映射为结构化 ExperimentConfig。"""
    return ExperimentConfig(
        model=_build_section(ModelConfig, config_dict.get("model", {})),
        data=_build_section(DataConfig, config_dict.get("data", {})),
        augment=_build_section(AugmentConfig, config_dict.get("augment", {})),
        optim=_build_section(OptimConfig, config_dict.get("optim", {})),
        scheduler=_build_section(SchedulerConfig, config_dict.get("scheduler", {})),
        train=_build_section(TrainConfig, config_dict.get("train", {})),
        eval=_build_section(EvalConfig, config_dict.get("eval", {})),
        distributed=_build_section(DistributedConfig, config_dict.get("distributed", {})),
        visualization=_build_section(VisualizationConfig, config_dict.get("visualization", {})),
    )


def apply_overrides(config_dict: Dict[str, Any], overrides: List[str]) -> Dict[str, Any]:
    """应用命令行 override 选项到配置字典。"""
    updated = json.loads(json.dumps(config_dict))
    for item in overrides:
        if "=" not in item:
            continue
        key, raw_value = item.split("=", 1)
        value = _parse_value(raw_value)
        _set_by_path(updated, key.strip(), value)
    return updated


def _build_section(section_cls, values: Dict[str, Any]):
    allowed = {field.name for field in section_cls.__dataclass_fields__.values()}
    filtered = {k: v for k, v in values.items() if k in allowed}
    return section_cls(**filtered)


def _parse_value(raw_value: str) -> Any:
    raw_value = raw_value.strip()
    if raw_value.lower() in {"true", "false"}:
        return raw_value.lower() == "true"
    try:
        return int(raw_value)
    except ValueError:
        pass
    try:
        return float(raw_value)
    except ValueError:
        pass
    if raw_value.startswith("[") or raw_value.startswith("{"):
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value
    if "," in raw_value:
        return [item.strip() for item in raw_value.split(",") if item.strip()]
    return raw_value


def _set_by_path(container: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = container
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value

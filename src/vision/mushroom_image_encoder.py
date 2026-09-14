"""
蘑菇图像编码器
使用CLIP模型对MinIO中的蘑菇图像进行编码，解析时间信息，并获取对应的环境参数
集成LLaMA模型获取蘑菇生长情况描述
"""

import importlib
import json
import sys
import time
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PIL.Image import Image as PILImageType

_import_start = time.perf_counter()
_import_last = _import_start
_import_steps = []


def _record_import_step(label: str, last: float) -> float:
    now = time.perf_counter()
    _import_steps.append((label, (now - last) * 1000.0, (now - _import_start) * 1000.0))
    return now


def _timed_import(module_name: str, label: str):
    global _import_last
    module = importlib.import_module(module_name)
    _import_last = _record_import_step(label, _import_last)
    return module


np = _timed_import("numpy", "numpy")
requests = _timed_import("requests", "requests")
logger = _timed_import("loguru", "loguru").logger
Image = _timed_import("PIL.Image", "PIL")
func = _timed_import("sqlalchemy", "sqlalchemy.func").func
sessionmaker = _timed_import("sqlalchemy.orm", "sqlalchemy.orm").sessionmaker

environment_processor_module = _timed_import(
    "environment.processor", "environment.processor"
)
const_config_module = _timed_import(
    "global_const.const_config", "global_const.const_config"
)
global_const_module = _timed_import(
    "global_const.global_const", "global_const.global_const"
)
utils_module = _timed_import("utils", "utils")
create_table_module = _timed_import("utils.create_table", "utils.create_table")
get_data_module = _timed_import("utils.get_data", "utils.get_data")
minio_client_module = _timed_import("utils.minio_client", "utils.minio_client")

create_env_data_processor = environment_processor_module.create_env_data_processor
ROOM_ID_MAPPING = const_config_module.ROOM_ID_MAPPING
env = global_const_module.env
pgsql_engine = global_const_module.pgsql_engine
settings = global_const_module.settings
log_task_event = utils_module.log_task_event
ImageTextQuality = create_table_module.ImageTextQuality
MushroomImageEmbedding = create_table_module.MushroomImageEmbedding
GetData = get_data_module.GetData
create_minio_client = minio_client_module.create_minio_client

_import_last = _record_import_step("project_modules", _import_last)

mushroom_image_processor_module = _timed_import(
    "vision.mushroom_image_processor", "vision.mushroom_image_processor"
)
MushroomImageInfo = mushroom_image_processor_module.MushroomImageInfo
create_mushroom_processor = mushroom_image_processor_module.create_mushroom_processor

_import_last = _record_import_step("local_modules", _import_last)

from vision.encoder_llm.prompt_builder import (
    apply_prompt_length_guard,
    build_compact_v15_prompt,
    build_minimal_fallback_prompt,
)
from vision.encoder_llm.llama_gateway import (
    build_llama_completions_url,
    build_llama_headers,
    get_llama_extra_body,
    get_llama_generation_options,
    parse_llama_json_content,
)
from vision.encoder_image.compression import (
    LlamaImageCompressionConfig,
    binary_search_quality,
    compute_ssim,
    encode_image_for_llama_with_meta,
    encode_jpeg_bytes,
    resize_image_for_llama,
)
from vision.encoder_storage.text_quality_repository import (
    insert_text_quality_record,
    save_text_quality_only,
)


def _encoder_log(event: str, message: str, level: str = "INFO", **context: Any) -> None:
    """输出统一的视觉编码事件日志。"""
    log_task_event(
        "VISION_ENCODER",
        event,
        message,
        level=level,
        task_type="helper",
        **context,
    )


torch = None
CLIPModel = None
CLIPProcessor = None

if _import_steps:
    for label, delta_ms, total_ms in _import_steps:
        _encoder_log(
            "VISION_IMPORT_TIMING",
            "模块导入阶段计时",
            level="DEBUG",
            import_label=label,
            delta_ms=round(delta_ms, 2),
            total_ms=round(total_ms, 2),
        )
    _encoder_log(
        "VISION_IMPORT_TOTAL",
        "模块导入完成",
        level="DEBUG",
        total_ms=round((time.perf_counter() - _import_start) * 1000.0, 2),
    )


class MushroomImageEncoder:
    """蘑菇图像编码器类"""

    def __init__(self, load_clip: bool = True):
        """初始化编码器"""
        self.device = "cpu"
        _encoder_log(
            "VISION_ENCODER_INIT_START",
            "开始初始化图像编码器",
            level="DEBUG",
            device=self.device,
        )

        # 初始化CLIP模型
        self.clip_model = None
        self.clip_processor = None
        if load_clip:
            self._init_clip_model()
        else:
            _encoder_log(
                "VISION_CLIP_SKIPPED",
                "跳过CLIP模型加载，仅执行文本与质量分析",
                status="skipped",
            )

        # 初始化MinIO客户端和处理器
        self.minio_client = create_minio_client()
        self.processor = create_mushroom_processor()

        # 初始化数据库会话
        self.Session = sessionmaker(bind=pgsql_engine)

        # 初始化环境数据处理器
        self._init_env_processor()

        # 初始化GetData实例用于获取提示词
        self.get_data = GetData(
            urls=settings.data_source_url,
            host=settings.host.host,
            port=settings.host.port,
        )

        # 初始化LLaMA客户端
        self._init_llama_client()

        # 库房号映射：MinIO中的库房号 -> 环境配置中的库房号
        self.room_id_mapping = dict(ROOM_ID_MAPPING)

        # 质量控制参数 (默认宽松)
        self.quality_threshold = 0
        self.required_keywords = []
        self.low_quality_override_keywords = [
            "菌丝",
            "菌丝体",
            "原基",
            "针",
            "针尖",
            "出菇",
            "mycelium",
            "primordia",
            "pinhead",
            "pinning",
            "hypha",
        ]

        # LLaMA 性能监控配置
        self.time_threshold = 5000  # 性能警告阈值 (毫秒)
        self._setup_performance_logger()

        _encoder_log(
            "VISION_ENCODER_INIT_FINISH",
            "图像编码器初始化完成",
            level="DEBUG",
            status="success",
        )

    def _ensure_torch_imported(self):
        """按需导入 torch，避免纯文本任务在模块导入阶段加载重依赖。"""
        global torch

        if torch is None:
            start = time.perf_counter()
            torch = importlib.import_module("torch")
            _encoder_log(
                "VISION_LAZY_IMPORT_TORCH",
                "按需加载 torch 完成",
                level="DEBUG",
                delta_ms=round((time.perf_counter() - start) * 1000.0, 2),
            )

        return torch

    def _ensure_clip_dependencies_loaded(self):
        """按需导入 CLIP 依赖，仅在需要向量编码时加载。"""
        global CLIPModel, CLIPProcessor

        torch_module = self._ensure_torch_imported()
        if self.device == "cpu" and torch_module.cuda.is_available():
            self.device = "cuda"
            _encoder_log(
                "VISION_DEVICE_SWITCH",
                "编码设备切换为 CUDA",
                level="DEBUG",
                device=self.device,
            )

        if CLIPModel is None or CLIPProcessor is None:
            start = time.perf_counter()
            transformers_module = importlib.import_module("transformers")
            CLIPModel = transformers_module.CLIPModel
            CLIPProcessor = transformers_module.CLIPProcessor
            _encoder_log(
                "VISION_LAZY_IMPORT_TRANSFORMERS",
                "按需加载 transformers 完成",
                level="DEBUG",
                delta_ms=round((time.perf_counter() - start) * 1000.0, 2),
            )

    def _ensure_clip_model_ready(self):
        """确保 CLIP 依赖和模型在首次使用前加载完成。"""
        self._ensure_clip_dependencies_loaded()
        if self.clip_model is None or self.clip_processor is None:
            self._init_clip_model()

    def _setup_performance_logger(self):
        """配置 LLaMA 性能监控专用日志"""
        # 避免重复添加 sink (简单检查)
        # 注意: 这种检查并不完美，但在单例/工厂模式下足矣
        if not hasattr(self, "_perf_logger_configured"):
            log_path = Path("logs/llama_performance.log")
            try:
                logger.add(
                    log_path,
                    rotation="10 MB",
                    retention="30 days",
                    compression="zip",
                    filter=lambda record: (
                        record["extra"].get("type") == "llama_performance"
                    ),
                    format="{message}",  # 使用 JSON 格式或者自定义格式，这里我们把 message 构造成 JSON 字符串
                    level="INFO",
                    enqueue=True,
                )
                self._perf_logger_configured = True
            except Exception as e:
                _encoder_log(
                    "VISION_PERF_LOGGER_CONFIG_FAILED",
                    "配置 LLaMA 性能日志失败",
                    level="ERROR",
                    status="failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )

    def _map_room_id(self, room_id: str) -> str:
        """
        映射库房号：将MinIO中的库房号映射到环境配置中的库房号

        Args:
            room_id: MinIO中的库房号

        Returns:
            环境配置中对应的库房号
        """
        mapped_id = self.room_id_mapping.get(room_id, room_id)
        if mapped_id != room_id:
            _encoder_log(
                "VISION_ROOM_ID_MAPPED",
                "已映射库房编号",
                level="DEBUG",
                room_id=room_id,
                mapped_room_id=mapped_id,
            )
        return mapped_id

    def _allow_low_quality(self, description: str) -> bool:
        """低质量但早期生长阶段允许通过"""
        if not description:
            return False
        desc_lower = description.lower()
        return any(k.lower() in desc_lower for k in self.low_quality_override_keywords)

    def _save_text_quality_only(
        self,
        image_info: MushroomImageInfo,
        time_info: dict,
        growth_stage_description: str,
        chinese_description: str | None,
        llama_quality_score: float | None,
    ) -> bool:
        """仅保存文本描述与质量评分"""
        try:
            room_id = self._map_room_id(image_info.mushroom_id)
            in_date = time_info["collection_date"].date()
            return save_text_quality_only(
                session_factory=self.Session,
                image_path=image_info.file_path,
                room_id=room_id,
                in_date=in_date,
                collection_datetime=time_info["collection_datetime"],
                growth_stage_description=growth_stage_description,
                chinese_description=chinese_description,
                llama_quality_score=llama_quality_score,
            )
        except Exception as e:
            _encoder_log(
                "VISION_TEXT_QUALITY_SAVE_FAILED",
                "保存文本与质量记录失败",
                level="ERROR",
                image_name=image_info.file_name,
                room_id=image_info.mushroom_id,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return False

    def _init_clip_model(self):
        """初始化CLIP模型"""
        self._ensure_clip_dependencies_loaded()

        # 检查本地模型路径
        # 在容器中，源码直接复制到/app，models挂载到/app/models
        # 在开发环境中，保持原有的相对路径计算

        # 首先检查容器环境的路径
        container_model_path = Path("/app/models/clip-vit-base-patch32")

        # 然后检查开发环境的路径
        local_model_path = (
            Path(__file__).parent.parent.parent / "models" / "clip-vit-base-patch32"
        )

        if container_model_path.exists():
            model_name = str(container_model_path)
        elif local_model_path.exists():
            model_name = str(local_model_path)
        else:
            model_name = "openai/clip-vit-base-patch32"

        _encoder_log(
            "VISION_CLIP_MODEL_LOADING",
            "开始加载 CLIP 模型",
            level="DEBUG",
            model_name=model_name,
            status="running",
        )
        import warnings

        from transformers import logging as trans_log

        # 临时抑制transformers库的模型加载警告
        trans_log.set_verbosity_error()
        warnings.filterwarnings("ignore", category=UserWarning, module="transformers")

        try:
            self.clip_processor = CLIPProcessor.from_pretrained(model_name)
            self.clip_model = CLIPModel.from_pretrained(model_name).to(self.device)
            self.clip_model.eval()
        except Exception as e:
            _encoder_log(
                "VISION_CLIP_MODEL_LOAD_FAILED",
                "CLIP 模型加载失败",
                level="ERROR",
                model_name=model_name,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            if model_name == "openai/clip-vit-base-patch32":
                _encoder_log(
                    "VISION_CLIP_MODEL_MANUAL_DOWNLOAD_REQUIRED",
                    "默认 CLIP 模型不可用，需要手动放置本地模型文件",
                    level="CRITICAL",
                    model_name=model_name,
                    container_model_path=str(container_model_path),
                    local_model_path=str(local_model_path),
                    status="failed",
                )
            raise RuntimeError(f"Failed to load CLIP model: {e}") from e

        # 恢复警告
        trans_log.set_verbosity_warning()
        warnings.resetwarnings()

        _encoder_log(
            "VISION_CLIP_MODEL_READY",
            "CLIP 模型加载完成",
            level="DEBUG",
            model_name=model_name,
            device=str(self.device),
            status="success",
        )

    def _init_env_processor(self):
        """初始化环境数据处理器"""
        try:
            self.env_processor = create_env_data_processor()
            _encoder_log(
                "VISION_ENV_PROCESSOR_READY",
                "环境数据处理器初始化完成",
                level="DEBUG",
                status="success",
            )
        except Exception as e:
            _encoder_log(
                "VISION_ENV_PROCESSOR_INIT_FAILED",
                "环境数据处理器初始化失败",
                level="WARNING",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            self.env_processor = None

    def _init_llama_client(self):
        """初始化LLaMA客户端"""
        try:
            # 仅使用 llama-vl 配置，不回退到纯文本 llama 配置
            # Dynaconf 将 'llama-vl' 转换为 'llama_vl'
            if hasattr(settings, "llama_vl"):
                self.llama_config = settings.llama_vl
                _encoder_log(
                    "VISION_LLAMA_CONFIG_READY",
                    "已加载 llama-vl 配置",
                    level="DEBUG",
                )
            else:
                _encoder_log(
                    "VISION_LLAMA_CONFIG_MISSING",
                    "未找到 LLaMA-VL 配置，视觉描述功能不可用",
                    level="WARNING",
                    status="skipped",
                )
                self.llama_client = False
                return

            # 检查是否启用LLaMA
            if hasattr(self.llama_config, "enabled") and not self.llama_config.enabled:
                _encoder_log(
                    "VISION_LLAMA_DISABLED",
                    "LLaMA-VL 已禁用",
                    level="DEBUG",
                    status="skipped",
                )
                self.llama_client = False
                return

            # 标记LLaMA客户端可用
            self.llama_client = True
            _encoder_log(
                "VISION_LLAMA_CLIENT_READY",
                "LLaMA-VL 客户端初始化完成",
                level="DEBUG",
                status="success",
                model_name=getattr(self.llama_config, "model", "unknown"),
                endpoint=(
                    f"{getattr(self.llama_config, 'llama_host', 'localhost')}:"
                    f"{getattr(self.llama_config, 'llama_port', '7001')}"
                ),
            )

        except Exception as e:
            _encoder_log(
                "VISION_LLAMA_CLIENT_INIT_FAILED",
                "LLaMA-VL 客户端初始化失败",
                level="WARNING",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            self.llama_client = False

    def _get_llama_generation_options(self) -> dict[str, Any]:
        """从 llama_vl 配置读取采样参数。"""
        return get_llama_generation_options(self.llama_config)

    def _get_llama_extra_body(self, model_name: str) -> dict[str, Any] | None:
        """为 Qwen3 系列关闭 think/reasoning 输出。"""
        return get_llama_extra_body(model_name)

    def _apply_prompt_length_guard(self, prompt_text: str) -> str:
        """提示词长度守卫：过长时切换紧凑版提示词。"""
        def _log_compacted(original_length: int) -> None:
            _encoder_log(
                "VISION_LLAMA_PROMPT_COMPACTED",
                "提示词过长，切换到紧凑版提示词",
                level="WARNING",
                status="partial",
                total_items=original_length,
            )

        return apply_prompt_length_guard(prompt_text, on_compacted=_log_compacted)

    def _build_compact_v15_prompt(self) -> str:
        """构建紧凑版v15提示词，保留核心约束和关键示例。"""
        return build_compact_v15_prompt()

    def _build_minimal_fallback_prompt(self) -> str:
        """构建最小兼容提示词，用于接口400时降级重试。"""
        return build_minimal_fallback_prompt()

    def _call_llama_api(
        self,
        image_data: str,
        mlflow_images: dict[str, bytes] | None = None,
    ) -> dict[str, Any]:
        """
        直接调用LLaMA API (集成性能监控)

        Args:
            image_data: base64编码的图像数据
            mlflow_images: 需额外写入MLflow的图像字节，key为名称

        Returns:
            包含描述和评分的字典
        """
        # [性能监控] 初始化上下文
        request_id = str(uuid.uuid4())
        start_time = time.time()
        start_time_iso = datetime.now().isoformat()
        status = "unknown"
        error_details = None
        model_name = "unknown"

        # 绑定专用logger
        perf_logger = logger.bind(type="llama_performance")

        _encoder_log(
            "VISION_LLAMA_API_START",
            "开始调用 LLaMA 视觉接口",
            status="running",
            run_id=request_id,
            total_items=len(image_data),
        )

        try:
            # 从API动态获取提示词，如果失败则使用配置文件中的默认值
            prompt = self.get_data.get_mushroom_prompt()
            if not prompt:
                _encoder_log(
                    "VISION_LLAMA_PROMPT_FALLBACK",
                    "无法获取动态提示词，改用默认提示词",
                    level="WARNING",
                    status="fallback",
                )
                # 尝试从配置获取，如果没有则使用默认值
                prompt = getattr(
                    self.llama_config,
                    "mushroom_descripe_prompt",
                    "Describe the mushroom growth stage.",
                )

            # 获取配置参数，提供默认值
            model = getattr(self.llama_config, "model", "qwen/qwen3-vl-2b")
            model_name = model  # 记录用于日志

            generation_options = self._get_llama_generation_options()

            messages = []
            last_user_index = None
            if isinstance(prompt, list):
                for msg in prompt:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    if role == "system":
                        messages.append(
                            {
                                "role": "system",
                                "content": self._apply_prompt_length_guard(content),
                            }
                        )
                    elif role == "user":
                        messages.append(
                            {
                                "role": "user",
                                "content": self._apply_prompt_length_guard(content),
                            }
                        )
                        last_user_index = len(messages) - 1
                    elif role == "assistant":
                        messages.append({"role": "assistant", "content": str(content)})
                    else:
                        messages.append({"role": str(role), "content": str(content)})
                        if role == "user":
                            last_user_index = len(messages) - 1
            else:
                messages.append(
                    {
                        "role": "system",
                        "content": self._apply_prompt_length_guard(prompt),
                    }
                )
                messages.append({"role": "user", "content": ""})
                last_user_index = 1

            if last_user_index is None:
                messages.append({"role": "user", "content": ""})
                last_user_index = len(messages) - 1

            user_content = messages[last_user_index].get("content", "")
            if isinstance(user_content, list):
                typed_user_content = user_content
            else:
                typed_user_content = (
                    [{"type": "text", "text": str(user_content)}]
                    if str(user_content).strip()
                    else []
                )

            typed_user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                }
            )
            messages[last_user_index]["content"] = typed_user_content

            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "response_format": {"type": "text"},
            }
            payload.update(generation_options)

            extra_body = self._get_llama_extra_body(model)
            if extra_body:
                payload["extra_body"] = extra_body

            headers = build_llama_headers(self.llama_config, model)
            base_url = build_llama_completions_url(self.llama_config)

            # 从配置获取超时时间，默认600秒
            timeout = getattr(self.llama_config, "timeout", 600)

            # 使用requests直接发送请求，使用配置的超时时间
            linked_prompt_tag_value = None
            try:
                prompt_source = None
                if hasattr(self.get_data, "get_cached_prompt_meta"):
                    prompt_source = self.get_data.get_cached_prompt_meta().get("source")
                if not prompt_source:
                    prompt_source = getattr(
                        settings.data_source_url, "prompt_mushroom_description", None
                    )

                if isinstance(prompt_source, str) and prompt_source.startswith(
                    "prompts:/"
                ):
                    parts = prompt_source[len("prompts:/") :].strip("/").split("/")
                    if len(parts) >= 2:
                        linked_prompt_tag_value = json.dumps(
                            [{"name": parts[0], "version": str(parts[1])}],
                            ensure_ascii=False,
                        )
            except Exception:
                linked_prompt_tag_value = None

            # 尝试设置 MLflow trace
            span_cm = None
            span = None
            try:
                import base64
                import copy
                import os
                import tempfile

                import mlflow
                from mlflow.tracing.utils.prompt import TraceTagKey

                span_cm = mlflow.start_span(
                    name="llama_chat_completions", span_type="LLM"
                )
                span = span_cm.__enter__()

                trace_payload = copy.deepcopy(payload)

                if mlflow.active_run() and env != "production":
                    try:
                        tracked_images: dict[str, bytes] = {}
                        if mlflow_images:
                            tracked_images.update(mlflow_images)
                        else:
                            tracked_images["compressed"] = base64.b64decode(image_data)

                        tracking_uri = mlflow.get_tracking_uri()
                        compressed_http_url = None

                        for image_name, image_bytes in tracked_images.items():
                            with tempfile.NamedTemporaryFile(
                                suffix=".jpg", delete=False
                            ) as f:
                                f.write(image_bytes)
                                temp_path = f.name

                            artifact_path = f"images/{request_id}_{image_name}.jpg"
                            mlflow.log_artifact(temp_path, artifact_path)
                            os.remove(temp_path)

                            artifact_uri = mlflow.get_artifact_uri(artifact_path)
                            if artifact_uri.startswith("mlflow-artifacts:/"):
                                path = artifact_uri[len("mlflow-artifacts:/") :]
                                http_url = f"{tracking_uri.rstrip('/')}/api/2.0/mlflow-artifacts/artifacts/{path}"
                            else:
                                http_url = artifact_uri

                            if image_name == "compressed":
                                compressed_http_url = http_url

                        if compressed_http_url is None and tracked_images:
                            first_name = next(iter(tracked_images.keys()))
                            artifact_path = f"images/{request_id}_{first_name}.jpg"
                            artifact_uri = mlflow.get_artifact_uri(artifact_path)
                            if artifact_uri.startswith("mlflow-artifacts:/"):
                                path = artifact_uri[len("mlflow-artifacts:/") :]
                                compressed_http_url = f"{tracking_uri.rstrip('/')}/api/2.0/mlflow-artifacts/artifacts/{path}"
                            else:
                                compressed_http_url = artifact_uri

                        for msg in trace_payload.get("messages", []):
                            if isinstance(msg.get("content"), list):
                                for item in msg["content"]:
                                    if item.get("type") == "image_url":
                                        item["image_url"]["artifact_url"] = (
                                            compressed_http_url
                                        )
                    except Exception as e:
                        _encoder_log(
                            "VISION_LLAMA_TRACE_ARTIFACT_FAILED",
                            "写入 MLflow 图像 artifact 失败",
                            level="DEBUG",
                            status="partial",
                            run_id=request_id,
                            error_type=type(e).__name__,
                            error_message=str(e),
                        )
                elif mlflow.active_run() and env == "production":
                    _encoder_log(
                        "VISION_LLAMA_TRACE_ARTIFACT_SKIPPED",
                        "生产环境跳过 MLflow 图像 artifact 写入",
                        level="DEBUG",
                        status="skipped",
                        run_id=request_id,
                    )

                span.set_inputs(trace_payload)

                trace_id = None
                try:
                    trace_id = mlflow.get_active_trace_id()
                except Exception:
                    pass

                if trace_id and linked_prompt_tag_value is not None:
                    try:
                        mlflow.set_trace_tag(
                            trace_id,
                            TraceTagKey.LINKED_PROMPTS,
                            linked_prompt_tag_value,
                        )
                    except Exception:
                        pass
            except Exception as e:
                _encoder_log(
                    "VISION_LLAMA_TRACE_SETUP_FAILED",
                    "MLflow trace 初始化失败",
                    level="DEBUG",
                    status="partial",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                if span_cm:
                    span_cm.__exit__(None, None, None)
                span_cm = None
                span = None

            try:
                _encoder_log(
                    "VISION_LLAMA_REQUEST_SENT",
                    "LLaMA 请求已发送",
                    model_name=model,
                    run_id=request_id,
                    timeout_sec=timeout,
                    status="running",
                    has_extra_body=bool(extra_body),
                )
                request_start = time.time()
                resp = requests.post(
                    base_url, json=payload, headers=headers, timeout=timeout
                )
                _encoder_log(
                    "VISION_LLAMA_RESPONSE_RECEIVED",
                    "LLaMA 请求已返回",
                    status="success" if resp.status_code == 200 else "failed",
                    run_id=request_id,
                    error_code=f"http_{resp.status_code}",
                    duration_ms=round((time.time() - request_start) * 1000, 2),
                )
                if span:
                    if resp.status_code == 200:
                        span.set_outputs(resp.json())
                    else:
                        span.set_outputs(
                            {"status_code": resp.status_code, "text": resp.text}
                        )

                if (
                    resp.status_code == 400
                    and "failed to process image" in (resp.text or "").lower()
                ):
                    _encoder_log(
                        "VISION_LLAMA_REQUEST_RETRY_MINIMAL",
                        "主提示词请求被拒绝，使用最小兼容提示词重试一次",
                        level="WARNING",
                        status="retrying",
                        run_id=request_id,
                    )
                    fallback_payload = {
                        "model": model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": self._build_minimal_fallback_prompt(),
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/jpeg;base64,{image_data}"
                                        },
                                    },
                                ],
                            }
                        ],
                        "stream": False,
                        "response_format": {"type": "text"},
                    }
                    fallback_payload.update(generation_options)
                    if extra_body:
                        fallback_payload["extra_body"] = extra_body
                    resp = requests.post(
                        base_url,
                        json=fallback_payload,
                        headers=headers,
                        timeout=timeout,
                    )

                    if span:
                        if resp.status_code == 200:
                            span.set_outputs(resp.json())
                        else:
                            span.set_outputs(
                                {"status_code": resp.status_code, "text": resp.text}
                            )
            except Exception:
                import sys

                if span_cm:
                    span_cm.__exit__(*sys.exc_info())
                    span_cm = None
                # Retry once
                resp = requests.post(
                    base_url, json=payload, headers=headers, timeout=timeout
                )
            finally:
                if span_cm:
                    span_cm.__exit__(None, None, None)

            if resp.status_code == 200:
                response_data = resp.json()
                content = response_data["choices"][0]["message"]["content"]

                # 解析JSON响应
                try:
                    # 尝试直接解析JSON
                    # 有些模型可能返回包含Markdown代码块的JSON，需要清理
                    if "```json" in content:
                        content = content.split("```json")[1].split("```")[0].strip()
                    elif "```" in content:
                        content = content.split("```")[1].split("```")[0].strip()

                    llama_result = parse_llama_json_content(content)

                    # 验证必需字段
                    if (
                        "growth_stage_description" not in llama_result
                        or "image_quality_score" not in llama_result
                    ):
                        status = "failed"
                        error_details = f"Missing required fields. Keys found: {list(llama_result.keys())}"
                        _encoder_log(
                            "VISION_LLAMA_RESPONSE_REQUIRED_FIELDS_MISSING",
                            "LLaMA 响应缺少必需字段",
                            level="ERROR",
                            status="failed",
                            run_id=request_id,
                            error_message=error_details,
                        )
                        return {
                            "growth_stage_description": "",
                            "image_quality_score": None,
                            "chinese_description": None,
                        }

                    # 验证数据类型
                    description = str(llama_result["growth_stage_description"])
                    quality_score = llama_result["image_quality_score"]
                    chinese_description = llama_result.get("chinese_description", None)

                    # 验证质量评分范围
                    if not isinstance(quality_score, (int, float)):
                        _encoder_log(
                            "VISION_LLAMA_SCORE_TYPE_INVALID",
                            "LLaMA 质量评分类型无效，已置空",
                            level="WARNING",
                            status="partial",
                            run_id=request_id,
                            error_message=str(type(quality_score)),
                        )
                        quality_score = None
                    elif quality_score < 0 or quality_score > 100:
                        _encoder_log(
                            "VISION_LLAMA_SCORE_OUT_OF_RANGE",
                            "LLaMA 质量评分超出范围，已裁剪",
                            level="WARNING",
                            status="partial",
                            run_id=request_id,
                            score=float(quality_score),
                        )
                        quality_score = max(0, min(100, quality_score))

                    _encoder_log(
                        "VISION_LLAMA_PARSE_OK",
                        "LLaMA 响应解析成功",
                        level="DEBUG",
                        status="success",
                        run_id=request_id,
                        score=quality_score,
                        has_chinese_description=bool(chinese_description),
                    )
                    status = "success"
                    return {
                        "growth_stage_description": description,
                        "image_quality_score": quality_score,
                        "chinese_description": chinese_description,
                    }

                except json.JSONDecodeError as e:
                    status = "failed"
                    error_details = f"JSON parse error: {str(e)}"
                    _encoder_log(
                        "VISION_LLAMA_JSON_PARSE_FAILED",
                        "LLaMA 响应 JSON 解析失败",
                        level="ERROR",
                        status="failed",
                        run_id=request_id,
                        error_type=type(e).__name__,
                        error_message=str(e),
                        response_preview=content[:100],
                    )
                    return {
                        "growth_stage_description": "",
                        "image_quality_score": None,
                        "chinese_description": None,
                    }
                except KeyError as e:
                    status = "failed"
                    error_details = f"Missing key: {str(e)}"
                    _encoder_log(
                        "VISION_LLAMA_RESPONSE_KEY_MISSING",
                        "LLaMA 响应缺少键",
                        level="ERROR",
                        status="failed",
                        run_id=request_id,
                        error_message=str(e),
                    )
                    return {
                        "growth_stage_description": "",
                        "image_quality_score": None,
                        "chinese_description": None,
                    }
            else:
                status = "failed"
                error_details = f"HTTP {resp.status_code}: {resp.text[:200]}"
                _encoder_log(
                    "VISION_LLAMA_HTTP_FAILED",
                    "LLaMA API 调用失败",
                    level="ERROR",
                    status="failed",
                    run_id=request_id,
                    error_code=f"http_{resp.status_code}",
                    error_message=resp.text[:200],
                )
                return {
                    "growth_stage_description": "",
                    "image_quality_score": None,
                    "chinese_description": None,
                }

        except requests.exceptions.Timeout:
            status = "failed"
            error_details = "Request timed out"
            _encoder_log(
                "VISION_LLAMA_TIMEOUT",
                "LLaMA API 请求超时",
                level="WARNING",
                status="failed",
                run_id=request_id,
                timeout_sec=getattr(self.llama_config, "timeout", 600),
            )
            return {
                "growth_stage_description": "",
                "image_quality_score": None,
                "chinese_description": None,
            }
        except requests.exceptions.ConnectionError as e:
            status = "failed"
            error_details = f"Connection error: {str(e)}"
            _encoder_log(
                "VISION_LLAMA_CONNECTION_ERROR",
                "LLaMA API 连接错误",
                level="WARNING",
                status="failed",
                run_id=request_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {
                "growth_stage_description": "",
                "image_quality_score": None,
                "chinese_description": None,
            }
        except Exception as e:
            status = "failed"
            error_details = f"Unexpected error: {str(e)}"
            _encoder_log(
                "VISION_LLAMA_UNEXPECTED_ERROR",
                "LLaMA API 调用异常",
                level="ERROR",
                status="failed",
                run_id=request_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {
                "growth_stage_description": "",
                "image_quality_score": None,
                "chinese_description": None,
            }

        finally:
            # [性能监控] 记录与写入
            end_time = time.time()
            duration_ms = (end_time - start_time) * 1000

            # 构建日志条目 (JSON 友好结构)
            log_entry = {
                "timestamp": start_time_iso,
                "request_id": request_id,
                "model": model_name,
                "input_info": {"image_size_b64": len(image_data)},
                "start_time": start_time,
                "end_time": end_time,
                "duration_ms": round(duration_ms, 2),
                "status": status,
                "error": error_details,
            }

            try:
                # 写入专用日志文件
                perf_logger.info(json.dumps(log_entry, ensure_ascii=False))

                # 检查阈值并触发警告
                if duration_ms > self.time_threshold:
                    _encoder_log(
                        "VISION_LLAMA_PERF_SLOW",
                        "LLaMA 请求耗时超过阈值",
                        level="WARNING",
                        status="partial",
                        run_id=request_id,
                        duration_ms=round(duration_ms, 2),
                        model_name=model_name,
                    )
            except Exception as log_err:
                _encoder_log(
                    "VISION_LLAMA_PERF_LOG_FAILED",
                    "写入 LLaMA 性能日志失败",
                    level="ERROR",
                    status="failed",
                    run_id=request_id,
                    error_type=type(log_err).__name__,
                    error_message=str(log_err),
                )

    def _resize_image_for_llama(self, image: PILImageType) -> PILImageType:
        """
        将图像缩放到指定分辨率用于LLaMA处理，减少运算量

        Args:
            image: 原始PIL图像对象

        Returns:
            缩放后的PIL图像对象
        """
        try:
            target_width = int(getattr(self.llama_config, "image_width", 960))
            target_height = int(getattr(self.llama_config, "image_height", 960))
            resized = resize_image_for_llama(image, target_width, target_height)

            if not resized.changed:
                _encoder_log(
                    "VISION_LLAMA_IMAGE_RESIZE_SKIPPED",
                    "图像已在目标尺寸范围内，保留原始分辨率",
                    level="DEBUG",
                    original_width=resized.original_size[0],
                    original_height=resized.original_size[1],
                )
                return image

            _encoder_log(
                "VISION_LLAMA_IMAGE_RESIZED",
                "已为 LLaMA 缩放图像尺寸",
                level="DEBUG",
                original_width=resized.original_size[0],
                original_height=resized.original_size[1],
                resized_width=resized.resized_size[0],
                resized_height=resized.resized_size[1],
            )

            return resized.image

        except Exception as e:
            _encoder_log(
                "VISION_LLAMA_IMAGE_RESIZE_FAILED",
                "LLaMA 图像缩放失败，回退到原图",
                level="WARNING",
                status="fallback",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return image

    def _compute_ssim(
        self, source_image: PILImageType, target_image: PILImageType
    ) -> float:
        """计算两张图像的全局SSIM（灰度）。"""
        return compute_ssim(source_image, target_image)

    def _encode_jpeg_bytes(self, image: PILImageType, quality: int) -> bytes:
        """将图像按指定质量编码为JPEG字节流。"""
        return encode_jpeg_bytes(image, quality)

    def _binary_search_quality(
        self,
        source_image: PILImageType,
        max_image_bytes: int,
        min_jpeg_quality: int,
        max_jpeg_quality: int,
        ssim_threshold: float,
        quality_search_steps: int,
    ) -> tuple[bytes, int, float]:
        """在体积和SSIM约束下二分搜索最优JPEG质量。"""
        return binary_search_quality(
            source_image=source_image,
            max_image_bytes=max_image_bytes,
            min_jpeg_quality=min_jpeg_quality,
            max_jpeg_quality=max_jpeg_quality,
            ssim_threshold=ssim_threshold,
            quality_search_steps=quality_search_steps,
        )

    def _encode_image_for_llama_with_meta(
        self,
        image: PILImageType,
    ) -> tuple[str, bytes, PILImageType]:
        """将图像编码为适合LLaMA-VL的base64，含体积/质量双约束。"""
        config = LlamaImageCompressionConfig(
            target_width=int(getattr(self.llama_config, "image_width", 960)),
            target_height=int(getattr(self.llama_config, "image_height", 960)),
            jpeg_quality=int(getattr(self.llama_config, "jpeg_quality", 80)),
            min_jpeg_quality=int(getattr(self.llama_config, "min_jpeg_quality", 50)),
            max_image_bytes=int(
                getattr(self.llama_config, "max_image_bytes", 350 * 1024)
            ),
            downscale_step_percent=int(
                getattr(self.llama_config, "downscale_step_percent", 15)
            ),
            ssim_threshold=float(getattr(self.llama_config, "ssim_threshold", 0.95)),
            quality_search_steps=int(
                getattr(self.llama_config, "quality_search_steps", 7)
            ),
            max_downscale_attempts=int(
                getattr(self.llama_config, "max_downscale_attempts", 4)
            ),
        )
        resized_image = self._resize_image_for_llama(image)
        encoded = encode_image_for_llama_with_meta(resized_image, config)

        if encoded.compression_applied:
            _encoder_log(
                "VISION_LLAMA_ADAPTIVE_COMPRESSION_APPLIED",
                "图像已完成自适应压缩",
                level="WARNING",
                attempt=encoded.attempt,
                image_size=str(encoded.encoded_size),
                jpeg_quality=encoded.selected_quality,
                image_bytes=len(encoded.image_bytes),
                ssim=round(encoded.ssim, 4),
                target_ssim=round(encoded.target_ssim, 4),
                status="success",
            )

        return encoded.image_data, encoded.image_bytes, encoded.resized_image

    def _encode_image_for_llama(self, image: PILImageType) -> str:
        """兼容接口：仅返回base64编码字符串。"""
        image_data, _, _ = self._encode_image_for_llama_with_meta(image)
        return image_data

    def _get_llama_description(self, image: PILImageType) -> dict[str, Any]:
        """
        使用LLaMA模型获取蘑菇生长情况描述和图像质量评分

        Args:
            image: PIL图像对象

        Returns:
            包含growth_stage_description、image_quality_score、chinese_description的字典
            格式: {"growth_stage_description": str, "image_quality_score": float or None, "chinese_description": str or None}
        """
        if not self.llama_client:
            _encoder_log(
                "VISION_LLAMA_DESCRIPTION_SKIPPED",
                "LLaMA 客户端不可用，跳过描述生成",
                level="WARNING",
                status="skipped",
            )
            return {
                "growth_stage_description": "",
                "image_quality_score": None,
                "chinese_description": None,
            }

        try:
            # 图像预处理 + 自适应压缩编码（减少视觉token和请求体积）
            image_data, compressed_bytes, resized_image = (
                self._encode_image_for_llama_with_meta(image)
            )

            # 记录压缩前后图像到MLflow（在safe_hourly_text_quality_inference中有active run）
            pre_quality = int(
                getattr(self.llama_config, "mlflow_pre_image_quality", 95)
            )
            pre_quality = max(20, min(100, pre_quality))
            pre_compression_bytes = self._encode_jpeg_bytes(resized_image, pre_quality)

            mlflow_images = {
                "pre_compression": pre_compression_bytes,
                "compressed": compressed_bytes,
            }

            # 调用LLaMA API
            result = self._call_llama_api(
                image_data,
                mlflow_images=mlflow_images,
            )
            _encoder_log(
                "VISION_LLAMA_DESCRIPTION_READY",
                "已生成 LLaMA 描述结果",
                level="DEBUG",
                status="success",
                response_preview=result.get("growth_stage_description", "")[:50],
                score=result.get("image_quality_score"),
            )
            return result

        except Exception as e:
            _encoder_log(
                "VISION_LLAMA_DESCRIPTION_FAILED",
                "获取 LLaMA 描述失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {
                "growth_stage_description": "",
                "image_quality_score": None,
                "chinese_description": None,
            }

    def get_multimodal_embedding(
        self, image: PILImageType, text_description: str
    ) -> list[float] | None:
        """
        获取图像和文本的多模态CLIP向量编码

        Args:
            image: PIL图像对象
            text_description: 环境数据的语义描述文本

        Returns:
            512维联合向量列表，失败返回None
        """
        try:
            self._ensure_clip_model_ready()
            torch_module = self._ensure_torch_imported()

            # 确保图像为RGB格式
            if image.mode != "RGB":
                image = image.convert("RGB")

            # 同时预处理图像和文本
            inputs = self.clip_processor(
                text=text_description,
                images=image,
                return_tensors="pt",
                padding=True,
                truncation=True,
            ).to(self.device)

            # 获取图像和文本特征
            with torch_module.no_grad():
                image_features = self.clip_model.get_image_features(
                    pixel_values=inputs["pixel_values"]
                )
                text_features = self.clip_model.get_text_features(
                    input_ids=inputs["input_ids"],
                    attention_mask=inputs["attention_mask"],
                )

            # 多模态特征融合 - 使用加权平均
            # 图像特征权重0.7，文本特征权重0.3（可根据实际效果调整）
            image_weight = 0.7
            text_weight = 0.3

            # 确保特征是tensor格式，处理可能的BaseModelOutputWithPooling对象
            if hasattr(image_features, "last_hidden_state"):
                # Take the pooled output if available, otherwise take the CLS token
                if (
                    hasattr(image_features, "pooler_output")
                    and image_features.pooler_output is not None
                ):
                    image_features = image_features.pooler_output
                else:
                    image_features = image_features.last_hidden_state[
                        :, 0, :
                    ]  # Take the CLS token
            elif (
                hasattr(image_features, "pooler_output")
                and image_features.pooler_output is not None
            ):
                image_features = image_features.pooler_output
            elif torch_module.is_tensor(image_features):
                # Already a tensor, use as-is
                pass
            else:
                # Fallback: assume it's a tensor-like object
                image_features = image_features

            if hasattr(text_features, "last_hidden_state"):
                # Take the pooled output if available, otherwise take the CLS token
                if (
                    hasattr(text_features, "pooler_output")
                    and text_features.pooler_output is not None
                ):
                    text_features = text_features.pooler_output
                else:
                    text_features = text_features.last_hidden_state[
                        :, 0, :
                    ]  # Take the CLS token
            elif (
                hasattr(text_features, "pooler_output")
                and text_features.pooler_output is not None
            ):
                text_features = text_features.pooler_output
            elif torch_module.is_tensor(text_features):
                # Already a tensor, use as-is
                pass
            else:
                # Fallback: assume it's a tensor-like object
                text_features = text_features

            # 归一化各自的特征
            image_features_norm = image_features / image_features.norm(
                dim=-1, keepdim=True
            )
            text_features_norm = text_features / text_features.norm(
                dim=-1, keepdim=True
            )

            # 加权融合
            multimodal_features = (
                image_weight * image_features_norm + text_weight * text_features_norm
            )

            # 最终归一化
            embedding = multimodal_features.cpu().numpy()[0]
            embedding = embedding / np.linalg.norm(embedding)

            _encoder_log(
                "VISION_MULTIMODAL_EMBEDDING_READY",
                "多模态 embedding 生成完成",
                level="DEBUG",
                text_preview=text_description[:50],
                embedding_dim=len(embedding.tolist()),
                status="success",
            )
            return embedding.tolist()

        except Exception as e:
            _encoder_log(
                "VISION_MULTIMODAL_EMBEDDING_FAILED",
                "多模态 embedding 生成失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return None

    def get_image_embedding(self, image: PILImageType) -> list[float] | None:
        """
        获取纯图像的CLIP向量编码（保留作为备用方法）

        Args:
            image: PIL图像对象

        Returns:
            512维向量列表，失败返回None
        """
        try:
            self._ensure_clip_model_ready()
            torch_module = self._ensure_torch_imported()

            # 确保图像为RGB格式
            if image.mode != "RGB":
                image = image.convert("RGB")

            # 预处理图像
            inputs = self.clip_processor(
                images=image, return_tensors="pt", padding=True
            ).to(self.device)

            # 获取图像特征
            with torch_module.no_grad():
                image_features = self.clip_model.get_image_features(**inputs)

            # 确保特征是tensor格式，处理可能的BaseModelOutputWithPooling对象
            if hasattr(image_features, "last_hidden_state"):
                # Take the pooled output if available, otherwise take the CLS token
                if (
                    hasattr(image_features, "pooler_output")
                    and image_features.pooler_output is not None
                ):
                    image_features = image_features.pooler_output
                else:
                    image_features = image_features.last_hidden_state[
                        :, 0, :
                    ]  # Take the CLS token
            elif (
                hasattr(image_features, "pooler_output")
                and image_features.pooler_output is not None
            ):
                image_features = image_features.pooler_output
            elif torch_module.is_tensor(image_features):
                # Already a tensor, use as-is
                pass
            else:
                # Fallback: assume it's a tensor-like object
                image_features = image_features

            # 归一化向量（对余弦相似度很重要）
            embedding = image_features.cpu().numpy()[0]
            embedding = embedding / np.linalg.norm(embedding)

            return embedding.tolist()

        except Exception as e:
            _encoder_log(
                "VISION_IMAGE_EMBEDDING_FAILED",
                "图像 embedding 生成失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return None

    def parse_time_from_path(
        self, image_info: MushroomImageInfo
    ) -> dict[str, datetime]:
        """
        从图像路径信息中解析时间

        Args:
            image_info: 蘑菇图像信息对象

        Returns:
            包含各种时间信息的字典
        """
        time_info = {
            "collection_datetime": image_info.collection_datetime,
            "collection_date": datetime.strptime(image_info.collection_date, "%Y%m%d"),
            "detailed_time": datetime.strptime(
                image_info.detailed_time, "%Y%m%d%H%M%S"
            ),
            "date_folder": datetime.strptime(image_info.date_folder, "%Y%m%d"),
        }

        # 添加时间范围（用于查询环境参数）
        collection_time = time_info["collection_datetime"]
        time_info["query_start"] = collection_time - timedelta(minutes=30)  # 前30分钟
        time_info["query_end"] = collection_time + timedelta(minutes=30)  # 后30分钟

        return time_info

    def get_environmental_data(
        self, mushroom_id: str, time_info: dict[str, datetime]
    ) -> dict | None:
        """
        根据蘑菇库号和时间信息获取环境参数

        Args:
            mushroom_id: 蘑菇库号
            time_info: 时间信息字典

        Returns:
            结构化的环境参数字典，失败返回None
        """
        if not self.env_processor:
            _encoder_log(
                "VISION_ENV_PROCESSOR_UNAVAILABLE",
                "环境数据处理器未初始化，跳过环境数据查询",
                level="WARNING",
                room_id=mushroom_id,
                status="skipped",
            )
            return None

        try:
            collection_time = time_info["collection_datetime"]
            # 构建临时图像路径用于记录
            temp_image_path = (
                f"{mushroom_id}/{collection_time.strftime('%Y%m%d')}/temp_image.jpg"
            )

            # 映射库房号：MinIO中的库房号 -> 环境配置中的库房号
            mapped_room_id = self._map_room_id(mushroom_id)

            _encoder_log(
                "VISION_ENV_QUERY_START",
                "开始查询图像对应环境数据",
                level="DEBUG",
                room_id=mushroom_id,
                mapped_room_id=mapped_room_id,
                trigger_time=collection_time.isoformat(),
                status="running",
            )

            # 使用映射后的库房号查询环境数据
            env_data = self.env_processor.get_environment_data(
                room_id=mapped_room_id,
                collection_time=collection_time,
                image_path=temp_image_path,
                time_window_minutes=1,  # 查询前后1分钟的数据
            )

            if env_data:
                _encoder_log(
                    "VISION_ENV_QUERY_OK",
                    "已获取图像对应环境数据",
                    level="DEBUG",
                    room_id=mushroom_id,
                    mapped_room_id=mapped_room_id,
                    status="success",
                )
                return env_data
            else:
                _encoder_log(
                    "VISION_ENV_QUERY_EMPTY",
                    "未找到图像对应环境数据",
                    level="DEBUG",
                    room_id=mushroom_id,
                    mapped_room_id=mapped_room_id,
                    status="skipped",
                )
                return None

        except Exception as e:
            _encoder_log(
                "VISION_ENV_QUERY_FAILED",
                "查询图像对应环境数据失败",
                level="ERROR",
                room_id=mushroom_id,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return None

    def get_growth_stage_analysis(self, image: PILImageType) -> dict[str, Any]:
        """
        获取图像的生长阶段分析（公开接口）

        Args:
            image: PIL图像对象

        Returns:
            包含growth_stage_description和image_quality_score的字典
        """
        return self._get_llama_description(image)

    def process_single_image(
        self,
        image_info: MushroomImageInfo,
        save_to_db: bool = True,
        precomputed_analysis: dict | None = None,
        selected_quality_record_id: int | None = None,
    ) -> dict | None:
        """
        处理单个图像：解析时间、获取环境参数、多模态编码
        只有在获取到完整数据（图像+环境数据）时才存储到数据库

        Args:
            image_info: 蘑菇图像信息
            save_to_db: 是否保存到数据库
            precomputed_analysis: 预计算的分析结果（可选），包含growth_stage_description和image_quality_score
            selected_quality_record_id: 已选中的image_text_quality记录ID（可选），用于仅回填mushroom_embedding_id

        Returns:
            处理结果字典
        """
        try:
            _encoder_log(
                "VISION_SINGLE_IMAGE_START",
                "开始处理单张图像",
                room_id=image_info.mushroom_id,
                image_name=image_info.file_name,
                status="running",
            )
            # 1. 从MinIO获取图像
            image = self.minio_client.get_image(image_info.file_path)
            if image is None:
                _encoder_log(
                    "VISION_SINGLE_IMAGE_FETCH_FAILED",
                    "从 MinIO 获取图像失败",
                    level="WARNING",
                    room_id=image_info.mushroom_id,
                    image_name=image_info.file_name,
                    status="failed",
                )
                return None

            # 2. 解析时间信息
            time_info = self.parse_time_from_path(image_info)

            # 3. 获取环境参数和语义描述
            env_data = self.get_environmental_data(image_info.mushroom_id, time_info)

            # 4. 检查是否获取到完整环境数据
            if env_data is None:
                _encoder_log(
                    "VISION_SINGLE_IMAGE_NO_ENV_DATA",
                    "未获取到环境数据，切换纯图像编码路径",
                    level="DEBUG",
                    room_id=image_info.mushroom_id,
                    image_name=image_info.file_name,
                    status="partial",
                )
                # 如果没有环境数据，使用纯图像编码
                embedding = self.get_image_embedding(image)
                if embedding is None:
                    _encoder_log(
                        "VISION_SINGLE_IMAGE_EMBEDDING_FAILED",
                        "纯图像编码失败",
                        level="ERROR",
                        room_id=image_info.mushroom_id,
                        image_name=image_info.file_name,
                        status="failed",
                    )
                    return None

                return {
                    "image_info": image_info,
                    "embedding": embedding,
                    "time_info": time_info,
                    "environmental_data": None,
                    "processed_at": datetime.now(),
                    "saved_to_db": False,
                    "skip_reason": "no_environment_data",
                }

            # 5. LLaMA服务可用性检查 (Strict Mode)
            if not self.llama_client:
                _encoder_log(
                    "VISION_SINGLE_IMAGE_LLAMA_UNAVAILABLE",
                    "LLaMA 服务不可用，跳过图像处理",
                    level="WARNING",
                    room_id=image_info.mushroom_id,
                    image_name=image_info.file_name,
                    status="skipped",
                )
                return None

            # 6. 使用LLaMA模型获取蘑菇生长情况描述和图像质量评分
            if precomputed_analysis:
                llama_result = precomputed_analysis
                _encoder_log(
                    "VISION_SINGLE_IMAGE_PRECOMPUTED_ANALYSIS",
                    "使用预计算的 LLaMA 分析结果",
                    level="DEBUG",
                    room_id=image_info.mushroom_id,
                    image_name=image_info.file_name,
                    score=llama_result.get("image_quality_score"),
                )
            else:
                llama_result = self._get_llama_description(image)

            # 提取growth_stage_description、image_quality_score、chinese_description
            growth_stage_description = llama_result.get("growth_stage_description", "")
            chinese_description = llama_result.get("chinese_description", None)
            llama_quality_score = llama_result.get("image_quality_score", None)

            # 7. 验证LLaMA结果 (No Degradation)
            if not growth_stage_description:
                _encoder_log(
                    "VISION_SINGLE_IMAGE_DESCRIPTION_EMPTY",
                    "LLaMA 未生成有效描述，跳过处理",
                    level="WARNING",
                    room_id=image_info.mushroom_id,
                    image_name=image_info.file_name,
                    status="skipped",
                )
                return None

            # 7.1 质量评分筛选
            if self.quality_threshold > 0:
                if (
                    llama_quality_score is not None
                    and llama_quality_score < self.quality_threshold
                ):
                    if self._allow_low_quality(growth_stage_description):
                        _encoder_log(
                            "VISION_SINGLE_IMAGE_QUALITY_OVERRIDE",
                            "低质量但属于允许的早期生长阶段，继续处理",
                            room_id=image_info.mushroom_id,
                            image_name=image_info.file_name,
                            score=llama_quality_score,
                            status="partial",
                        )
                    else:
                        _encoder_log(
                            "VISION_SINGLE_IMAGE_QUALITY_TOO_LOW",
                            "图像质量评分过低，跳过处理",
                            level="WARNING",
                            room_id=image_info.mushroom_id,
                            image_name=image_info.file_name,
                            score=llama_quality_score,
                            status="skipped",
                        )
                        return None

            # 7.2 关键词内容筛选 (如蘑菇特征)
            if self.required_keywords:
                desc_lower = growth_stage_description.lower()
                if not any(k.lower() in desc_lower for k in self.required_keywords):
                    _encoder_log(
                        "VISION_SINGLE_IMAGE_REQUIRED_KEYWORDS_MISSING",
                        "未检测到必需特征关键词，跳过处理",
                        level="WARNING",
                        room_id=image_info.mushroom_id,
                        image_name=image_info.file_name,
                        status="skipped",
                        response_preview=growth_stage_description[:30],
                    )
                    return None

            # 8. 构建完整的文本描述：身份元数据 + LLaMA生长阶段描述
            identity_metadata = env_data.get(
                "semantic_description",
                f"Mushroom Room {image_info.mushroom_id}, unknown stage, Day 0.",
            )

            # 结合身份元数据和LLaMA生长阶段描述
            full_text_description = f"{identity_metadata} {growth_stage_description}"
            _encoder_log(
                "VISION_SINGLE_IMAGE_TEXT_COMPOSED",
                "已完成身份元数据与 LLaMA 描述拼接",
                level="DEBUG",
                room_id=image_info.mushroom_id,
                image_name=image_info.file_name,
            )

            # 9. 使用多模态编码（图像 + 完整文本描述）
            if not self.clip_model or not self.clip_processor:
                if save_to_db:
                    saved = self._save_text_quality_only(
                        image_info,
                        time_info,
                        growth_stage_description,
                        chinese_description,
                        llama_quality_score,
                    )
                else:
                    saved = False

                return {
                    "image_info": image_info,
                    "embedding": None,
                    "time_info": time_info,
                    "environmental_data": env_data,
                    "processed_at": datetime.now(),
                    "saved_to_db": saved,
                    "skip_reason": "clip_disabled",
                }

            embedding = self.get_multimodal_embedding(image, full_text_description)

            if embedding is None:
                _encoder_log(
                    "VISION_SINGLE_IMAGE_MULTIMODAL_FAILED",
                    "多模态编码失败",
                    level="ERROR",
                    room_id=image_info.mushroom_id,
                    image_name=image_info.file_name,
                    status="failed",
                )
                return None

            # 8. 将描述和质量评分保存到环境数据中
            env_data["full_text_description"] = full_text_description
            env_data["llama_description"] = (
                growth_stage_description if growth_stage_description else "N/A"
            )
            env_data["chinese_description"] = chinese_description
            env_data["image_quality_score"] = (
                llama_quality_score  # 使用LLaMA返回的质量评分
            )

            # 9. 构建结果
            result = {
                "image_info": image_info,
                "embedding": embedding,
                "time_info": time_info,
                "environmental_data": env_data,
                "processed_at": datetime.now(),
            }

            # 10. 只有在获取到完整数据时才保存到数据库
            if save_to_db:
                success = self._save_to_database(
                    result,
                    selected_quality_record_id=selected_quality_record_id,
                )
                result["saved_to_db"] = success
                if not success:
                    _encoder_log(
                        "VISION_SINGLE_IMAGE_DB_SAVE_FAILED",
                        "单图处理结果保存数据库失败",
                        level="ERROR",
                        room_id=image_info.mushroom_id,
                        image_name=image_info.file_name,
                        status="failed",
                    )
            else:
                result["saved_to_db"] = False

            _encoder_log(
                "VISION_SINGLE_IMAGE_FINISH",
                "单张图像处理完成",
                room_id=image_info.mushroom_id,
                image_name=image_info.file_name,
                status="success" if result.get("saved_to_db") else "partial",
                stored_records=1 if result.get("saved_to_db") else 0,
            )

            return result

        except Exception as e:
            _encoder_log(
                "VISION_SINGLE_IMAGE_EXCEPTION",
                "单张图像处理异常",
                level="ERROR",
                room_id=image_info.mushroom_id,
                image_name=image_info.file_name,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return None

    def process_image_batch(
        self, images_data: list[dict], save_to_db: bool = True
    ) -> list[dict]:
        """
        批处理多个图像：优化的批量处理方法

        Args:
            images_data: 图像数据列表，每个元素包含 {'image': PIL.Image, 'image_info': MushroomImageInfo, 'img_meta': dict}
            save_to_db: 是否保存到数据库

        Returns:
            处理结果列表
        """
        if not images_data:
            return []

        _encoder_log(
            "VISION_BATCH_PROCESS_START",
            "开始批量处理图像",
            total_items=len(images_data),
            status="running",
        )
        batch_results = []

        try:
            # 1. 批量准备数据
            batch_data = []
            for img_data in images_data:
                image = img_data["image"]
                image_info = img_data["image_info"]

                # 解析时间信息
                time_info = self.parse_time_from_path(image_info)

                # 获取环境参数
                env_data = self.get_environmental_data(
                    image_info.mushroom_id, time_info
                )

                batch_data.append(
                    {
                        "image": image,
                        "image_info": image_info,
                        "time_info": time_info,
                        "env_data": env_data,
                        "precomputed_analysis": img_data.get("img_meta", {}).get(
                            "_precomputed_analysis"
                        ),
                    }
                )

            # 2. 分离有环境数据和无环境数据的图片
            with_env_data = [
                item for item in batch_data if item["env_data"] is not None
            ]
            without_env_data = [item for item in batch_data if item["env_data"] is None]

            _encoder_log(
                "VISION_BATCH_CLASSIFIED",
                "批量图像已按环境数据分类",
                level="DEBUG",
                with_env_count=len(with_env_data),
                without_env_count=len(without_env_data),
                total_items=len(batch_data),
            )

            # 3. 批量处理有环境数据的图片
            if with_env_data:
                batch_results.extend(
                    self._process_batch_with_env_data(with_env_data, save_to_db)
                )

            # 4. 批量处理无环境数据的图片（纯图像编码）
            if without_env_data:
                batch_results.extend(
                    self._process_batch_without_env_data(without_env_data, save_to_db)
                )

            success_count = sum(1 for r in batch_results if r["success"])
            failed_count = sum(1 for r in batch_results if not r["success"])
            _encoder_log(
                "VISION_BATCH_PROCESS_FINISH",
                "批量图像处理完成",
                total_items=len(batch_results),
                successful_items=success_count,
                failed_items=failed_count,
                success_rate=round((success_count / len(batch_results) * 100), 2)
                if batch_results
                else 0.0,
                status="success" if failed_count == 0 else "partial",
            )

            return batch_results

        except Exception as e:
            _encoder_log(
                "VISION_BATCH_PROCESS_FAILED",
                "批量图像处理异常，开始回退单张处理",
                level="ERROR",
                total_items=len(images_data),
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            # 回退到单张处理
            for img_data in images_data:
                try:
                    result = self.process_single_image(
                        img_data["image_info"], save_to_db=save_to_db
                    )
                    success = result is not None and (
                        not save_to_db or result.get("saved_to_db", False)
                    )
                    batch_results.append(
                        {"success": success, "image_info": img_data["image_info"]}
                    )
                except Exception as e2:
                    _encoder_log(
                        "VISION_BATCH_FALLBACK_ITEM_FAILED",
                        "批量回退单张处理失败",
                        level="ERROR",
                        image_name=img_data["image_info"].file_name,
                        room_id=img_data["image_info"].mushroom_id,
                        status="failed",
                        error_type=type(e2).__name__,
                        error_message=str(e2),
                    )
                    batch_results.append(
                        {"success": False, "image_info": img_data["image_info"]}
                    )

            return batch_results

    def _process_batch_with_env_data(
        self, batch_data: list[dict], save_to_db: bool
    ) -> list[dict]:
        """批量处理有环境数据的图片"""
        results = []

        try:
            # 0. Strict Check: LLaMA必须可用
            if not self.llama_client:
                _encoder_log(
                    "VISION_BATCH_ENV_LLAMA_UNAVAILABLE",
                    "LLaMA 服务不可用，跳过有环境数据的批处理",
                    level="WARNING",
                    total_items=len(batch_data),
                    status="skipped",
                )
                for item in batch_data:
                    results.append({"success": False, "image_info": item["image_info"]})
                return results

            # 1. 批量获取LLaMA描述 (混合 Precomputed 和 Computed)
            llama_results = [None] * len(batch_data)
            need_compute_images = []
            need_compute_indices = []

            for i, item in enumerate(batch_data):
                if item.get("precomputed_analysis"):
                    llama_results[i] = item["precomputed_analysis"]
                else:
                    need_compute_images.append(item["image"])
                    need_compute_indices.append(i)

            # 对缺失的进行计算
            if need_compute_images:
                computed_results = self._get_llama_descriptions_batch(
                    need_compute_images
                )
                for idx, res in zip(need_compute_indices, computed_results):
                    llama_results[idx] = res

            # 2. 准备批量CLIP编码的数据
            clip_inputs = []
            valid_items = []  # (original_index, item, llama_result)

            for i, item in enumerate(batch_data):
                llama_result = llama_results[i] if i < len(llama_results) else {}
                growth_stage_description = llama_result.get(
                    "growth_stage_description", ""
                )
                llama_quality_score = llama_result.get("image_quality_score", 0)

                # Strict Check: LLaMA描述必须存在
                if not growth_stage_description:
                    _encoder_log(
                        "VISION_BATCH_ENV_ITEM_SKIPPED_NO_DESCRIPTION",
                        "LLaMA 描述为空，跳过该图像",
                        level="WARNING",
                        image_name=item["image_info"].file_name,
                        room_id=item["image_info"].mushroom_id,
                        status="skipped",
                    )
                    results.append({"success": False, "image_info": item["image_info"]})
                    continue

                # Quality Check
                if self.quality_threshold > 0:
                    if (
                        llama_quality_score is not None
                        and llama_quality_score < self.quality_threshold
                    ):
                        if self._allow_low_quality(growth_stage_description):
                            _encoder_log(
                                "VISION_BATCH_ENV_LOW_QUALITY_OVERRIDDEN",
                                "低质量图像因早期生长阶段规则被放行",
                                image_name=item["image_info"].file_name,
                                room_id=item["image_info"].mushroom_id,
                                score=llama_quality_score,
                                status="success",
                            )
                        else:
                            _encoder_log(
                                "VISION_BATCH_ENV_ITEM_SKIPPED_LOW_QUALITY",
                                "质量评分低于阈值，跳过该图像",
                                level="WARNING",
                                image_name=item["image_info"].file_name,
                                room_id=item["image_info"].mushroom_id,
                                score=llama_quality_score,
                                threshold=self.quality_threshold,
                                status="skipped",
                            )
                            results.append(
                                {"success": False, "image_info": item["image_info"]}
                            )
                            continue

                # Content Check
                if self.required_keywords:
                    desc_lower = growth_stage_description.lower()
                    if not any(k.lower() in desc_lower for k in self.required_keywords):
                        _encoder_log(
                            "VISION_BATCH_ENV_ITEM_SKIPPED_CONTENT_MISMATCH",
                            "未检测到要求的内容特征，跳过该图像",
                            level="WARNING",
                            image_name=item["image_info"].file_name,
                            room_id=item["image_info"].mushroom_id,
                            required_keywords=",".join(self.required_keywords),
                            status="skipped",
                        )
                        results.append(
                            {"success": False, "image_info": item["image_info"]}
                        )
                        continue

                env_data = item["env_data"]
                identity_metadata = env_data.get(
                    "semantic_description",
                    f"Mushroom Room {item['image_info'].mushroom_id}, unknown stage, Day 0.",
                )

                full_text_description = (
                    f"{identity_metadata} {growth_stage_description}"
                )

                clip_inputs.append(
                    {"image": item["image"], "text": full_text_description, "index": i}
                )
                valid_items.append((i, item, llama_result))

            # 如果没有有效项，直接返回
            if not clip_inputs:
                return results

            # 3. 批量CLIP编码
            embeddings = self._get_multimodal_embeddings_batch(clip_inputs)

            # 4. 构建结果并保存
            for k, (original_idx, item, llama_result) in enumerate(valid_items):
                try:
                    embedding = embeddings[k] if k < len(embeddings) else None

                    if embedding is None:
                        _encoder_log(
                            "VISION_BATCH_ENV_ITEM_EMBEDDING_FAILED",
                            "批量多模态编码失败",
                            level="ERROR",
                            image_name=item["image_info"].file_name,
                            room_id=item["image_info"].mushroom_id,
                            status="failed",
                        )
                        results.append(
                            {"success": False, "image_info": item["image_info"]}
                        )
                        continue

                    # 构建完整结果
                    env_data = item["env_data"].copy()

                    # 添加描述和质量评分
                    env_data["llama_description"] = llama_result.get(
                        "growth_stage_description", "N/A"
                    )
                    env_data["image_quality_score"] = llama_result.get(
                        "image_quality_score"
                    )

                    result = {
                        "image_info": item["image_info"],
                        "embedding": embedding,
                        "time_info": item["time_info"],
                        "environmental_data": env_data,
                        "processed_at": datetime.now(),
                    }

                    # 保存到数据库
                    if save_to_db:
                        success = self._save_to_database(result)
                        result["saved_to_db"] = success
                    else:
                        result["saved_to_db"] = False
                        success = True

                    results.append(
                        {"success": success, "image_info": item["image_info"]}
                    )

                except Exception as e:
                    _encoder_log(
                        "VISION_BATCH_ENV_ITEM_FAILED",
                        "批量处理单项失败",
                        level="ERROR",
                        image_name=item["image_info"].file_name,
                        room_id=item["image_info"].mushroom_id,
                        status="failed",
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
                    results.append({"success": False, "image_info": item["image_info"]})

        except Exception as e:
            _encoder_log(
                "VISION_BATCH_ENV_FAILED",
                "批量处理有环境数据图像失败，开始回退单张处理",
                level="ERROR",
                total_items=len(batch_data),
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            # 回退到单张处理
            for item in batch_data:
                try:
                    result = self.process_single_image(
                        item["image_info"], save_to_db=save_to_db
                    )
                    success = result is not None and (
                        not save_to_db or result.get("saved_to_db", False)
                    )
                    results.append(
                        {"success": success, "image_info": item["image_info"]}
                    )
                except Exception as e2:
                    _encoder_log(
                        "VISION_BATCH_ENV_FALLBACK_ITEM_FAILED",
                        "有环境数据批量处理回退单张失败",
                        level="ERROR",
                        image_name=item["image_info"].file_name,
                        room_id=item["image_info"].mushroom_id,
                        status="failed",
                        error_type=type(e2).__name__,
                        error_message=str(e2),
                    )
                    results.append({"success": False, "image_info": item["image_info"]})

        return results

    def _process_batch_without_env_data(
        self, batch_data: list[dict], save_to_db: bool
    ) -> list[dict]:
        """批量处理无环境数据的图片（纯图像编码）"""
        results = []

        try:
            # 批量图像编码
            images = [item["image"] for item in batch_data]
            embeddings = self._get_image_embeddings_batch(images)

            for i, item in enumerate(batch_data):
                embedding = embeddings[i] if i < len(embeddings) else None

                if embedding is None:
                    _encoder_log(
                        "VISION_BATCH_IMAGE_ITEM_EMBEDDING_FAILED",
                        "纯图像批量编码失败",
                        level="ERROR",
                        image_name=item["image_info"].file_name,
                        room_id=item["image_info"].mushroom_id,
                        status="failed",
                    )
                    results.append({"success": False, "image_info": item["image_info"]})
                    continue

                results.append({"success": True, "image_info": item["image_info"]})

        except Exception as e:
            _encoder_log(
                "VISION_BATCH_IMAGE_FAILED",
                "纯图像批量编码失败，开始回退单张编码",
                level="ERROR",
                total_items=len(batch_data),
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            # 回退到单张处理
            for item in batch_data:
                try:
                    embedding = self.get_image_embedding(item["image"])
                    success = embedding is not None
                    results.append(
                        {"success": success, "image_info": item["image_info"]}
                    )
                except Exception as e2:
                    _encoder_log(
                        "VISION_BATCH_IMAGE_FALLBACK_ITEM_FAILED",
                        "纯图像批量编码回退单张失败",
                        level="ERROR",
                        image_name=item["image_info"].file_name,
                        room_id=item["image_info"].mushroom_id,
                        status="failed",
                        error_type=type(e2).__name__,
                        error_message=str(e2),
                    )
                    results.append({"success": False, "image_info": item["image_info"]})

        return results

    def _get_multimodal_embeddings_batch(
        self, clip_inputs: list[dict]
    ) -> list[list[float] | None]:
        """批量获取多模态CLIP编码"""
        try:
            if not clip_inputs:
                return []

            self._ensure_clip_model_ready()
            torch_module = self._ensure_torch_imported()

            # 准备批量输入
            images = [item["image"] for item in clip_inputs]
            texts = [item["text"] for item in clip_inputs]

            # 确保所有图像为RGB格式
            processed_images = []
            for image in images:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                processed_images.append(image)

            # 批量预处理
            inputs = self.clip_processor(
                text=texts,
                images=processed_images,
                return_tensors="pt",
                padding=True,
                truncation=True,
            ).to(self.device)

            # 批量获取特征
            with torch_module.no_grad():
                image_features = self.clip_model.get_image_features(
                    pixel_values=inputs["pixel_values"]
                )
                text_features = self.clip_model.get_text_features(
                    input_ids=inputs["input_ids"],
                    attention_mask=inputs["attention_mask"],
                )

            # 批量融合特征
            image_weight = 0.7
            text_weight = 0.3

            # 确保特征是tensor格式，处理可能的BaseModelOutputWithPooling对象
            if hasattr(image_features, "last_hidden_state"):
                # Take the pooled output if available, otherwise take the CLS token
                if (
                    hasattr(image_features, "pooler_output")
                    and image_features.pooler_output is not None
                ):
                    image_features = image_features.pooler_output
                else:
                    image_features = image_features.last_hidden_state[
                        :, 0, :
                    ]  # Take the CLS token
            elif (
                hasattr(image_features, "pooler_output")
                and image_features.pooler_output is not None
            ):
                image_features = image_features.pooler_output
            elif torch_module.is_tensor(image_features):
                # Already a tensor, use as-is
                pass
            else:
                # Fallback: assume it's a tensor-like object
                image_features = image_features

            if hasattr(text_features, "last_hidden_state"):
                # Take the pooled output if available, otherwise take the CLS token
                if (
                    hasattr(text_features, "pooler_output")
                    and text_features.pooler_output is not None
                ):
                    text_features = text_features.pooler_output
                else:
                    text_features = text_features.last_hidden_state[
                        :, 0, :
                    ]  # Take the CLS token
            elif (
                hasattr(text_features, "pooler_output")
                and text_features.pooler_output is not None
            ):
                text_features = text_features.pooler_output
            elif torch_module.is_tensor(text_features):
                # Already a tensor, use as-is
                pass
            else:
                # Fallback: assume it's a tensor-like object
                text_features = text_features

            image_features_norm = image_features / image_features.norm(
                dim=-1, keepdim=True
            )
            text_features_norm = text_features / text_features.norm(
                dim=-1, keepdim=True
            )

            multimodal_features = (
                image_weight * image_features_norm + text_weight * text_features_norm
            )

            # 最终归一化并转换为列表
            embeddings = []
            for i in range(multimodal_features.shape[0]):
                embedding = multimodal_features[i].cpu().numpy()
                embedding = embedding / np.linalg.norm(embedding)
                embeddings.append(embedding.tolist())

            _encoder_log(
                "VISION_BATCH_MULTIMODAL_EMBEDDINGS_READY",
                "批量多模态编码完成",
                level="DEBUG",
                total_items=len(embeddings),
                status="success",
            )
            return embeddings

        except Exception as e:
            _encoder_log(
                "VISION_BATCH_MULTIMODAL_EMBEDDINGS_FAILED",
                "批量多模态编码失败",
                level="ERROR",
                total_items=len(clip_inputs),
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return [None] * len(clip_inputs)

    def _get_image_embeddings_batch(
        self, images: list[PILImageType]
    ) -> list[list[float] | None]:
        """批量获取纯图像CLIP编码"""
        try:
            if not images:
                return []

            self._ensure_clip_model_ready()
            torch_module = self._ensure_torch_imported()

            # 确保所有图像为RGB格式
            processed_images = []
            for image in images:
                if image.mode != "RGB":
                    image = image.convert("RGB")
                processed_images.append(image)

            # 批量预处理
            inputs = self.clip_processor(
                images=processed_images, return_tensors="pt"
            ).to(self.device)

            # 批量获取图像特征
            with torch_module.no_grad():
                image_features = self.clip_model.get_image_features(**inputs)

            # 确保特征是tensor格式，处理可能的BaseModelOutputWithPooling对象
            if hasattr(image_features, "last_hidden_state"):
                # Take the pooled output if available, otherwise take the CLS token
                if (
                    hasattr(image_features, "pooler_output")
                    and image_features.pooler_output is not None
                ):
                    image_features = image_features.pooler_output
                else:
                    image_features = image_features.last_hidden_state[
                        :, 0, :
                    ]  # Take the CLS token
            elif (
                hasattr(image_features, "pooler_output")
                and image_features.pooler_output is not None
            ):
                image_features = image_features.pooler_output
            elif torch_module.is_tensor(image_features):
                # Already a tensor, use as-is
                pass
            else:
                # Fallback: assume it's a tensor-like object
                image_features = image_features

            # 归一化并转换为列表
            embeddings = []
            for i in range(image_features.shape[0]):
                embedding = image_features[i].cpu().numpy()
                embedding = embedding / np.linalg.norm(embedding)
                embeddings.append(embedding.tolist())

            _encoder_log(
                "VISION_BATCH_IMAGE_EMBEDDINGS_READY",
                "批量图像编码完成",
                level="DEBUG",
                total_items=len(embeddings),
                status="success",
            )
            return embeddings

        except Exception as e:
            _encoder_log(
                "VISION_BATCH_IMAGE_EMBEDDINGS_FAILED",
                "批量图像编码失败",
                level="ERROR",
                total_items=len(images),
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return [None] * len(images)

    def _get_llama_descriptions_batch(self, images: list[PILImageType]) -> list[dict]:
        """批量获取LLaMA描述"""
        try:
            if not images:
                return []

            # 当前LLaMA API可能不支持批量处理，逐个处理但优化调用
            results = []
            for image in images:
                try:
                    result = self._get_llama_description(image)
                    results.append(result)
                except Exception as e:
                    _encoder_log(
                        "VISION_BATCH_LLAMA_DESCRIPTION_ITEM_FAILED",
                        "批量 LLaMA 描述单项失败",
                        level="WARNING",
                        status="failed",
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
                    results.append(
                        {
                            "growth_stage_description": "",
                            "image_quality_score": None,
                            "chinese_description": None,
                        }
                    )

            _encoder_log(
                "VISION_BATCH_LLAMA_DESCRIPTIONS_READY",
                "批量 LLaMA 描述完成",
                level="DEBUG",
                total_items=len(results),
                status="success",
            )
            return results

        except Exception as e:
            _encoder_log(
                "VISION_BATCH_LLAMA_DESCRIPTIONS_FAILED",
                "批量 LLaMA 描述失败",
                level="ERROR",
                total_items=len(images),
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return [
                {
                    "growth_stage_description": "",
                    "image_quality_score": None,
                    "chinese_description": None,
                }
            ] * len(images)

    def _insert_text_quality_record(
        self,
        session,
        image_path: str,
        embedding_id,
        room_id: str | None,
        in_date,
        collection_datetime: datetime | None,
        llama_description: str | None,
        chinese_description: str | None,
        image_quality_score: float | None,
    ) -> None:
        insert_text_quality_record(
            session,
            image_path,
            embedding_id,
            room_id,
            in_date,
            collection_datetime,
            llama_description,
            chinese_description,
            image_quality_score,
        )

    def _save_to_database(
        self,
        result: dict,
        selected_quality_record_id: int | None = None,
    ) -> bool:
        """
        保存处理结果到数据库
        只有在获取到完整环境数据时才保存

        Args:
            result: 处理结果字典

        Returns:
            是否保存成功
        """
        session = self.Session()
        try:
            image_info = result["image_info"]
            env_data = result["environmental_data"]

            _encoder_log(
                "VISION_DB_SAVE_START",
                "开始写入图像处理结果到数据库",
                room_id=image_info.mushroom_id,
                image_name=image_info.file_name,
                status="running",
            )

            # 确保有环境数据才保存
            if not env_data:
                _encoder_log(
                    "VISION_DB_SAVE_SKIPPED_NO_ENV",
                    "无环境数据，跳过数据库保存",
                    level="DEBUG",
                    room_id=image_info.mushroom_id,
                    image_name=image_info.file_name,
                    status="skipped",
                )
                return False

            # 检查是否已存在
            existing = (
                session.query(MushroomImageEmbedding)
                .filter_by(image_path=image_info.file_path)
                .first()
            )

            def _normalize_json_value(value, fallback):
                if value in (None, "", "{}", "null"):
                    return fallback
                return value

            def _is_empty_json(value) -> bool:
                return value in (None, "", "{}", "null", {})

            def _load_latest_room_config_fallback(room_id: str) -> dict:
                latest_room_record = (
                    session.query(MushroomImageEmbedding)
                    .filter(MushroomImageEmbedding.room_id == room_id)
                    .order_by(MushroomImageEmbedding.collection_datetime.desc())
                    .first()
                )
                if not latest_room_record:
                    return {}
                return {
                    "air_cooler_config": latest_room_record.air_cooler_config,
                    "fresh_fan_config": latest_room_record.fresh_fan_config,
                    "light_config": latest_room_record.light_config,
                    "humidifier_config": latest_room_record.humidifier_config,
                    "light_count": latest_room_record.light_count,
                    "humidifier_count": latest_room_record.humidifier_count,
                }

            if existing:
                # 更新现有记录
                existing.embedding = result["embedding"]
                existing.collection_datetime = result["time_info"][
                    "collection_datetime"
                ]

                # 更新环境数据字段
                existing.room_id = env_data.get("room_id", image_info.mushroom_id)
                existing.in_date = env_data.get(
                    "in_date", result["time_info"]["collection_date"].date()
                )
                existing.in_num = env_data.get("in_num", 0)
                existing.growth_day = env_data.get("growth_day", 0)
                existing.collection_ip = image_info.collection_ip

                air_cooler_config = _normalize_json_value(
                    env_data.get("air_cooler_config"), existing.air_cooler_config
                )
                fresh_fan_config = _normalize_json_value(
                    env_data.get("fresh_fan_config"), existing.fresh_fan_config
                )
                light_config = _normalize_json_value(
                    env_data.get("light_config"), existing.light_config
                )
                humidifier_config = _normalize_json_value(
                    env_data.get("humidifier_config"), existing.humidifier_config
                )
                env_sensor_status = _normalize_json_value(
                    env_data.get("env_sensor_status"), existing.env_sensor_status
                )

                existing.air_cooler_config = (
                    air_cooler_config if air_cooler_config is not None else {}
                )
                existing.fresh_fan_config = (
                    fresh_fan_config if fresh_fan_config is not None else {}
                )
                existing.light_count = env_data.get("light_count", 0)
                existing.light_config = light_config if light_config is not None else {}
                existing.humidifier_count = env_data.get("humidifier_count", 0)
                existing.humidifier_config = (
                    humidifier_config
                    if humidifier_config is not None
                    else {"left": {}, "right": {}}
                )
                existing.env_sensor_status = (
                    env_sensor_status if env_sensor_status is not None else {}
                )
                existing.semantic_description = env_data.get(
                    "semantic_description", "无环境数据。"
                )
                existing.updated_at = datetime.now()
                _encoder_log(
                    "VISION_DB_RECORD_UPDATED",
                    "已更新 mushroom_embedding 记录",
                    level="DEBUG",
                    room_id=image_info.mushroom_id,
                    image_name=image_info.file_name,
                )
            else:
                room_for_fallback = env_data.get("room_id", image_info.mushroom_id)
                fallback_config = _load_latest_room_config_fallback(room_for_fallback)

                air_cooler_config = env_data.get("air_cooler_config")
                fresh_fan_config = env_data.get("fresh_fan_config")
                light_config = env_data.get("light_config")
                humidifier_config = env_data.get("humidifier_config")

                if _is_empty_json(air_cooler_config):
                    air_cooler_config = fallback_config.get("air_cooler_config") or {}
                if _is_empty_json(fresh_fan_config):
                    fresh_fan_config = fallback_config.get("fresh_fan_config") or {}
                if _is_empty_json(light_config):
                    light_config = fallback_config.get("light_config") or {}
                if _is_empty_json(humidifier_config):
                    humidifier_config = fallback_config.get("humidifier_config") or {
                        "left": {},
                        "right": {},
                    }

                light_count = env_data.get("light_count", 0)
                humidifier_count = env_data.get("humidifier_count", 0)
                if not light_count and fallback_config.get("light_count"):
                    light_count = fallback_config.get("light_count")
                if not humidifier_count and fallback_config.get("humidifier_count"):
                    humidifier_count = fallback_config.get("humidifier_count")

                # 创建新记录
                new_record = MushroomImageEmbedding(
                    image_path=image_info.file_path,
                    collection_datetime=result["time_info"]["collection_datetime"],
                    embedding=result["embedding"],
                    room_id=room_for_fallback,
                    in_date=env_data.get(
                        "in_date", result["time_info"]["collection_date"].date()
                    ),
                    in_num=env_data.get("in_num", 0),
                    growth_day=env_data.get("growth_day", 0),
                    collection_ip=image_info.collection_ip,
                    air_cooler_config=air_cooler_config,
                    fresh_fan_config=fresh_fan_config,
                    light_count=light_count,
                    light_config=light_config,
                    humidifier_count=humidifier_count,
                    humidifier_config=humidifier_config,
                    env_sensor_status=env_data.get("env_sensor_status") or {},
                    semantic_description=env_data.get(
                        "semantic_description", "无环境数据。"
                    ),
                )

                session.add(new_record)
                _encoder_log(
                    "VISION_DB_RECORD_CREATED",
                    "已创建 mushroom_embedding 记录",
                    level="DEBUG",
                    room_id=image_info.mushroom_id,
                    image_name=image_info.file_name,
                )

            session.flush()

            embedding_id = existing.id if existing else new_record.id
            room_id = env_data.get("room_id", image_info.mushroom_id)
            in_date = env_data.get(
                "in_date", result["time_info"]["collection_date"].date()
            )
            llama_description = env_data.get("llama_description", None)
            chinese_description = env_data.get("chinese_description", None)
            image_quality_score = env_data.get("image_quality_score", None)

            if selected_quality_record_id is not None:
                quality_record = (
                    session.query(ImageTextQuality)
                    .filter(ImageTextQuality.id == selected_quality_record_id)
                    .first()
                )
                if quality_record:
                    quality_record.mushroom_embedding_id = embedding_id
                    quality_record.updated_at = func.now()
                    _encoder_log(
                        "VISION_DB_TEXT_QUALITY_LINKED",
                        "已回填 image_text_quality 与 mushroom_embedding 关联",
                        level="DEBUG",
                        room_id=image_info.mushroom_id,
                        image_name=image_info.file_name,
                        quality_record_id=selected_quality_record_id,
                    )
                else:
                    _encoder_log(
                        "VISION_DB_TEXT_QUALITY_MISSING",
                        "指定的 image_text_quality 记录不存在，跳过回填",
                        level="WARNING",
                        room_id=image_info.mushroom_id,
                        image_name=image_info.file_name,
                        quality_record_id=selected_quality_record_id,
                        status="partial",
                    )
            else:
                self._insert_text_quality_record(
                    session,
                    image_info.file_path,
                    embedding_id,
                    room_id,
                    in_date,
                    result["time_info"]["collection_datetime"],
                    llama_description,
                    chinese_description,
                    image_quality_score,
                )

            session.commit()
            _encoder_log(
                "VISION_DB_SAVE_FINISH",
                "图像处理结果写库完成",
                room_id=image_info.mushroom_id,
                image_name=image_info.file_name,
                status="success",
                stored_records=1,
            )
            return True

        except Exception as e:
            _encoder_log(
                "VISION_DB_SAVE_FAILED",
                "图像处理结果写库失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            session.rollback()
            return False
        finally:
            session.close()

    def batch_process_images(
        self,
        mushroom_id: str | None = None,
        date_filter: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        batch_size: int = 10,
    ) -> dict[str, int]:
        """
        批量处理图像

        Args:
            mushroom_id: 蘑菇库号过滤
            date_filter: 日期过滤 (YYYYMMDD)
            start_time: 开始时间过滤 (含)
            end_time: 结束时间过滤 (不含)
            batch_size: 批处理大小

        Returns:
            处理统计结果
        """
        _encoder_log(
            "VISION_BATCH_START",
            "开始批量处理图像",
            mushroom_id=mushroom_id,
            window_start=start_time.isoformat() if start_time else None,
            window_end=end_time.isoformat() if end_time else None,
            target_date=date_filter,
            status="running",
        )

        # 获取所有蘑菇图像
        all_images = self.processor.get_mushroom_images(
            mushroom_id=mushroom_id,
            date_filter=date_filter,
            start_time=start_time,
            end_time=end_time,
        )

        if not all_images:
            _encoder_log(
                "VISION_BATCH_EMPTY",
                "未找到符合条件的图像",
                level="WARNING",
                mushroom_id=mushroom_id,
                target_date=date_filter,
                status="skipped",
            )
            return {"total": 0, "success": 0, "failed": 0, "skipped": 0}

        _encoder_log(
            "VISION_BATCH_DISCOVERED",
            "已加载待处理图像列表",
            mushroom_id=mushroom_id,
            total_items=len(all_images),
            status="running",
        )

        stats = {"total": len(all_images), "success": 0, "failed": 0, "skipped": 0}

        # 分批处理
        for i in range(0, len(all_images), batch_size):
            batch = all_images[i : i + batch_size]
            _encoder_log(
                "VISION_BATCH_CHUNK_START",
                "开始处理图像批次",
                batch_index=i // batch_size + 1,
                batch_size=batch_size,
                total_items=len(batch),
                status="running",
            )

            for image_info in batch:
                try:
                    # 检查是否已处理过
                    if self._is_already_processed(image_info.file_path):
                        _encoder_log(
                            "VISION_IMAGE_SKIPPED",
                            "图像已处理，跳过",
                            level="DEBUG",
                            room_id=image_info.mushroom_id,
                            image_name=image_info.file_name,
                            skipped_items=1,
                            status="skipped",
                        )
                        stats["skipped"] += 1
                        continue

                    # 处理图像
                    result = self.process_single_image(image_info, save_to_db=True)

                    if result and result.get("saved_to_db", False):
                        stats["success"] += 1
                    else:
                        stats["failed"] += 1

                except Exception as e:
                    _encoder_log(
                        "VISION_IMAGE_PROCESS_FAILED",
                        "批处理中单张图像处理失败",
                        level="ERROR",
                        room_id=image_info.mushroom_id,
                        image_name=image_info.file_name,
                        failed_items=1,
                        status="failed",
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
                    stats["failed"] += 1

        _encoder_log(
            "VISION_BATCH_FINISH",
            "图像批量处理完成",
            total_items=stats["total"],
            successful_items=stats["success"],
            failed_items=stats["failed"],
            skipped_items=stats["skipped"],
            success_rate=round((stats["success"] / stats["total"] * 100), 2)
            if stats["total"]
            else 0.0,
            status="success" if stats["failed"] == 0 else "partial",
        )

        return stats

    def batch_process_text_quality(
        self,
        mushroom_id: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        batch_size: int = 10,
        reprocess: bool = False,
        max_images: int | None = None,
        link_mushroom_embedding: bool = True,
    ) -> dict[str, int]:
        """仅计算文本描述和图像质量评分并落库（不做图像编码）"""
        _encoder_log(
            "VISION_TEXT_QUALITY_START",
            "开始批量文本与质量分析",
            mushroom_id=mushroom_id,
            window_start=start_time.isoformat() if start_time else None,
            window_end=end_time.isoformat() if end_time else None,
            reprocess=reprocess,
            status="running",
        )

        all_images = self.processor.get_mushroom_images(
            mushroom_id=mushroom_id,
            date_filter=None,
            start_time=start_time,
            end_time=end_time,
        )

        if not all_images:
            _encoder_log(
                "VISION_TEXT_QUALITY_EMPTY",
                "未找到符合条件的图像",
                level="WARNING",
                mushroom_id=mushroom_id,
                status="skipped",
            )
            return {"total": 0, "success": 0, "failed": 0, "skipped": 0}

        # 与最近图片脚本对齐：优先处理最新图片，可选限制数量
        if max_images is not None and max_images > 0:
            all_images.sort(key=lambda img: img.collection_datetime, reverse=True)
            all_images = all_images[:max_images]

        stats = {"total": len(all_images), "success": 0, "failed": 0, "skipped": 0}

        session = self.Session()
        try:
            for i in range(0, len(all_images), batch_size):
                batch = all_images[i : i + batch_size]
                for image_info in batch:
                    try:
                        existing = None
                        time_info = self.parse_time_from_path(image_info)
                        in_date = time_info["collection_date"].date()
                        room_id = self._map_room_id(image_info.mushroom_id)

                        if not reprocess:
                            existing = (
                                session.query(ImageTextQuality)
                                .filter_by(image_path=image_info.file_path)
                                .order_by(ImageTextQuality.created_at.desc())
                                .first()
                            )
                            if existing:
                                updated = False
                                if existing.in_date != in_date:
                                    existing.in_date = in_date
                                    updated = True
                                if existing.room_id is None and room_id:
                                    existing.room_id = room_id
                                    updated = True
                                if existing.collection_datetime is None:
                                    existing.collection_datetime = time_info[
                                        "collection_datetime"
                                    ]
                                    updated = True
                                if updated:
                                    existing.updated_at = func.now()
                                    stats["success"] += 1
                                    continue
                            if (
                                existing
                                and existing.llama_description
                                and existing.image_quality_score is not None
                                and existing.chinese_description
                            ):
                                stats["skipped"] += 1
                                continue

                        image = self.minio_client.get_image(image_info.file_path)
                        if image is None:
                            stats["failed"] += 1
                            continue

                        llama_result = self._get_llama_description(image)
                        growth_stage_description = llama_result.get(
                            "growth_stage_description", ""
                        )
                        chinese_description = llama_result.get(
                            "chinese_description", None
                        )
                        quality_score = llama_result.get("image_quality_score", None)

                        embedding_id = None
                        if link_mushroom_embedding:
                            existing_embedding = (
                                session.query(MushroomImageEmbedding)
                                .filter_by(image_path=image_info.file_path)
                                .first()
                            )
                            embedding_id = (
                                existing_embedding.id if existing_embedding else None
                            )

                        if existing:
                            if not existing.chinese_description and chinese_description:
                                existing.chinese_description = chinese_description
                            if (
                                not existing.llama_description
                                and growth_stage_description
                            ):
                                existing.llama_description = growth_stage_description
                            if (
                                existing.image_quality_score is None
                                and quality_score is not None
                            ):
                                existing.image_quality_score = quality_score
                            existing.updated_at = func.now()
                            stats["success"] += 1
                            continue

                        self._insert_text_quality_record(
                            session,
                            image_info.file_path,
                            embedding_id,
                            room_id,
                            in_date,
                            time_info["collection_datetime"],
                            growth_stage_description
                            if growth_stage_description
                            else None,
                            chinese_description,
                            quality_score,
                        )
                        stats["success"] += 1
                    except Exception as e:
                        _encoder_log(
                            "VISION_TEXT_QUALITY_ITEM_FAILED",
                            "单张图像文本与质量分析失败",
                            level="ERROR",
                            room_id=image_info.mushroom_id,
                            image_name=image_info.file_name,
                            failed_items=1,
                            status="failed",
                            error_type=type(e).__name__,
                            error_message=str(e),
                        )
                        stats["failed"] += 1

                session.commit()

        except Exception as e:
            session.rollback()
            _encoder_log(
                "VISION_TEXT_QUALITY_FAILED",
                "批量文本与质量分析失败",
                level="ERROR",
                mushroom_id=mushroom_id,
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
        finally:
            session.close()

        _encoder_log(
            "VISION_TEXT_QUALITY_FINISH",
            "文本与质量分析完成",
            total_items=stats["total"],
            successful_items=stats["success"],
            failed_items=stats["failed"],
            skipped_items=stats["skipped"],
            success_rate=round((stats["success"] / stats["total"] * 100), 2)
            if stats["total"]
            else 0.0,
            status="success" if stats["failed"] == 0 else "partial",
        )
        return stats

    def process_top_quality_embeddings_for_date(
        self,
        target_date,
        top_k: int = 5,
        batch_size: int = 10,
    ) -> dict[str, int]:
        """按库房/日期选取Top-K质量图像进行编码"""
        from global_const.const_config import MUSHROOM_ROOM_IDS

        _encoder_log(
            "VISION_TOPK_START",
            "开始处理指定日期 Top-K 质量图像",
            target_date=str(target_date),
            total_items=top_k,
            batch_size=batch_size,
            status="running",
        )
        stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}

        def _normalize_path(path: str | None) -> str:
            if not path:
                return ""
            value = str(path).strip().replace("\\", "/")
            if value.startswith("mogu/"):
                value = value[5:]
            return value.lstrip("/")

        session = self.Session()
        try:
            minio_rooms = set(self.minio_client.list_rooms())

            for room_id in MUSHROOM_ROOM_IDS:
                candidate_quality_room_ids = {str(room_id)}
                for minio_room_id, env_room_id in self.room_id_mapping.items():
                    if str(env_room_id) == str(room_id):
                        candidate_quality_room_ids.add(str(minio_room_id))

                day_start = datetime.combine(target_date, datetime.min.time())
                day_end = day_start + timedelta(days=1)

                quality_rows = (
                    session.query(ImageTextQuality)
                    .filter(
                        ImageTextQuality.room_id.in_(list(candidate_quality_room_ids))
                    )
                    .filter(ImageTextQuality.image_quality_score.isnot(None))
                    .filter(ImageTextQuality.chinese_description.isnot(None))
                    .filter(
                        func.length(func.trim(ImageTextQuality.chinese_description)) > 0
                    )
                    .filter(
                        (
                            ImageTextQuality.collection_datetime.isnot(None)
                            & (ImageTextQuality.collection_datetime >= day_start)
                            & (ImageTextQuality.collection_datetime < day_end)
                        )
                        | (ImageTextQuality.in_date == target_date)
                    )
                    .order_by(
                        ImageTextQuality.image_quality_score.desc(),
                        ImageTextQuality.created_at.desc(),
                    )
                    .all()
                )

                if not quality_rows:
                    _encoder_log(
                        "VISION_TOPK_ROOM_NO_QUALITY_ROWS",
                        "当前库房无可用质量记录",
                        level="DEBUG",
                        room_id=room_id,
                        candidate_room_ids=",".join(sorted(candidate_quality_room_ids)),
                        status="skipped",
                    )
                    continue

                minio_candidates = [
                    rid for rid in candidate_quality_room_ids if rid in minio_rooms
                ]
                if not minio_candidates and str(room_id) in minio_rooms:
                    minio_candidates = [str(room_id)]

                images = []
                for minio_room_id in minio_candidates:
                    images.extend(
                        self.processor.get_mushroom_images(
                            mushroom_id=minio_room_id,
                            date_filter=target_date.strftime("%Y%m%d"),
                        )
                    )

                image_map = {img.file_path: img for img in images}
                normalized_image_map = {
                    _normalize_path(img.file_path): img for img in images
                }
                basename_image_map = {
                    Path(img.file_path).name: img for img in images if img.file_path
                }

                _encoder_log(
                    "VISION_TOPK_ROOM_SCAN_READY",
                    "已完成库房 Top-K 图像候选收集",
                    room_id=room_id,
                    total_items=len(quality_rows),
                    minio_candidate_count=len(minio_candidates),
                    minio_image_count=len(images),
                    status="running",
                )

                seen_paths = set()
                selected = 0
                for row in quality_rows:
                    if selected >= top_k:
                        break

                    if row.image_path in seen_paths:
                        continue
                    seen_paths.add(row.image_path)

                    image_info = image_map.get(row.image_path)
                    if not image_info:
                        normalized_path = _normalize_path(row.image_path)
                        image_info = normalized_image_map.get(normalized_path)
                    if not image_info and row.image_path:
                        image_info = basename_image_map.get(Path(row.image_path).name)

                    if not image_info:
                        stats["failed"] += 1
                        _encoder_log(
                            "VISION_TOPK_IMAGE_MATCH_MISSING",
                            "Top-K 图像记录未匹配到 MinIO 图像",
                            level="DEBUG",
                            room_id=room_id,
                            image_path=row.image_path,
                            status="failed",
                        )
                        continue

                    selected += 1

                    if self._is_already_processed(image_info.file_path):
                        if linked_embedding := (
                            session.query(MushroomImageEmbedding)
                            .filter_by(image_path=image_info.file_path)
                            .first()
                        ):
                            if row.mushroom_embedding_id != linked_embedding.id:
                                row.mushroom_embedding_id = linked_embedding.id
                                row.updated_at = func.now()
                                _encoder_log(
                                    "VISION_TOPK_EMBEDDING_LINK_BACKFILLED",
                                    "Top-K 已编码图像已回填 embedding 关联",
                                    level="DEBUG",
                                    room_id=room_id,
                                    quality_record_id=row.id,
                                    embedding_id=linked_embedding.id,
                                    status="success",
                                )
                        stats["skipped"] += 1
                        continue

                    precomputed = None
                    if row.llama_description:
                        precomputed = {
                            "growth_stage_description": row.llama_description,
                            "image_quality_score": row.image_quality_score,
                        }

                    result = self.process_single_image(
                        image_info,
                        save_to_db=True,
                        precomputed_analysis=precomputed,
                        selected_quality_record_id=row.id,
                    )
                    stats["total"] += 1
                    if result and result.get("saved_to_db", False):
                        stats["success"] += 1
                    else:
                        stats["failed"] += 1

            _encoder_log(
                "VISION_TOPK_FINISH",
                "Top-K 质量图像编码完成",
                total_items=stats["total"],
                successful_items=stats["success"],
                failed_items=stats["failed"],
                skipped_items=stats["skipped"],
                success_rate=round((stats["success"] / stats["total"] * 100), 2)
                if stats["total"]
                else 0.0,
                status="success" if stats["failed"] == 0 else "partial",
            )
            return stats
        finally:
            session.close()

    def _is_already_processed(self, image_path: str) -> bool:
        """检查图像是否已经处理过"""
        session = self.Session()
        try:
            existing = (
                session.query(MushroomImageEmbedding)
                .filter_by(image_path=image_path)
                .first()
            )
            return existing is not None
        except Exception as e:
            _encoder_log(
                "VISION_PROCESS_STATE_CHECK_FAILED",
                "检查图像处理状态失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return False
        finally:
            session.close()

    def get_processing_statistics(self) -> dict:
        """获取处理统计信息"""
        session = self.Session()
        try:
            from sqlalchemy import func

            _encoder_log(
                "VISION_STATS_QUERY_START",
                "开始查询图像处理统计信息",
                status="running",
            )

            # 总处理数量
            total_count = session.query(MushroomImageEmbedding).count()

            # 按库房分组统计
            room_stats = (
                session.query(
                    MushroomImageEmbedding.room_id,
                    func.count(MushroomImageEmbedding.id).label("count"),
                )
                .group_by(MushroomImageEmbedding.room_id)
                .all()
            )

            # 按生长天数分组统计（替代growth_stage）
            growth_day_stats = (
                session.query(
                    MushroomImageEmbedding.growth_day,
                    func.count(MushroomImageEmbedding.id).label("count"),
                )
                .group_by(MushroomImageEmbedding.growth_day)
                .all()
            )

            # 按日期分组统计
            date_stats = (
                session.query(
                    MushroomImageEmbedding.in_date,
                    func.count(MushroomImageEmbedding.id).label("count"),
                )
                .group_by(MushroomImageEmbedding.in_date)
                .all()
            )

            # 有环境控制策略的记录数
            with_env_control = (
                session.query(MushroomImageEmbedding)
                .filter(MushroomImageEmbedding.semantic_description != "无环境数据。")
                .count()
            )

            # 补光灯使用统计
            light_usage = (
                session.query(
                    MushroomImageEmbedding.light_count,
                    func.count(MushroomImageEmbedding.id).label("count"),
                )
                .group_by(MushroomImageEmbedding.light_count)
                .all()
            )

            stats = {
                "total_processed": total_count,
                "with_environmental_control": with_env_control,
                "room_distribution": {
                    str(room_id): count for room_id, count in room_stats
                },
                "growth_day_distribution": {
                    day: count for day, count in growth_day_stats
                },
                "date_distribution": {str(date): count for date, count in date_stats},
                "light_usage_distribution": {
                    f"light_{count}": usage for count, usage in light_usage
                },
                "processing_time": datetime.now().isoformat(),
            }

            _encoder_log(
                "VISION_STATS_QUERY_FINISH",
                "图像处理统计信息查询完成",
                status="success",
                total_items=total_count,
                stored_records=total_count,
            )
            return stats

        except Exception as e:
            _encoder_log(
                "VISION_STATS_QUERY_FAILED",
                "获取图像处理统计信息失败",
                level="ERROR",
                status="failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {}
        finally:
            session.close()

    def validate_system_with_limited_samples(
        self, max_per_mushroom: int = 3
    ) -> dict[str, Any]:
        """
        验证系统功能，每个蘑菇库房最多处理指定数量的图像
        只有在获取到完整数据时才存储到数据库

        Args:
            max_per_mushroom: 每个蘑菇库房最多处理的图像数量

        Returns:
            验证结果统计
        """
        _encoder_log(
            "VISION_VALIDATION_START",
            "开始执行系统抽样验证",
            total_items=max_per_mushroom,
            status="running",
        )

        # 获取所有图像并按库房分组
        all_images = self.processor.get_mushroom_images()
        mushroom_groups = {}

        for img in all_images:
            if img.mushroom_id not in mushroom_groups:
                mushroom_groups[img.mushroom_id] = []
            mushroom_groups[img.mushroom_id].append(img)

        _encoder_log(
            "VISION_VALIDATION_ROOMS_READY",
            "已完成库房分组，准备执行抽样验证",
            total_items=len(mushroom_groups),
            room_ids=sorted(mushroom_groups.keys()),
            status="running",
        )

        validation_results = {
            "mushroom_ids": sorted(mushroom_groups.keys()),
            "total_mushrooms": len(mushroom_groups),
            "processed_per_mushroom": {},
            "total_processed": 0,
            "total_success": 0,
            "total_failed": 0,
            "total_skipped": 0,
            "total_no_env_data": 0,
        }

        # 对每个库房处理有限数量的图像
        for mushroom_id in sorted(mushroom_groups.keys()):
            _encoder_log(
                "VISION_VALIDATION_ROOM_START",
                "开始验证单个库房",
                room_id=mushroom_id,
                status="running",
            )

            images = mushroom_groups[mushroom_id]
            processed_count = 0
            success_count = 0
            failed_count = 0
            skipped_count = 0
            no_env_data_count = 0

            # 找到未处理的图像
            for img in images:
                if processed_count >= max_per_mushroom:
                    break

                try:
                    # 检查是否已处理
                    if self._is_already_processed(img.file_path):
                        skipped_count += 1
                        _encoder_log(
                            "VISION_VALIDATION_IMAGE_SKIPPED",
                            "验证样本已处理，跳过",
                            room_id=mushroom_id,
                            image_name=img.file_name,
                            skipped_items=1,
                            status="skipped",
                        )
                        continue

                    # 处理图像
                    _encoder_log(
                        "VISION_VALIDATION_IMAGE_START",
                        "开始处理验证样本",
                        room_id=mushroom_id,
                        image_name=img.file_name,
                        status="running",
                    )
                    result = self.process_single_image(img, save_to_db=True)

                    if result:
                        if result.get("saved_to_db", False):
                            success_count += 1
                            _encoder_log(
                                "VISION_VALIDATION_IMAGE_SUCCESS",
                                "验证样本处理并保存成功",
                                room_id=mushroom_id,
                                image_name=img.file_name,
                                status="success",
                                stored_records=1,
                            )
                        elif result.get("skip_reason") == "no_environment_data":
                            no_env_data_count += 1
                            _encoder_log(
                                "VISION_VALIDATION_IMAGE_NO_ENV",
                                "验证样本处理完成但无环境数据",
                                level="WARNING",
                                room_id=mushroom_id,
                                image_name=img.file_name,
                                status="partial",
                            )
                        else:
                            failed_count += 1
                            _encoder_log(
                                "VISION_VALIDATION_IMAGE_FAILED",
                                "验证样本处理失败",
                                level="ERROR",
                                room_id=mushroom_id,
                                image_name=img.file_name,
                                status="failed",
                            )
                    else:
                        failed_count += 1
                        _encoder_log(
                            "VISION_VALIDATION_IMAGE_NONE",
                            "验证样本处理返回空结果",
                            level="ERROR",
                            room_id=mushroom_id,
                            image_name=img.file_name,
                            status="failed",
                        )

                    processed_count += 1

                except Exception as e:
                    failed_count += 1
                    _encoder_log(
                        "VISION_VALIDATION_IMAGE_EXCEPTION",
                        "验证样本处理异常",
                        level="ERROR",
                        room_id=mushroom_id,
                        image_name=img.file_name,
                        status="failed",
                        error_type=type(e).__name__,
                        error_message=str(e),
                    )
                    processed_count += 1

            # 记录该库房的结果
            validation_results["processed_per_mushroom"][mushroom_id] = {
                "processed": processed_count,
                "success": success_count,
                "failed": failed_count,
                "skipped": skipped_count,
                "no_env_data": no_env_data_count,
                "total_images": len(images),
            }

            validation_results["total_processed"] += processed_count
            validation_results["total_success"] += success_count
            validation_results["total_failed"] += failed_count
            validation_results["total_skipped"] += skipped_count
            validation_results["total_no_env_data"] += no_env_data_count

            _encoder_log(
                "VISION_VALIDATION_ROOM_FINISH",
                "单个库房抽样验证完成",
                room_id=mushroom_id,
                status="success" if failed_count == 0 else "partial",
                total_items=processed_count,
                successful_items=success_count,
                failed_items=failed_count,
                skipped_items=skipped_count,
                no_env_data=no_env_data_count,
            )

        _encoder_log(
            "VISION_VALIDATION_FINISH",
            "系统抽样验证完成",
            status="success" if validation_results["total_failed"] == 0 else "partial",
            total_items=validation_results["total_processed"],
            successful_items=validation_results["total_success"],
            failed_items=validation_results["total_failed"],
            skipped_items=validation_results["total_skipped"],
            no_env_data=validation_results["total_no_env_data"],
        )

        return validation_results


def create_mushroom_encoder(load_clip: bool = True) -> MushroomImageEncoder:
    """创建蘑菇图像编码器实例"""
    return MushroomImageEncoder(load_clip=load_clip)


if __name__ == "__main__":
    try:
        # Initialize encoder
        encoder = create_mushroom_encoder()
        _encoder_log(
            "VISION_ENCODER_SELFTEST_INIT",
            "编码器自检初始化成功",
            status="success",
        )

        # Test system validation with limited samples
        _encoder_log(
            "VISION_ENCODER_SELFTEST_VALIDATION_START",
            "开始执行 limited validation 自检",
            status="running",
        )
        validation_results = encoder.validate_system_with_limited_samples(
            max_per_mushroom=2
        )

        _encoder_log(
            "VISION_ENCODER_SELFTEST_VALIDATION_FINISH",
            "limited validation 自检完成",
            total_mushrooms=validation_results["total_mushrooms"],
            mushroom_ids=",".join(
                str(item) for item in validation_results["mushroom_ids"]
            ),
            total_items=validation_results["total_processed"],
            successful_items=validation_results["total_success"],
            failed_items=validation_results["total_failed"],
            skipped_items=validation_results["total_skipped"],
            no_env_data=validation_results["total_no_env_data"],
            status="success" if validation_results["total_failed"] == 0 else "partial",
        )

        for mushroom_id, stats in validation_results["processed_per_mushroom"].items():
            _encoder_log(
                "VISION_ENCODER_SELFTEST_ROOM_BREAKDOWN",
                "limited validation 库房明细",
                level="DEBUG",
                room_id=mushroom_id,
                processed_items=stats["processed"],
                successful_items=stats["success"],
                failed_items=stats["failed"],
                no_env_data=stats["no_env_data"],
            )

        # Get processing statistics
        processing_stats = encoder.get_processing_statistics()
        _encoder_log(
            "VISION_ENCODER_SELFTEST_STATS",
            "获取编码器处理统计",
            total_items=processing_stats.get("total_processed", 0),
            with_environmental_control=processing_stats.get(
                "with_environmental_control", 0
            ),
            status="success",
        )

        _encoder_log(
            "VISION_ENCODER_SELFTEST_FINISH",
            "编码器自检完成",
            status="success",
        )

    except Exception as e:
        _encoder_log(
            "VISION_ENCODER_SELFTEST_FAILED",
            "编码器自检失败",
            level="ERROR",
            status="failed",
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=traceback.format_exc(),
        )
        sys.exit(1)

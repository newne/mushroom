"""
离线图文处理核心模块
"""
import io
import base64
import json
import re
import time
import uuid
import requests
from typing import Dict, Any, Optional
from PIL import Image
from loguru import logger

import mlflow
from .config import config
from .data_loader import DataLoader
from .mlflow_logger import MLflowLogger

class OfflineProcessor:
    """离线图文处理器"""

    def __init__(self):
        self.data_loader = DataLoader()
        self.mlflow_logger = MLflowLogger()
        self.llama_config = config.llama_vl_config

        self.prompt = None
        self.prompt_text = getattr(self.llama_config, "mushroom_descripe_prompt", "")
        if not self.prompt_text:
            self.prompt = self._load_prompt_from_registry()
        
        # 性能统计
        self.total_processed = 0
        self.failed_count = 0

    def _load_prompt_from_registry(self, prompt_name="growth_stage_describe", version="3"):
        """Loads prompt from MLflow Registry"""
        try:
            mlflow.set_tracking_uri(config.mlflow_tracking_uri)
            logger.info(f"Loading prompt {prompt_name} (version {version}) from MLflow Registry...")
            return mlflow.genai.load_prompt(f"prompts:/{prompt_name}/{version}")
        except Exception as e:
            logger.warning(f"Failed to load prompt from registry: {e}. Using fallback.")
            return None

    def process_daily_batch(self, limit_per_room_day: int = 2, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """
        执行每日批量处理任务
        """
        logger.info(f"Starting batch processing with limit {limit_per_room_day} per room/day. Date range: {start_date} to {end_date}")
        
        # 1. 获取元数据
        try:
            items = self.data_loader.get_random_images_metadata(
                limit_per_room_day=limit_per_room_day,
                start_date=start_date,
                end_date=end_date
            )
        except Exception as e:
            logger.error(f"Failed to get metadata: {e}")
            return

        # 2. 遍历处理
        for item in items:
            image_path = item['image_path']
            room_id = item['room_id']
            collection_datetime = item['collection_datetime']
            
            logger.info(f"Processing {image_path} (Room: {room_id})")
            
            try:
                # 下载图片
                image = self.data_loader.download_image(image_path)
                if image is None:
                    self.failed_count += 1
                    continue
                
                # 预处理
                processed_image = self._preprocess_image(image)
                
                # 调用LLaMA生成描述
                description_result = self._generate_description(processed_image)
                
                # 记录到MLflow
                self.mlflow_logger.log_analysis_result(
                    image=image, # 记录原始图片
                    image_name=f"{room_id}_{image_path.replace('/', '_')}",
                    chinese_desc=description_result.get("chinese_description", "") or "N/A",
                    english_desc=description_result.get("growth_stage_description", "") or "N/A",
                    metadata={
                        "room_id": room_id,
                        "collection_datetime": str(collection_datetime),
                        "original_path": image_path,
                        "quality_score": description_result.get("image_quality_score")
                    }
                )
                
                self.total_processed += 1
                
            except Exception as e:
                logger.error(f"Error processing {image_path}: {e}")
                self.failed_count += 1
                
        logger.info(f"Batch processing completed. Total: {self.total_processed}, Failed: {self.failed_count}")

    def _preprocess_image(self, image: Image.Image) -> Image.Image:
        """图片预处理：格式转换、尺寸调整"""
        # 确保RGB
        if image.mode != "RGB":
            image = image.convert("RGB")
            
        # 调整尺寸
        target_w = getattr(self.llama_config, "image_width", 960)
        target_h = getattr(self.llama_config, "image_height", 960)
        
        if image.size != (target_w, target_h):
            image = image.resize((target_w, target_h), Image.Resampling.LANCZOS)
            
        return image

    def _generate_description(self, image: Image.Image) -> Dict[str, Any]:
        """调用LLaMA API生成描述"""
        # Base64编码
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85)
        image_data = base64.b64encode(buffer.getvalue()).decode("utf-8")
        
        if self.prompt is not None:
            prompt_messages = self.prompt.format(image_input="[image attached]")
        else:
            prompt_messages = [
                {"role": "system", "content": self.prompt_text or "Describe the mushroom growth stage in JSON format."},
                {"role": "user", "content": "Please analyze the image and generate the JSON output."},
            ]

        model = getattr(self.llama_config, "model", "qwen3-vl-2b-instruct")

        openai_messages = []
        last_user_index = None
        for msg in prompt_messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                openai_messages.append({"role": "system", "content": str(content)})
            elif role == "user":
                openai_messages.append({"role": "user", "content": str(content)})
                last_user_index = len(openai_messages) - 1
            elif role == "assistant":
                openai_messages.append({"role": "assistant", "content": str(content)})
            else:
                openai_messages.append({"role": str(role or "user"), "content": str(content)})
                if role == "user":
                    last_user_index = len(openai_messages) - 1

        if last_user_index is None:
            openai_messages.append({"role": "user", "content": ""})
            last_user_index = len(openai_messages) - 1

        user_content = openai_messages[last_user_index].get("content", "")
        if isinstance(user_content, list):
            typed_user_content = user_content
        else:
            typed_user_content = [{"type": "text", "text": str(user_content)}]
        typed_user_content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
        )
        openai_messages[last_user_index]["content"] = typed_user_content

        payload = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": getattr(self.llama_config, "max_tokens", 1024),
            "temperature": getattr(self.llama_config, "temperature", 0.7),
            "top_p": getattr(self.llama_config, "top_p", 0.9),
            "stream": False,
        }
        
        # 构建URL
        host = getattr(self.llama_config, "llama_host", "localhost")
        port = getattr(self.llama_config, "llama_port", "7001")
        base_url_template = getattr(self.llama_config, "llama_completions", "http://{0}:{1}/v1/chat/completions")
        url = base_url_template.format(host, port)
        
        headers = {"Content-Type": "application/json"}
        # 简单处理API Key，假设配置中有
        api_key = getattr(self.llama_config, "api_key", None)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # 重试机制
        max_retries = 3
        for i in range(max_retries):
            try:
                response = requests.post(url, json=payload, headers=headers, timeout=getattr(self.llama_config, "timeout", 60))
                response.raise_for_status()
                
                result = response.json()
                content = result["choices"][0]["message"]["content"]
                return self._parse_llama_content(content)
                    
            except Exception as e:
                logger.warning(f"LLaMA API call failed (attempt {i+1}/{max_retries}): {e}")
                time.sleep(1)
        
        raise RuntimeError("Failed to generate description after retries")

    def _parse_llama_content(self, content: str) -> Dict[str, Any]:
        if not content or not content.strip():
            return {
                "growth_stage_description": "",
                "chinese_description": None,
                "image_quality_score": None
            }

        cleaned = content.strip().replace("\ufeff", "")
        candidates = []

        code_block_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
        if code_block_match:
            candidates.append(code_block_match.group(1).strip())

        candidates.append(cleaned)

        left = cleaned.find("{")
        right = cleaned.rfind("}")
        if left != -1 and right != -1 and right > left:
            candidates.append(cleaned[left:right + 1])

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue

            if not isinstance(parsed, dict):
                continue

            # Basic validation/normalization for core fields
            if "image_quality_score" in parsed:
                qs = parsed["image_quality_score"]
                if isinstance(qs, (int, float)):
                    parsed["image_quality_score"] = max(0, min(100, qs))
                else:
                    parsed["image_quality_score"] = None
            
            # Ensure core fields exist
            parsed.setdefault("growth_stage_description", "")
            parsed.setdefault("chinese_description", None)
            parsed.setdefault("image_quality_score", None)

            return parsed

        logger.warning("LLaMA response is not valid JSON, returning raw text structure")
        return {
            "growth_stage_description": content,
            "chinese_description": None,
            "image_quality_score": None
        }

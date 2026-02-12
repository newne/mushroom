import base64
import os
import json
import pandas as pd
from typing import List, Dict, Any, Union, Optional, Tuple
from openai import OpenAI
from .config import config

class Runner:
    """
    Wrapper for OpenAI client to execute prompts against LLaMA-VL model.
    """
    def __init__(self):
        self.client = OpenAI(
            base_url=config.base_url,
            api_key=config.api_key
        )
        self.model = config.model

    def _encode_image(self, image_path: str) -> str:
        """Encodes an image to base64 with resizing."""
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image not found: {image_path}")
            
        try:
            from PIL import Image
            import io
            
            with Image.open(image_path) as img:
                # Resize if too large (e.g. > 1024px)
                max_size = 1024
                if max(img.size) > max_size:
                    img.thumbnail((max_size, max_size))
                
                # Convert to RGB
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                    
                buffered = io.BytesIO()
                img.save(buffered, format="JPEG", quality=85)
                return base64.b64encode(buffered.getvalue()).decode('utf-8')
        except ImportError:
            # Fallback if PIL is not available
            with open(image_path, "rb") as image_file:
                return base64.b64encode(image_file.read()).decode('utf-8')

    def _build_messages(
        self,
        prompt: Union[str, List[Dict[str, Any]], None],
        image_path: Optional[str],
        variables: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        variables = variables or {}

        if prompt is None:
            prompt = "Describe this image."

        if isinstance(prompt, str):
            prompt_content = prompt
            try:
                prompt_content = prompt_content.format(**variables)
            except Exception:
                pass

            user_content: List[Dict[str, Any]] = [{"type": "text", "text": prompt_content}]
            if image_path:
                base64_image = self._encode_image(image_path)
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    }
                )
            return [{"role": "user", "content": user_content}]

        if isinstance(prompt, list):
            messages: List[Dict[str, Any]] = []
            last_user_index: Optional[int] = None

            for msg in prompt:
                role = msg.get("role")
                content = msg.get("content", "")
                if role == "system":
                    messages.append({"role": "system", "content": str(content)})
                elif role == "user":
                    messages.append({"role": "user", "content": str(content)})
                    last_user_index = len(messages) - 1
                elif role == "assistant":
                    messages.append({"role": "assistant", "content": str(content)})
                else:
                    messages.append({"role": str(role or "user"), "content": str(content)})
                    if role == "user":
                        last_user_index = len(messages) - 1

            if image_path:
                if last_user_index is None:
                    messages.append({"role": "user", "content": ""})
                    last_user_index = len(messages) - 1

                user_msg = messages[last_user_index]
                user_content = user_msg.get("content", "")
                if isinstance(user_content, list):
                    typed_content = user_content
                else:
                    typed_content = [{"type": "text", "text": str(user_content)}]

                base64_image = self._encode_image(image_path)
                typed_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"},
                    }
                )
                user_msg["content"] = typed_content

            return messages

        return [{"role": "user", "content": [{"type": "text", "text": str(prompt)}]}]

    def predict(self, inputs: pd.DataFrame) -> List[str]:
        """
        Batch prediction method compatible with MLflow evaluate.
        Expects a DataFrame with 'image_path' and prompt variables.
        The prompt template should be supplied via context or columns.
        
        If 'prompt' column exists, it uses it as the full prompt.
        Otherwise, it expects 'prompt_template' and variable columns.
        """
        results = []
        for _, row in inputs.iterrows():
            image_path = row.get('image_path')

            prompt: Union[str, List[Dict[str, Any]], None]
            if "prompt_messages" in row and row["prompt_messages"] is not None:
                prompt = row["prompt_messages"]
                if isinstance(prompt, str):
                    try:
                        prompt = json.loads(prompt)
                    except Exception:
                        prompt = str(prompt)
            elif "prompt" in row and row["prompt"] is not None:
                prompt = row["prompt"]
            elif "prompt_template" in row and row["prompt_template"] is not None:
                prompt = row["prompt_template"]
            else:
                prompt = None

            variables = row.to_dict()
            messages = self._build_messages(prompt=prompt, image_path=image_path, variables=variables)

            max_tokens = int(row.get("max_tokens", 1024) or 1024)
            temperature = float(row.get("temperature", 0.7) or 0.7)
            prompt_uri = row.get("prompt_uri")

            try:
                try:
                    import mlflow
                    from mlflow.tracing.utils.prompt import TraceTagKey
                    import json as _json

                    prompt_tag_value = None
                    if prompt_uri and isinstance(prompt_uri, str) and prompt_uri.startswith("prompts:/"):
                        parts = prompt_uri[len("prompts:/") :].strip("/").split("/")
                        if len(parts) >= 2:
                            name = parts[0]
                            version = parts[1]
                            prompt_tag_value = _json.dumps([{"name": name, "version": str(version)}], ensure_ascii=False)

                    with mlflow.start_span(name="llm_inference", span_type="LLM"):
                        if prompt_tag_value is not None:
                            mlflow.update_current_trace(tags={TraceTagKey.LINKED_PROMPTS: prompt_tag_value})
                        response = self.client.chat.completions.create(
                            model=self.model,
                            messages=messages,
                            max_tokens=max_tokens,
                            temperature=temperature,
                        )
                except Exception:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                results.append(response.choices[0].message.content)
            except Exception as e:
                results.append(f"Error: {str(e)}")
                
        return results

    def predict_with_usage(self, inputs: pd.DataFrame) -> Tuple[List[str], List[Dict[str, Any]]]:
        results: List[str] = []
        usages: List[Dict[str, Any]] = []
        for _, row in inputs.iterrows():
            image_path = row.get("image_path")

            prompt: Union[str, List[Dict[str, Any]], None]
            if "prompt_messages" in row and row["prompt_messages"] is not None:
                prompt = row["prompt_messages"]
                if isinstance(prompt, str):
                    try:
                        prompt = json.loads(prompt)
                    except Exception:
                        prompt = str(prompt)
            elif "prompt" in row and row["prompt"] is not None:
                prompt = row["prompt"]
            elif "prompt_template" in row and row["prompt_template"] is not None:
                prompt = row["prompt_template"]
            else:
                prompt = None

            variables = row.to_dict()
            messages = self._build_messages(prompt=prompt, image_path=image_path, variables=variables)
            max_tokens = int(row.get("max_tokens", 1024) or 1024)
            temperature = float(row.get("temperature", 0.7) or 0.7)
            prompt_uri = row.get("prompt_uri")

            try:
                try:
                    import mlflow
                    from mlflow.tracing.utils.prompt import TraceTagKey
                    import json as _json

                    prompt_tag_value = None
                    if prompt_uri and isinstance(prompt_uri, str) and prompt_uri.startswith("prompts:/"):
                        parts = prompt_uri[len("prompts:/") :].strip("/").split("/")
                        if len(parts) >= 2:
                            name = parts[0]
                            version = parts[1]
                            prompt_tag_value = _json.dumps([{"name": name, "version": str(version)}], ensure_ascii=False)

                    with mlflow.start_span(name="llm_inference", span_type="LLM"):
                        if prompt_tag_value is not None:
                            mlflow.update_current_trace(tags={TraceTagKey.LINKED_PROMPTS: prompt_tag_value})
                        response = self.client.chat.completions.create(
                            model=self.model,
                            messages=messages,
                            max_tokens=max_tokens,
                            temperature=temperature,
                        )
                except Exception:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                results.append(response.choices[0].message.content)
                usage = getattr(response, "usage", None)
                usages.append(
                    {
                        "prompt_tokens": getattr(usage, "prompt_tokens", None),
                        "completion_tokens": getattr(usage, "completion_tokens", None),
                        "total_tokens": getattr(usage, "total_tokens", None),
                        "finish_reason": getattr(response.choices[0], "finish_reason", None),
                    }
                )
            except Exception as e:
                results.append(f"Error: {str(e)}")
                usages.append({"error": str(e)})

        return results, usages

    def run_single(self, image_path: str, prompt_template: str, variables: Dict[str, Any] = None) -> str:
        """Helper for single run"""
        if variables is None:
            variables = {}
        
        prompt = prompt_template.format(**variables)
        df = pd.DataFrame([{
            'image_path': image_path,
            'prompt': prompt
        }])
        return self.predict(df)[0]

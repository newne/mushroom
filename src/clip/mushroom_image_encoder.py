"""
蘑菇图像编码器
使用CLIP模型对MinIO中的蘑菇图像进行编码，解析时间信息，并获取对应的环境参数
集成LLaMA模型获取蘑菇生长情况描述
"""

import base64
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Any

import numpy as np
import requests
import torch
from PIL import Image
from loguru import logger
from sqlalchemy.orm import sessionmaker
from transformers import CLIPProcessor, CLIPModel

from global_const.global_const import pgsql_engine, settings
from utils.create_table import MushroomImageEmbedding
from utils.env_data_processor import create_env_data_processor
from utils.get_data import GetData
from utils.minio_client import create_minio_client
from .mushroom_image_processor import create_mushroom_processor, MushroomImageInfo


class MushroomImageEncoder:
    """蘑菇图像编码器类"""
    
    def __init__(self):
        """初始化编码器"""
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.debug(f"设备: {self.device}")
        
        # 初始化CLIP模型
        self._init_clip_model()
        
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
            port=settings.host.port
        )
        
        # 初始化LLaMA客户端
        self._init_llama_client()
        
        # 库房号映射：MinIO中的库房号 -> 环境配置中的库房号
        self.room_id_mapping = {
            '7': '607',   # MinIO中的7对应环境配置中的607
            '8': '608',   # MinIO中的8对应环境配置中的608
        }
        
        logger.debug("图像编码器初始化完成")
    
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
            logger.debug(f"Mapped room ID: {room_id} -> {mapped_id}")
        return mapped_id
    
    def _init_clip_model(self):
        """初始化CLIP模型"""
        # 检查本地模型路径
        # 在容器中，src目录内容被复制到/app，models挂载到/models
        # 在开发环境中，保持原有的相对路径计算
        
        # 首先检查容器环境的路径
        container_model_path = Path('/models/clip-vit-base-patch32')
        
        # 然后检查开发环境的路径
        local_model_path = Path(__file__).parent.parent.parent / 'models' / 'clip-vit-base-patch32'
        
        if container_model_path.exists():
            model_name = str(container_model_path)
        elif local_model_path.exists():
            model_name = str(local_model_path)
        else:
            model_name = 'openai/clip-vit-base-patch32'
        
        logger.debug(f"加载CLIP模型: {model_name}")
        self.clip_processor = CLIPProcessor.from_pretrained(model_name)
        self.clip_model = CLIPModel.from_pretrained(model_name).to(self.device)
        self.clip_model.eval()
        logger.debug(f"CLIP模型加载完成")
    
    def _init_env_processor(self):
        """初始化环境数据处理器"""
        try:
            self.env_processor = create_env_data_processor()
            logger.debug("环境数据处理器初始化完成")
        except Exception as e:
            logger.warning(f"环境数据处理器初始化失败: {e}")
            self.env_processor = None
    
    def _init_llama_client(self):
        """初始化LLaMA客户端"""
        try:
            # 检查是否启用LLaMA
            if hasattr(settings.llama, 'enabled') and not settings.llama.enabled:
                logger.debug("LLaMA已禁用")
                self.llama_client = False
                return
                
            # 标记LLaMA客户端可用
            self.llama_client = True
            logger.debug("LLaMA客户端初始化完成")
        except Exception as e:
            logger.warning(f"LLaMA客户端初始化失败: {e}")
            self.llama_client = False
    
    def _call_llama_api(self, image_data: str) -> str:
        """
        直接调用LLaMA API
        
        Args:
            image_data: base64编码的图像数据
            
        Returns:
            LLaMA生成的描述
        """
        try:
            # 从API动态获取提示词，如果失败则使用配置文件中的默认值
            prompt = self.get_data.get_mushroom_prompt()
            if not prompt:
                logger.warning("[LLAMA-API] 无法获取提示词，使用配置文件中的默认值")
                prompt = settings.llama.mushroom_descripe_prompt
            
            payload = {
                "model": f"{settings.llama.model}",
                "messages": [
                    {
                        "role": "system",
                        "content": prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}}
                        ]
                    }
                ],
                "max_tokens": -1,
                "temperature": 0.7,
                "stream": False
            }

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {settings.llama.api_key}"
            }
            
            base_url = settings.llama.llama_completions.format(settings.llama.llama_host, settings.llama.llama_port)
            
            # 从配置获取超时时间，默认600秒
            timeout = getattr(settings.llama, 'timeout', 600)
            
            # 使用requests直接发送请求，使用配置的超时时间
            resp = requests.post(base_url, json=payload, headers=headers, timeout=timeout)
            
            if resp.status_code == 200:
                response_data = resp.json()
                content = response_data["choices"][0]["message"]["content"]
                
                # 解析JSON响应
                try:
                    # 尝试直接解析JSON
                    llama_result = json.loads(content)
                    
                    # 验证必需字段
                    if "growth_stage_description" not in llama_result or "image_quality_score" not in llama_result:
                        logger.error(f"[LLAMA-001] 响应缺少必需字段 | 字段: {list(llama_result.keys())}")
                        return {"growth_stage_description": "", "image_quality_score": None}
                    
                    # 验证数据类型
                    description = str(llama_result["growth_stage_description"])
                    quality_score = llama_result["image_quality_score"]
                    
                    # 验证质量评分范围
                    if not isinstance(quality_score, (int, float)):
                        logger.warning(f"[LLAMA-002] 质量评分类型无效 | 类型: {type(quality_score)}")
                        quality_score = None
                    elif quality_score < 0 or quality_score > 100:
                        logger.warning(f"[LLAMA-003] 质量评分超出范围 | 评分: {quality_score}")
                        quality_score = max(0, min(100, quality_score))
                    
                    logger.debug(f"LLaMA解析成功: 质量评分={quality_score}")
                    return {"growth_stage_description": description, "image_quality_score": quality_score}
                    
                except json.JSONDecodeError as e:
                    logger.error(f"[LLAMA-004] JSON解析失败 | 错误: {e}")
                    return {"growth_stage_description": "", "image_quality_score": None}
                except KeyError as e:
                    logger.error(f"[LLAMA-005] 响应缺少键 | 键: {e}")
                    return {"growth_stage_description": "", "image_quality_score": None}
            else:
                logger.error(f"[LLAMA-006] API调用失败 | 状态码: {resp.status_code}")
                return {"growth_stage_description": "", "image_quality_score": None}
                
        except requests.exceptions.Timeout:
            logger.warning(f"[LLAMA-007] API超时 | 超时时间: {getattr(settings.llama, 'timeout', 600)}秒")
            return {"growth_stage_description": "", "image_quality_score": None}
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"[LLAMA-008] 连接错误 | 错误: {e}")
            return {"growth_stage_description": "", "image_quality_score": None}
        except Exception as e:
            logger.error(f"[LLAMA-009] 调用异常 | 错误: {e}")
            return {"growth_stage_description": "", "image_quality_score": None}
    
    def _resize_image_for_llama(self, image: Image.Image) -> Image.Image:
        """
        将图像缩放到960x540分辨率用于LLaMA处理，减少运算量
        
        Args:
            image: 原始PIL图像对象
            
        Returns:
            缩放后的PIL图像对象
        """
        try:
            # 目标分辨率：短边960，长边按比例缩放
            original_width, original_height = image.size
            
            # 计算缩放比例，使短边为960
            if original_width < original_height:
                # 宽度是短边
                scale_ratio = 960 / original_width
                new_width = 960
                new_height = int(original_height * scale_ratio)
            else:
                # 高度是短边
                scale_ratio = 960 / original_height
                new_height = 960
                new_width = int(original_width * scale_ratio)
            
            # 如果新尺寸超过原尺寸，则不放大，保持原尺寸
            if new_width > original_width or new_height > original_height:
                logger.debug(f"Image already smaller than target size, keeping original: {original_width}x{original_height}")
                return image
            
            # 使用高质量重采样进行缩放
            resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
            logger.debug(f"Resized image for LLaMA: {original_width}x{original_height} -> {new_width}x{new_height}")
            
            return resized_image
            
        except Exception as e:
            logger.warning(f"Failed to resize image for LLaMA, using original: {e}")
            return image
    
    def _get_llama_description(self, image: Image.Image) -> Dict[str, Any]:
        """
        使用LLaMA模型获取蘑菇生长情况描述和图像质量评分
        
        Args:
            image: PIL图像对象
            
        Returns:
            包含growth_stage_description和image_quality_score的字典
            格式: {"growth_stage_description": str, "image_quality_score": float or None}
        """
        if not self.llama_client:
            logger.warning("LLaMA client not available, skipping description generation")
            return {"growth_stage_description": "", "image_quality_score": None}
        
        try:
            # 为LLaMA处理缩放图像（减少运算量）
            resized_image = self._resize_image_for_llama(image)
            
            # 将缩放后的PIL图像转换为base64编码
            buffer = io.BytesIO()
            resized_image.save(buffer, format='JPEG', quality=85)  # 使用适中的质量以平衡文件大小和质量
            image_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
            
            # 调用LLaMA API
            result = self._call_llama_api(image_data)
            logger.debug(f"LLaMA result: description='{result.get('growth_stage_description', '')[:50]}...', quality_score={result.get('image_quality_score')}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to get LLaMA description: {e}")
            return {"growth_stage_description": "", "image_quality_score": None}
    
    def get_multimodal_embedding(self, image: Image.Image, text_description: str) -> Optional[List[float]]:
        """
        获取图像和文本的多模态CLIP向量编码
        
        Args:
            image: PIL图像对象
            text_description: 环境数据的语义描述文本
            
        Returns:
            512维联合向量列表，失败返回None
        """
        try:
            # 确保图像为RGB格式
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # 同时预处理图像和文本
            inputs = self.clip_processor(
                text=text_description,
                images=image, 
                return_tensors="pt", 
                padding=True,
                truncation=True
            ).to(self.device)
            
            # 获取图像和文本特征
            with torch.no_grad():
                image_features = self.clip_model.get_image_features(pixel_values=inputs['pixel_values'])
                text_features = self.clip_model.get_text_features(
                    input_ids=inputs['input_ids'],
                    attention_mask=inputs['attention_mask']
                )
            
            # 多模态特征融合 - 使用加权平均
            # 图像特征权重0.7，文本特征权重0.3（可根据实际效果调整）
            image_weight = 0.7
            text_weight = 0.3
            
            # 归一化各自的特征
            image_features_norm = image_features / image_features.norm(dim=-1, keepdim=True)
            text_features_norm = text_features / text_features.norm(dim=-1, keepdim=True)
            
            # 加权融合
            multimodal_features = (image_weight * image_features_norm + 
                                 text_weight * text_features_norm)
            
            # 最终归一化
            embedding = multimodal_features.cpu().numpy()[0]
            embedding = embedding / np.linalg.norm(embedding)
            
            logger.debug(f"Generated multimodal embedding for text: '{text_description[:50]}...'")
            return embedding.tolist()
            
        except Exception as e:
            logger.error(f"Failed to get multimodal embedding: {e}")
            return None
    
    def get_image_embedding(self, image: Image.Image) -> Optional[List[float]]:
        """
        获取纯图像的CLIP向量编码（保留作为备用方法）
        
        Args:
            image: PIL图像对象
            
        Returns:
            512维向量列表，失败返回None
        """
        try:
            # 确保图像为RGB格式
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # 预处理图像
            inputs = self.clip_processor(
                images=image, 
                return_tensors="pt", 
                padding=True
            ).to(self.device)
            
            # 获取图像特征
            with torch.no_grad():
                image_features = self.clip_model.get_image_features(**inputs)
            
            # 归一化向量（对余弦相似度很重要）
            embedding = image_features.cpu().numpy()[0]
            embedding = embedding / np.linalg.norm(embedding)
            
            return embedding.tolist()
            
        except Exception as e:
            logger.error(f"Failed to get image embedding: {e}")
            return None
    
    def parse_time_from_path(self, image_info: MushroomImageInfo) -> Dict[str, datetime]:
        """
        从图像路径信息中解析时间
        
        Args:
            image_info: 蘑菇图像信息对象
            
        Returns:
            包含各种时间信息的字典
        """
        time_info = {
            'collection_datetime': image_info.collection_datetime,
            'collection_date': datetime.strptime(image_info.collection_date, '%Y%m%d'),
            'detailed_time': datetime.strptime(image_info.detailed_time, '%Y%m%d%H%M%S'),
            'date_folder': datetime.strptime(image_info.date_folder, '%Y%m%d')
        }
        
        # 添加时间范围（用于查询环境参数）
        collection_time = time_info['collection_datetime']
        time_info['query_start'] = collection_time - timedelta(minutes=30)  # 前30分钟
        time_info['query_end'] = collection_time + timedelta(minutes=30)    # 后30分钟
        
        return time_info
    
    def get_environmental_data(self, mushroom_id: str, time_info: Dict[str, datetime]) -> Optional[Dict]:
        """
        根据蘑菇库号和时间信息获取环境参数
        
        Args:
            mushroom_id: 蘑菇库号
            time_info: 时间信息字典
            
        Returns:
            结构化的环境参数字典，失败返回None
        """
        if not self.env_processor:
            logger.warning("Environment data processor not initialized, skipping environment data retrieval")
            return None
        
        try:
            collection_time = time_info['collection_datetime']
            # 构建临时图像路径用于记录
            temp_image_path = f"{mushroom_id}/{collection_time.strftime('%Y%m%d')}/temp_image.jpg"
            
            # 映射库房号：MinIO中的库房号 -> 环境配置中的库房号
            mapped_room_id = self._map_room_id(mushroom_id)
            
            logger.debug(f"Querying environment data for room {mushroom_id} (mapped to {mapped_room_id}) at time {collection_time}")
            
            # 使用映射后的库房号查询环境数据
            env_data = self.env_processor.get_environment_data(
                room_id=mapped_room_id,
                collection_time=collection_time,
                image_path=temp_image_path,
                time_window_minutes=1  # 查询前后1分钟的数据
            )
            
            if env_data:
                logger.debug(f"获取环境数据成功: 库房{mushroom_id}")
                return env_data
            else:
                logger.debug(f"未找到环境数据: 库房{mushroom_id}")
                return None
                
        except Exception as e:
            logger.error(f"Failed to get environment data for room {mushroom_id}: {e}")
            return None
    
    def process_single_image(self, image_info: MushroomImageInfo, 
                           save_to_db: bool = True) -> Optional[Dict]:
        """
        处理单个图像：解析时间、获取环境参数、多模态编码
        只有在获取到完整数据（图像+环境数据）时才存储到数据库
        
        Args:
            image_info: 蘑菇图像信息
            save_to_db: 是否保存到数据库
            
        Returns:
            处理结果字典
        """
        try:
            # 1. 从MinIO获取图像
            image = self.minio_client.get_image(image_info.file_path)
            if image is None:
                logger.warning(f"[IMG-010] 获取图像失败 | 文件: {image_info.file_name}")
                return None
            
            # 2. 解析时间信息
            time_info = self.parse_time_from_path(image_info)
            
            # 3. 获取环境参数和语义描述
            env_data = self.get_environmental_data(image_info.mushroom_id, time_info)
            
            # 4. 检查是否获取到完整环境数据
            if env_data is None:
                logger.debug(f"无环境数据，使用纯图像编码: {image_info.file_name}")
                # 如果没有环境数据，使用纯图像编码
                embedding = self.get_image_embedding(image)
                if embedding is None:
                    logger.error(f"[IMG-011] 图像编码失败 | 文件: {image_info.file_name}")
                    return None
                
                return {
                    'image_info': image_info,
                    'embedding': embedding,
                    'time_info': time_info,
                    'environmental_data': None,
                    'processed_at': datetime.now(),
                    'saved_to_db': False,
                    'skip_reason': 'no_environment_data'
                }
            
            # 5. 使用LLaMA模型获取蘑菇生长情况描述和图像质量评分
            llama_result = self._get_llama_description(image)
            
            # 提取growth_stage_description和image_quality_score
            growth_stage_description = llama_result.get('growth_stage_description', '')
            llama_quality_score = llama_result.get('image_quality_score', None)
            
            # 6. 构建完整的文本描述：身份元数据 + LLaMA生长阶段描述
            identity_metadata = env_data.get('semantic_description', f"Mushroom Room {image_info.mushroom_id}, unknown stage, Day 0.")
            
            # 使用growth_stage_description作为CLIP文本编码的输入
            if growth_stage_description and growth_stage_description != "No visible structures":
                # 结合身份元数据和LLaMA生长阶段描述
                full_text_description = f"{identity_metadata} {growth_stage_description}"
                logger.debug(f"使用组合描述: 身份+LLaMA")
            else:
                # 如果LLaMA描述失败或为空，仅使用身份元数据
                full_text_description = identity_metadata
                if growth_stage_description:
                    logger.debug(f"LLaMA返回无可见结构，仅使用身份元数据")
                else:
                    logger.debug(f"LLaMA描述为空，仅使用身份元数据")
            
            # 7. 使用多模态编码（图像 + 完整文本描述）
            embedding = self.get_multimodal_embedding(image, full_text_description)
            
            if embedding is None:
                logger.error(f"[IMG-012] 多模态编码失败 | 文件: {image_info.file_name}")
                return None
            
            # 8. 将描述和质量评分保存到环境数据中
            env_data['full_text_description'] = full_text_description
            env_data['llama_description'] = growth_stage_description if growth_stage_description else "N/A"
            env_data['image_quality_score'] = llama_quality_score  # 使用LLaMA返回的质量评分
            
            # 9. 构建结果
            result = {
                'image_info': image_info,
                'embedding': embedding,
                'time_info': time_info,
                'environmental_data': env_data,
                'processed_at': datetime.now()
            }
            
            # 10. 只有在获取到完整数据时才保存到数据库
            if save_to_db:
                success = self._save_to_database(result)
                result['saved_to_db'] = success
                if not success:
                    logger.error(f"[IMG-013] 保存数据库失败 | 文件: {image_info.file_name}")
            else:
                result['saved_to_db'] = False
            
            return result
            
        except Exception as e:
            logger.error(f"[IMG-014] 处理异常 | 文件: {image_info.file_name}, 错误: {e}")
            return None
    
    def process_image_batch(self, images_data: List[Dict], save_to_db: bool = True) -> List[Dict]:
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
        
        logger.info(f"[IMG-BATCH] 开始批处理 | 图片数: {len(images_data)}")
        batch_results = []
        
        try:
            # 1. 批量准备数据
            batch_data = []
            for img_data in images_data:
                image = img_data['image']
                image_info = img_data['image_info']
                
                # 解析时间信息
                time_info = self.parse_time_from_path(image_info)
                
                # 获取环境参数
                env_data = self.get_environmental_data(image_info.mushroom_id, time_info)
                
                batch_data.append({
                    'image': image,
                    'image_info': image_info,
                    'time_info': time_info,
                    'env_data': env_data
                })
            
            # 2. 分离有环境数据和无环境数据的图片
            with_env_data = [item for item in batch_data if item['env_data'] is not None]
            without_env_data = [item for item in batch_data if item['env_data'] is None]
            
            logger.debug(f"[IMG-BATCH] 数据分类 | 有环境数据: {len(with_env_data)}, 无环境数据: {len(without_env_data)}")
            
            # 3. 批量处理有环境数据的图片
            if with_env_data:
                batch_results.extend(self._process_batch_with_env_data(with_env_data, save_to_db))
            
            # 4. 批量处理无环境数据的图片（纯图像编码）
            if without_env_data:
                batch_results.extend(self._process_batch_without_env_data(without_env_data, save_to_db))
            
            logger.info(f"[IMG-BATCH] 批处理完成 | 成功: {sum(1 for r in batch_results if r['success'])}, "
                       f"失败: {sum(1 for r in batch_results if not r['success'])}")
            
            return batch_results
            
        except Exception as e:
            logger.error(f"[IMG-BATCH] 批处理异常: {e}")
            # 回退到单张处理
            for img_data in images_data:
                try:
                    result = self.process_single_image(img_data['image_info'], save_to_db=save_to_db)
                    success = result is not None and (not save_to_db or result.get('saved_to_db', False))
                    batch_results.append({'success': success, 'image_info': img_data['image_info']})
                except Exception as e2:
                    logger.error(f"[IMG-BATCH] 回退处理失败: {img_data['image_info'].file_name}, 错误: {e2}")
                    batch_results.append({'success': False, 'image_info': img_data['image_info']})
            
            return batch_results
    
    def _process_batch_with_env_data(self, batch_data: List[Dict], save_to_db: bool) -> List[Dict]:
        """批量处理有环境数据的图片"""
        results = []
        
        try:
            # 1. 批量获取LLaMA描述
            images = [item['image'] for item in batch_data]
            llama_results = self._get_llama_descriptions_batch(images)
            
            # 2. 准备批量CLIP编码的数据
            clip_inputs = []
            for i, item in enumerate(batch_data):
                env_data = item['env_data']
                llama_result = llama_results[i] if i < len(llama_results) else {}
                
                # 构建文本描述
                identity_metadata = env_data.get('semantic_description', f"Mushroom Room {item['image_info'].mushroom_id}, unknown stage, Day 0.")
                growth_stage_description = llama_result.get('growth_stage_description', '')
                
                if growth_stage_description and growth_stage_description != "No visible structures":
                    full_text_description = f"{identity_metadata} {growth_stage_description}"
                else:
                    full_text_description = identity_metadata
                
                clip_inputs.append({
                    'image': item['image'],
                    'text': full_text_description,
                    'index': i
                })
            
            # 3. 批量CLIP编码
            embeddings = self._get_multimodal_embeddings_batch(clip_inputs)
            
            # 4. 构建结果并保存
            for i, item in enumerate(batch_data):
                try:
                    embedding = embeddings[i] if i < len(embeddings) else None
                    
                    if embedding is None:
                        logger.error(f"[IMG-BATCH] 编码失败: {item['image_info'].file_name}")
                        results.append({'success': False, 'image_info': item['image_info']})
                        continue
                    
                    # 构建完整结果
                    llama_result = llama_results[i] if i < len(llama_results) else {}
                    env_data = item['env_data'].copy()
                    
                    # 添加描述和质量评分
                    env_data['llama_description'] = llama_result.get('growth_stage_description', 'N/A')
                    env_data['image_quality_score'] = llama_result.get('image_quality_score')
                    
                    result = {
                        'image_info': item['image_info'],
                        'embedding': embedding,
                        'time_info': item['time_info'],
                        'environmental_data': env_data,
                        'processed_at': datetime.now()
                    }
                    
                    # 保存到数据库
                    if save_to_db:
                        success = self._save_to_database(result)
                        result['saved_to_db'] = success
                    else:
                        result['saved_to_db'] = False
                        success = True
                    
                    results.append({'success': success, 'image_info': item['image_info']})
                    
                except Exception as e:
                    logger.error(f"[IMG-BATCH] 处理单项失败: {item['image_info'].file_name}, 错误: {e}")
                    results.append({'success': False, 'image_info': item['image_info']})
            
        except Exception as e:
            logger.error(f"[IMG-BATCH] 批量处理有环境数据失败: {e}")
            # 回退到单张处理
            for item in batch_data:
                try:
                    result = self.process_single_image(item['image_info'], save_to_db=save_to_db)
                    success = result is not None and (not save_to_db or result.get('saved_to_db', False))
                    results.append({'success': success, 'image_info': item['image_info']})
                except Exception as e2:
                    logger.error(f"[IMG-BATCH] 回退处理失败: {item['image_info'].file_name}, 错误: {e2}")
                    results.append({'success': False, 'image_info': item['image_info']})
        
        return results
    
    def _process_batch_without_env_data(self, batch_data: List[Dict], save_to_db: bool) -> List[Dict]:
        """批量处理无环境数据的图片（纯图像编码）"""
        results = []
        
        try:
            # 批量图像编码
            images = [item['image'] for item in batch_data]
            embeddings = self._get_image_embeddings_batch(images)
            
            for i, item in enumerate(batch_data):
                embedding = embeddings[i] if i < len(embeddings) else None
                
                if embedding is None:
                    logger.error(f"[IMG-BATCH] 纯图像编码失败: {item['image_info'].file_name}")
                    results.append({'success': False, 'image_info': item['image_info']})
                    continue
                
                # 构建结果（无环境数据）
                result = {
                    'image_info': item['image_info'],
                    'embedding': embedding,
                    'time_info': item['time_info'],
                    'environmental_data': None,
                    'processed_at': datetime.now(),
                    'saved_to_db': False,
                    'skip_reason': 'no_environment_data'
                }
                
                results.append({'success': True, 'image_info': item['image_info']})
                
        except Exception as e:
            logger.error(f"[IMG-BATCH] 批量纯图像编码失败: {e}")
            # 回退到单张处理
            for item in batch_data:
                try:
                    embedding = self.get_image_embedding(item['image'])
                    success = embedding is not None
                    results.append({'success': success, 'image_info': item['image_info']})
                except Exception as e2:
                    logger.error(f"[IMG-BATCH] 回退纯图像编码失败: {item['image_info'].file_name}, 错误: {e2}")
                    results.append({'success': False, 'image_info': item['image_info']})
        
        return results
    
    def _get_multimodal_embeddings_batch(self, clip_inputs: List[Dict]) -> List[Optional[List[float]]]:
        """批量获取多模态CLIP编码"""
        try:
            if not clip_inputs:
                return []
            
            # 准备批量输入
            images = [item['image'] for item in clip_inputs]
            texts = [item['text'] for item in clip_inputs]
            
            # 确保所有图像为RGB格式
            processed_images = []
            for image in images:
                if image.mode != 'RGB':
                    image = image.convert('RGB')
                processed_images.append(image)
            
            # 批量预处理
            inputs = self.clip_processor(
                text=texts,
                images=processed_images, 
                return_tensors="pt", 
                padding=True,
                truncation=True
            ).to(self.device)
            
            # 批量获取特征
            with torch.no_grad():
                image_features = self.clip_model.get_image_features(pixel_values=inputs['pixel_values'])
                text_features = self.clip_model.get_text_features(
                    input_ids=inputs['input_ids'],
                    attention_mask=inputs['attention_mask']
                )
            
            # 批量融合特征
            image_weight = 0.7
            text_weight = 0.3
            
            image_features_norm = image_features / image_features.norm(dim=-1, keepdim=True)
            text_features_norm = text_features / text_features.norm(dim=-1, keepdim=True)
            
            multimodal_features = (image_weight * image_features_norm + 
                                 text_weight * text_features_norm)
            
            # 最终归一化并转换为列表
            embeddings = []
            for i in range(multimodal_features.shape[0]):
                embedding = multimodal_features[i].cpu().numpy()
                embedding = embedding / np.linalg.norm(embedding)
                embeddings.append(embedding.tolist())
            
            logger.debug(f"[IMG-BATCH] 批量多模态编码完成: {len(embeddings)}个")
            return embeddings
            
        except Exception as e:
            logger.error(f"[IMG-BATCH] 批量多模态编码失败: {e}")
            return [None] * len(clip_inputs)
    
    def _get_image_embeddings_batch(self, images: List[Image.Image]) -> List[Optional[List[float]]]:
        """批量获取纯图像CLIP编码"""
        try:
            if not images:
                return []
            
            # 确保所有图像为RGB格式
            processed_images = []
            for image in images:
                if image.mode != 'RGB':
                    image = image.convert('RGB')
                processed_images.append(image)
            
            # 批量预处理
            inputs = self.clip_processor(
                images=processed_images, 
                return_tensors="pt"
            ).to(self.device)
            
            # 批量获取图像特征
            with torch.no_grad():
                image_features = self.clip_model.get_image_features(**inputs)
            
            # 归一化并转换为列表
            embeddings = []
            for i in range(image_features.shape[0]):
                embedding = image_features[i].cpu().numpy()
                embedding = embedding / np.linalg.norm(embedding)
                embeddings.append(embedding.tolist())
            
            logger.debug(f"[IMG-BATCH] 批量图像编码完成: {len(embeddings)}个")
            return embeddings
            
        except Exception as e:
            logger.error(f"[IMG-BATCH] 批量图像编码失败: {e}")
            return [None] * len(images)
    
    def _get_llama_descriptions_batch(self, images: List[Image.Image]) -> List[Dict]:
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
                    logger.warning(f"[IMG-BATCH] LLaMA描述失败: {e}")
                    results.append({"growth_stage_description": "", "image_quality_score": None})
            
            logger.debug(f"[IMG-BATCH] 批量LLaMA描述完成: {len(results)}个")
            return results
            
        except Exception as e:
            logger.error(f"[IMG-BATCH] 批量LLaMA描述失败: {e}")
            return [{"growth_stage_description": "", "image_quality_score": None}] * len(images)
    
    def _save_to_database(self, result: Dict) -> bool:
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
            image_info = result['image_info']
            env_data = result['environmental_data']
            
            # 确保有环境数据才保存
            if not env_data:
                logger.debug(f"无环境数据，跳过保存: {image_info.file_name}")
                return False
            
            # 检查是否已存在
            existing = session.query(MushroomImageEmbedding).filter_by(
                image_path=image_info.file_path
            ).first()
            
            if existing:
                # 更新现有记录
                existing.embedding = result['embedding']
                existing.collection_datetime = result['time_info']['collection_datetime']
                
                # 更新环境数据字段
                existing.room_id = env_data.get('room_id', image_info.mushroom_id)
                existing.in_date = env_data.get('in_date', result['time_info']['collection_datetime'].date())
                existing.in_num = env_data.get('in_num', 0)
                existing.growth_day = env_data.get('growth_day', 0)
                existing.air_cooler_config = env_data.get('air_cooler_config', '{}')
                existing.fresh_fan_config = env_data.get('fresh_fan_config', '{}')
                existing.light_count = env_data.get('light_count', 0)
                existing.light_config = env_data.get('light_config', '{}')
                existing.humidifier_count = env_data.get('humidifier_count', 0)
                existing.humidifier_config = env_data.get('humidifier_config', '{}')
                existing.env_sensor_status = env_data.get('env_sensor_status', '{}')
                existing.semantic_description = env_data.get('semantic_description', '无环境数据。')
                existing.llama_description = env_data.get('llama_description', 'N/A')
                existing.image_quality_score = env_data.get('image_quality_score', None)
                existing.updated_at = datetime.now()
                
                logger.debug(f"更新数据库记录: {image_info.file_name}")
            else:
                # 创建新记录
                new_record = MushroomImageEmbedding(
                    image_path=image_info.file_path,
                    collection_datetime=result['time_info']['collection_datetime'],
                    embedding=result['embedding'],
                    room_id=env_data.get('room_id', image_info.mushroom_id),
                    in_date=env_data.get('in_date', result['time_info']['collection_datetime'].date()),
                    in_num=env_data.get('in_num', 0),
                    growth_day=env_data.get('growth_day', 0),
                    air_cooler_config=env_data.get('air_cooler_config', '{}'),
                    fresh_fan_config=env_data.get('fresh_fan_config', '{}'),
                    light_count=env_data.get('light_count', 0),
                    light_config=env_data.get('light_config', '{}'),
                    humidifier_count=env_data.get('humidifier_count', 0),
                    humidifier_config=env_data.get('humidifier_config', '{}'),
                    env_sensor_status=env_data.get('env_sensor_status', '{}'),
                    semantic_description=env_data.get('semantic_description', '无环境数据。'),
                    llama_description=env_data.get('llama_description', 'N/A'),
                    image_quality_score=env_data.get('image_quality_score', None)
                )
                
                session.add(new_record)
                logger.debug(f"创建数据库记录: {image_info.file_name}")
            
            session.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to save to database: {e}")
            session.rollback()
            return False
        finally:
            session.close()
    
    def batch_process_images(self, mushroom_id: Optional[str] = None, 
                           date_filter: Optional[str] = None,
                           batch_size: int = 10) -> Dict[str, int]:
        """
        批量处理图像
        
        Args:
            mushroom_id: 蘑菇库号过滤
            date_filter: 日期过滤 (YYYYMMDD)
            batch_size: 批处理大小
            
        Returns:
            处理统计结果
        """
        logger.info("🚀 开始批量处理图像")
        
        # 获取所有蘑菇图像
        all_images = self.processor.get_mushroom_images(
            mushroom_id=mushroom_id,
            date_filter=date_filter
        )
        
        if not all_images:
            logger.warning("⚠️ 未找到符合条件的图像")
            return {'total': 0, 'success': 0, 'failed': 0, 'skipped': 0}
        
        logger.info(f"📊 找到 {len(all_images)} 张图像待处理")
        
        stats = {'total': len(all_images), 'success': 0, 'failed': 0, 'skipped': 0}
        
        # 分批处理
        for i in range(0, len(all_images), batch_size):
            batch = all_images[i:i + batch_size]
            logger.info(f"🔄 处理批次 {i//batch_size + 1}/{(len(all_images)-1)//batch_size + 1}")
            
            for image_info in batch:
                try:
                    # 检查是否已处理过
                    if self._is_already_processed(image_info.file_path):
                        logger.info(f"⏭️ 跳过已处理图像: {image_info.file_name}")
                        stats['skipped'] += 1
                        continue
                    
                    # 处理图像
                    result = self.process_single_image(image_info, save_to_db=True)
                    
                    if result and result.get('saved_to_db', False):
                        stats['success'] += 1
                    else:
                        stats['failed'] += 1
                        
                except Exception as e:
                    logger.error(f"❌ 批处理中处理图像失败 {image_info.file_name}: {e}")
                    stats['failed'] += 1
        
        logger.info(f"✅ 批量处理完成 - 总计: {stats['total']}, "
                   f"成功: {stats['success']}, 失败: {stats['failed']}, 跳过: {stats['skipped']}")
        
        return stats
    
    def _is_already_processed(self, image_path: str) -> bool:
        """检查图像是否已经处理过"""
        session = self.Session()
        try:
            existing = session.query(MushroomImageEmbedding).filter_by(
                image_path=image_path
            ).first()
            return existing is not None
        except Exception as e:
            logger.error(f"❌ 检查处理状态失败: {e}")
            return False
        finally:
            session.close()
    
    def get_processing_statistics(self) -> Dict:
        """获取处理统计信息"""
        session = self.Session()
        try:
            from sqlalchemy import func
            
            # 总处理数量
            total_count = session.query(MushroomImageEmbedding).count()
            
            # 按库房分组统计
            room_stats = session.query(
                MushroomImageEmbedding.room_id,
                func.count(MushroomImageEmbedding.id).label('count')
            ).group_by(MushroomImageEmbedding.room_id).all()
            
            # 按生长阶段分组统计
            stage_stats = session.query(
                MushroomImageEmbedding.growth_stage,
                func.count(MushroomImageEmbedding.id).label('count')
            ).group_by(MushroomImageEmbedding.growth_stage).all()
            
            # 按日期分组统计
            date_stats = session.query(
                MushroomImageEmbedding.in_date,
                func.count(MushroomImageEmbedding.id).label('count')
            ).group_by(MushroomImageEmbedding.in_date).all()
            
            # 有环境控制策略的记录数
            with_env_control = session.query(MushroomImageEmbedding).filter(
                MushroomImageEmbedding.semantic_description != '无环境数据。'
            ).count()
            
            # 补光灯使用统计
            light_usage = session.query(
                MushroomImageEmbedding.light_count,
                func.count(MushroomImageEmbedding.id).label('count')
            ).group_by(MushroomImageEmbedding.light_count).all()
            
            return {
                'total_processed': total_count,
                'with_environmental_control': with_env_control,
                'room_distribution': {str(room_id): count for room_id, count in room_stats},
                'growth_stage_distribution': {stage: count for stage, count in stage_stats},
                'date_distribution': {str(date): count for date, count in date_stats},
                'light_usage_distribution': {f'light_{count}': usage for count, usage in light_usage},
                'processing_time': datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ 获取统计信息失败: {e}")
            return {}
        finally:
            session.close()

    def validate_system_with_limited_samples(self, max_per_mushroom: int = 3) -> Dict[str, Any]:
        """
        验证系统功能，每个蘑菇库房最多处理指定数量的图像
        只有在获取到完整数据时才存储到数据库
        
        Args:
            max_per_mushroom: 每个蘑菇库房最多处理的图像数量
            
        Returns:
            验证结果统计
        """
        logger.info(f"Starting system validation with max {max_per_mushroom} images per room")
        
        # 获取所有图像并按库房分组
        all_images = self.processor.get_mushroom_images()
        mushroom_groups = {}
        
        for img in all_images:
            if img.mushroom_id not in mushroom_groups:
                mushroom_groups[img.mushroom_id] = []
            mushroom_groups[img.mushroom_id].append(img)
        
        logger.info(f"Found {len(mushroom_groups)} rooms: {sorted(mushroom_groups.keys())}")
        
        validation_results = {
            'mushroom_ids': sorted(mushroom_groups.keys()),
            'total_mushrooms': len(mushroom_groups),
            'processed_per_mushroom': {},
            'total_processed': 0,
            'total_success': 0,
            'total_failed': 0,
            'total_skipped': 0,
            'total_no_env_data': 0
        }
        
        # 对每个库房处理有限数量的图像
        for mushroom_id in sorted(mushroom_groups.keys()):
            logger.info(f"Validating room {mushroom_id}...")
            
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
                        logger.info(f"Skipping already processed image: {img.file_name}")
                        continue
                    
                    # 处理图像
                    logger.info(f"Processing image: {img.file_name}")
                    result = self.process_single_image(img, save_to_db=True)
                    
                    if result:
                        if result.get('saved_to_db', False):
                            success_count += 1
                            logger.info(f"Successfully processed and saved: {img.file_name}")
                        elif result.get('skip_reason') == 'no_environment_data':
                            no_env_data_count += 1
                            logger.warning(f"Processed but no environment data: {img.file_name}")
                        else:
                            failed_count += 1
                            logger.error(f"Processing failed: {img.file_name}")
                    else:
                        failed_count += 1
                        logger.error(f"Processing returned None: {img.file_name}")
                    
                    processed_count += 1
                    
                except Exception as e:
                    failed_count += 1
                    logger.error(f"Exception processing {img.file_name}: {e}")
                    processed_count += 1
            
            # 记录该库房的结果
            validation_results['processed_per_mushroom'][mushroom_id] = {
                'processed': processed_count,
                'success': success_count,
                'failed': failed_count,
                'skipped': skipped_count,
                'no_env_data': no_env_data_count,
                'total_images': len(images)
            }
            
            validation_results['total_processed'] += processed_count
            validation_results['total_success'] += success_count
            validation_results['total_failed'] += failed_count
            validation_results['total_skipped'] += skipped_count
            validation_results['total_no_env_data'] += no_env_data_count
            
            logger.info(f"Room {mushroom_id} results: processed={processed_count}, success={success_count}, "
                       f"failed={failed_count}, skipped={skipped_count}, no_env_data={no_env_data_count}")
        
        logger.info(f"System validation completed - total_processed: {validation_results['total_processed']}, "
                   f"success: {validation_results['total_success']}, failed: {validation_results['total_failed']}, "
                   f"skipped: {validation_results['total_skipped']}, no_env_data: {validation_results['total_no_env_data']}")
        
        return validation_results


def create_mushroom_encoder() -> MushroomImageEncoder:
    """创建蘑菇图像编码器实例"""
    return MushroomImageEncoder()


if __name__ == "__main__":


    try:
        # Initialize encoder
        encoder = create_mushroom_encoder()
        print('✅ Encoder initialized successfully')

        # Test system validation with limited samples
        print('🔍 Running system validation with limited samples...')
        validation_results = encoder.validate_system_with_limited_samples(max_per_mushroom=2)

        print('📊 Validation Results:')
        print(f'   Total mushrooms: {validation_results["total_mushrooms"]}')
        print(f'   Mushroom IDs: {validation_results["mushroom_ids"]}')
        print(f'   Total processed: {validation_results["total_processed"]}')
        print(f'   Total success: {validation_results["total_success"]}')
        print(f'   Total failed: {validation_results["total_failed"]}')
        print(f'   Total skipped: {validation_results["total_skipped"]}')
        print(f'   No env data: {validation_results["total_no_env_data"]}')

        print('\n📈 Per-mushroom breakdown:')
        for mushroom_id, stats in validation_results['processed_per_mushroom'].items():
            print(f'   Room {mushroom_id}: processed={stats["processed"]}, success={stats["success"]}, failed={stats["failed"]}, no_env_data={stats["no_env_data"]}')

        # Get processing statistics
        print('\n📋 Getting processing statistics...')
        processing_stats = encoder.get_processing_statistics()
        print(f'   Total records in database: {processing_stats.get("total_processed", 0)}')
        print(f'   Records with environmental control: {processing_stats.get("with_environmental_control", 0)}')

        print('\n✅ Multimodal CLIP encoding system test completed successfully!')

    except Exception as e:
        print(f'❌ Test failed: {e}')
        import traceback
        import sys
        traceback.print_exc()
        sys.exit(1)

"""
@Project ：get_data
@File    ：get_data.py
@IDE     ：PyCharm
@Author  ：niucg1@lenovo.com
@Date    ：2024/10/15 16:21
@Desc     :
"""

import json
import time
from typing import Optional

import pandas as pd
import requests
from loguru import logger

from global_const.global_const import settings
from utils.send_request import SendRequest


class GetData(SendRequest):
    def __init__(self, urls, host, port, **kwargs):
        super().__init__()
        self.host = host
        self.port = port
        self.urls = urls
        self._cached_prompt = None
        self._cached_prompt_expires_at = 0.0
        self._cached_prompt_source = None
        self._cached_prompt_version = None

    def _get_prompt_cache_ttl_seconds(self) -> int:
        ttl = getattr(settings, "prompt_cache_ttl_seconds", None)
        try:
            ttl_int = int(ttl)
            if ttl_int > 0:
                return ttl_int
        except Exception:
            pass
        return 3600

    def _get_mlflow_tracking_uri(self) -> Optional[str]:
        try:
            mlflow_cfg = getattr(settings, "mlflow", None)
            host = getattr(mlflow_cfg, "host", None)
            port = getattr(mlflow_cfg, "port", None)
            if host and port:
                return f"http://{host}:{port}"
        except Exception:
            return None
        return None

    def _load_prompt_from_mlflow(self, prompt_uri: str):
        try:
            import mlflow
        except Exception as e:
            logger.warning(f"[Prompt MLflow] MLflow不可用: {e}")
            return None

        tracking_uri = self._get_mlflow_tracking_uri()
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)

        try:
            prompt_obj = mlflow.genai.load_prompt(prompt_uri)
        except Exception as e:
            logger.warning(f"[Prompt MLflow] 加载失败: {prompt_uri} | {e}")
            return None

        expected_version = None
        parts = str(prompt_uri).replace("prompts:/", "").strip("/").split("/")
        if len(parts) >= 2 and parts[1].isdigit():
            expected_version = int(parts[1])

        try:
            actual_version = int(getattr(prompt_obj, "version", -1))
        except Exception:
            actual_version = None

        if (
            expected_version is not None
            and actual_version is not None
            and expected_version != actual_version
        ):
            logger.error(
                f"[Prompt MLflow] 版本不匹配: uri={prompt_uri} | expected={expected_version} actual={actual_version}"
            )
            return None

        try:
            variables = getattr(prompt_obj, "variables", set()) or set()
            if "image_input" in variables:
                prompt_value = prompt_obj.format(image_input="[image attached]")
            else:
                prompt_value = prompt_obj.template
        except Exception as e:
            logger.warning(f"[Prompt MLflow] 格式化失败: {prompt_uri} | {e}")
            prompt_value = getattr(prompt_obj, "template", None)

        if prompt_value is None:
            return None

        self._cached_prompt = prompt_value
        self._cached_prompt_source = prompt_uri
        self._cached_prompt_version = actual_version
        self._cached_prompt_expires_at = time.time() + self._get_prompt_cache_ttl_seconds()

        logger.info(
            f"[Prompt MLflow] 已加载并缓存提示词: {prompt_uri} | version={actual_version} | ttl={self._get_prompt_cache_ttl_seconds()}s"
        )
        return prompt_value

    def get_cached_prompt_meta(self) -> dict:
        return {
            "source": self._cached_prompt_source,
            "version": self._cached_prompt_version,
            "expires_at": self._cached_prompt_expires_at,
        }

    def get_mushroom_prompt(self):
        """
        从API动态获取蘑菇描述提示词
        
        Returns:
            str | list[dict]: 获取到的提示词内容，如果失败则返回None
        """
        if self._cached_prompt and time.time() < float(self._cached_prompt_expires_at or 0.0):
            return self._cached_prompt
            
        try:
            # 从配置文件读取API相关配置
            # Dynaconf会将配置键转换为大小写两种形式，尝试两种方式访问
            prompt_url = None
            if hasattr(self.urls, 'prompt_mushroom_description'):
                prompt_url = self.urls.prompt_mushroom_description
            elif hasattr(self.urls, 'PROMPT_MUSHROOM_DESCRIPTION'):
                prompt_url = self.urls.PROMPT_MUSHROOM_DESCRIPTION
            
            if not prompt_url:
                logger.warning("[Prompt API] 配置文件中未找到prompt_mushroom_description，使用默认提示词")
                fallback_prompt = getattr(settings.llama, 'mushroom_descripe_prompt', None)
                return fallback_prompt
            
            # 格式化URL
            prompt_url = prompt_url.format(host=self.host)

            if str(prompt_url).startswith("prompts:/"):
                prompt_value = self._load_prompt_from_mlflow(str(prompt_url))
                if prompt_value is not None:
                    return prompt_value
                logger.warning("[Prompt MLflow] 获取失败，使用默认提示词")
                fallback_prompt = getattr(settings.llama, "mushroom_descripe_prompt", None)
                return fallback_prompt
            
            # 检查是否有prompt配置
            if not hasattr(settings, 'prompt'):
                logger.warning("[Prompt API] 配置文件中未找到prompt配置，使用默认提示词")
                fallback_prompt = getattr(settings.llama, 'mushroom_descripe_prompt', None)
                return fallback_prompt
                
            backend_token = settings.prompt.backend_token
            
            headers = {
                "Authorization": backend_token,
                "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
                "Accept": "*/*",
                "Connection": "keep-alive"
            }
            
            logger.info(f"[Prompt API] 正在从API获取提示词: {prompt_url}")
            
            # 发送GET请求
            response = requests.get(
                prompt_url,
                headers=headers,
                timeout=10  # 设置10秒超时
            )
            
            # 检查响应状态
            if response.status_code == 200:
                data = response.json()
                
                # 根据API响应结构提取提示词内容
                if isinstance(data, dict):
                    # 先记录完整响应以便调试
                    logger.debug(f"[Prompt API] API响应结构: {list(data.keys())}")
                    
                    prompt_content = None
                    
                    # API返回格式: {"success": true, "data": {"content": {"template": "..."}}}
                    if 'data' in data and isinstance(data['data'], dict):
                        data_obj = data['data']
                        if 'content' in data_obj and isinstance(data_obj['content'], dict):
                            content_obj = data_obj['content']
                            if 'template' in content_obj and isinstance(content_obj['template'], str):
                                prompt_content = content_obj['template']
                                logger.debug("[Prompt API] 从data.content.template获取提示词")
                    
                    # 如果上面的路径没找到，尝试其他可能的字段
                    if not prompt_content:
                        for field in ['content', 'prompt', 'instruction', 'text', 'roleInstruction']:
                            if field in data and isinstance(data[field], str):
                                prompt_content = data[field]
                                logger.debug(f"[Prompt API] 从字段 '{field}' 获取提示词")
                                break
                        
                        # 尝试data嵌套的其他字段
                        if not prompt_content and 'data' in data and isinstance(data['data'], dict):
                            data_obj = data['data']
                            for field in ['content', 'prompt', 'instruction', 'text', 'roleInstruction', 'template']:
                                if field in data_obj and isinstance(data_obj[field], str):
                                    prompt_content = data_obj[field]
                                    logger.debug(f"[Prompt API] 从data.{field}获取提示词")
                                    break
                    
                    if prompt_content:
                        self._cached_prompt = prompt_content
                        self._cached_prompt_source = prompt_url
                        self._cached_prompt_expires_at = time.time() + self._get_prompt_cache_ttl_seconds()
                        logger.info(f"[Prompt API] 成功获取提示词，长度: {len(prompt_content)} 字符")
                        return prompt_content
                    else:
                        logger.warning(f"[Prompt API] API响应中未找到提示词内容")
                        logger.debug(f"[Prompt API] 响应顶层键: {list(data.keys())}")
                        if 'data' in data:
                            logger.debug(f"[Prompt API] data键: {list(data['data'].keys()) if isinstance(data['data'], dict) else type(data['data'])}")
                else:
                    logger.warning(f"[Prompt API] API响应格式异常: {type(data)}")
            else:
                logger.error(f"[Prompt API] API请求失败，状态码: {response.status_code}, 响应: {response.text}")
                
        except requests.exceptions.Timeout:
            logger.error("[Prompt API] API请求超时")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"[Prompt API] API连接失败: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"[Prompt API] API请求异常: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"[Prompt API] API响应JSON解析失败: {e}")
        except Exception as e:
            logger.error(f"[Prompt API] 获取提示词时发生未知错误: {e}")
        
        # 如果API获取失败，返回配置文件中的默认提示词作为后备
        logger.warning("[Prompt API] API获取失败，使用配置文件中的默认提示词")
        fallback_prompt = getattr(settings.llama, 'mushroom_descripe_prompt', None)
        if fallback_prompt:
            logger.info("[Prompt API] 已加载配置文件中的后备提示词")
        return fallback_prompt

    def get_history_data(
        self,
        datapoint_list,
        query_point_code,
        start_time,
        end_time,
    ):
        """
        批量获取历史数据
        :param datapoint_list:
        :param datapointTypeId:
        :param start_time:DateTime
        :param end_time:
        :return:
        """

        url = self.urls.history_data1.format(host=self.host)
        # 直接构建metrics列表
        metrics = [
            {"metaCode": f"{metacode}::{query_point_code}"}
            for metacode in datapoint_list
        ]
        payload = json.dumps(
            {
                "metrics": metrics,
                "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        headers = {
            "Content-Type": "application/json",
        }
        try:
            response = super().send_post_request(url, headers=headers, payload=payload)

        except Exception as e:
            logger.warning(f"[0.0.2] 获取测点历史数据(批量)失败！ 异常信息：{e}")
            return None
        if response:
            response = response.json()
            df = pd.json_normalize(response["data"])
            df = df.apply(
                lambda x: (
                    pd.DataFrame(
                        {
                            "time": x["abscissa"],
                            "value": x["ordinate"],
                            "chartCode": x["chartCode"],
                        }
                    )
                    if len(x["abscissa"]) > 0
                    else pd.DataFrame()
                ),
                axis=1,
            )
            df = pd.concat(df.to_list())
            if not df.empty:
                df[["chartCode", "point"]] = df["chartCode"].str.split(
                    "::", expand=True
                )
                df["value"] = pd.to_numeric(df["value"])
            return df
        else:
            return pd.DataFrame()

    def _resample_data(self, df, downsample, agg):
        """
        历史数据查询过程中，对数值列做聚合，其他列取第一个值
        :return:
        """
        # 动态生成聚合字典
        agg_dict = {col: "first" for col in df.columns if col != "value"}
        agg_dict["value"] = agg
        return df.resample("{}min".format(downsample)).agg(agg_dict)

    def get_device_history_cal(self, row, **kwargs):
        """
        获取设备的历史数据(边缘侧td)
        :param query_df:包含 查询设备名、测点名、开始时间和结束时间的dataframe
        """
        headers = {
            # "Authorization": "Basic cm9vdDp0YW9zZGF0YQ==",
            # "tz": "Asia/Shanghai",
            "Content-Type": "application/json",
        }

        payload = json.dumps(
            {
                "deviceCode": row["device_name"],
                "pointCode": row["point_name"],
                "start": row["start_time"],
                "end": row["end_time"],
            }
        )
        self.history_cal = self.urls.history_cal.format(host=self.host)
        response = super().send_post_request(self.history_cal, headers, payload)
        if response is None:
            return pd.DataFrame()
        else:
            response = response.json()
            if (
                response.get("data") is None
                or len(response.get("data").get("abscissa")) == 0
            ):
                return pd.DataFrame()
            if kwargs.get("query_batch"):
                tmp = pd.DataFrame(
                    {
                        "time": response.get("data").get("abscissa"),
                        "value": response.get("data").get("ordinate"),
                        "device_name": row["device_alias"],
                        "point_name": row["point_alias"],
                    }
                )
            else:
                tmp = pd.DataFrame(
                    {
                        "time": response.get("data").get("abscissa"),
                        "value": response.get("data").get("ordinate"),
                        "device_name": kwargs.get("device_alias"),
                        "point_name": row["point_alias"],
                    }
                )
            tmp["time"] = pd.to_datetime(tmp["time"]).dt.floor(freq="1min")
            tmp["value"] = pd.to_numeric(tmp["value"])
            return tmp

    def get_realtime_data(self, query_batch_df):
        """
        获取所有车间的code
        :param datapoint_list:
        :return:
        """

        url = self.urls.real_time_batch.format(host=self.host)
        if query_batch_df is None or query_batch_df.empty:
            return pd.DataFrame()
        # 使用 apply 函数将每一行转换为所需的字符串格式
        formatted_list = query_batch_df.apply(
            lambda row: f"{row['device_name']}::{row['point_name']}", axis=1
        ).tolist()
        payload = json.dumps(formatted_list)
        headers = {
            "Content-Type": "application/json",
        }
        try:
            response = super().send_post_request(url, headers=headers, payload=payload)
        except Exception as e:
            logger.warning(f"[0.0.3] 获取实时数据失败！ 异常信息：{e}")
            return pd.DataFrame()
        if response:
            return pd.json_normalize(response.json(), record_path="data")
        else:
            return pd.DataFrame()

    def send_cmd_post_request(self, row):
        header = {"Content-Type": "application/json"}
        payload = json.dumps(
            {
                "deviceCode": row["device_name"],
                "pointCode": row["point_name"],
                "value": float(row["cmd"]),
            }
        )
        return super().send_post_request(
            self.urls.write_cmd.format(self.host), header, payload
        )

    def write_instruction(self, res):
        try:
            res.apply(self.send_cmd_post_request, axis=1)
        except Exception as e:
            logger.error("[0.3.0] 指令下发接口调用失败！异常信息：{}".format(e))

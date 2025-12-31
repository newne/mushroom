"""
@Project ：get_data
@File    ：get_data.py
@IDE     ：PyCharm
@Author  ：niucg1@lenovo.com
@Date    ：2024/10/15 16:21
@Desc     :
"""

import json

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

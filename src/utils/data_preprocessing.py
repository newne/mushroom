from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

from global_const.global_const import create_get_data


def query_realtime_data(query_df, **kwargs):
    """
    边缘侧查询给定条件的实时数据
    :param query_df:
    :return:
    """
    get_data = create_get_data()
    real_time_df = get_data.get_realtime_data(query_df)
    if real_time_df is None or real_time_df.empty:
        logger.info(
            f"[0.0.0] 实时数据为空！退出计算。查询条件： \n {query_df.to_markdown()}"
        )
        return
    real_time_df[["device_name", "point_name"]] = real_time_df["p"].str.split(
        "::", expand=True
    )
    real_time_df = pd.merge(query_df, real_time_df, on=["device_name", "point_name"])

    real_time_df["v"] = real_time_df["v"].astype(float)
    if kwargs.get("pivot", None):
        real_time_df = real_time_df.pivot_table(
            index="device_alias", columns="point_alias", values="v"
        )

    return real_time_df


def query_data_by_batch_time(query_df, start_date, end_date, days=5):
    """
    边缘侧查询给定条件时间的所有历史数据
    :param query_df:
    :param start_date:
    :param end_date:
    :return:
    """
    query_slice_df = pd.DataFrame()
    # end_date = min(end_date, datetime.now())
    time_interval = days if (end_date - start_date).days / days > 1 else 1
    while start_date < end_date:
        end_date_ = start_date + timedelta(days=time_interval)
        end_date_ = min(end_date_, end_date)
        query_df.loc[:, "end_time"], query_df.loc[:, "start_time"] = (
            end_date_.strftime("%Y-%m-%d %H:%M:%S"),
            start_date.strftime("%Y-%m-%d %H:%M:%S"),
        )
        query_slice_df = pd.concat([query_slice_df, query_df], axis=0).reset_index(
            drop=True
        )
        start_date = end_date_

    # 修复：检查query_df是否有name属性，如果没有则使用device_alias列的第一个值
    device_alias = getattr(query_df, "name", None)
    if device_alias is None and not query_df.empty:
        device_alias = query_df["device_alias"].iloc[0]

    res = pd.concat(
        query_slice_df.apply(
            create_get_data().get_device_history_cal,
            device_alias=device_alias,
            query_batch=True,
            axis=1,
        ).tolist(),
        axis=0,
    )
    
    # 检查结果是否为空或缺少必要的列
    if res.empty or 'value' not in res.columns:
        # 返回空的DataFrame，但包含必要的列
        return pd.DataFrame(columns=['time', 'value', 'device_name', 'point_name'])
    
    res["value"] = pd.to_numeric(res["value"], errors='coerce')  # 添加错误处理
    res["time"] = pd.to_datetime(res["time"], errors='coerce')   # 添加错误处理
    return res


def query_history_by_query_time(query_df):
    """
    根据查询时间范围获取历史数据。
    :param query_df: 包含查询参数的 DataFrame
    :return: 合并的历史数据 DataFrame 或空 DataFrame（如果发生错误）
    """
    try:
        # 检查输入 DataFrame 是否为空
        if query_df is None or query_df.empty:
            logger.warning("输入的查询 DataFrame 为空")
            return pd.DataFrame()

        # 添加结束时间列
        query_df["end_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 添加开始时间列
        try:
            query_df["start_time"] = query_df.apply(
                lambda row: (
                    datetime.now() - timedelta(minutes=row["query_time"])
                ).strftime("%Y-%m-%d %H:%M:%S"),
                axis=1,
            )

        except KeyError as e:
            logger.error(f"查询 DataFrame 缺少必要列: {e}")
            return pd.DataFrame()

        # 获取历史数据
        # 修复：检查query_df是否有name属性，如果没有则使用device_alias列的第一个值
        device_alias = getattr(query_df, "name", None)
        if device_alias is None and not query_df.empty:
            device_alias = query_df["device_alias"].iloc[0]

        try:
            get_data = create_get_data()
            results = query_df.apply(
                get_data.get_device_history_cal, device_alias=device_alias, axis=1
            ).tolist()
            res = pd.concat(results, axis=0) if results else pd.DataFrame()
        except Exception as e:
            logger.error(f"调用 get_data.get_device_history_cal 时发生错误: {e}")
            raise ValueError(f"调用 get_data.get_device_history_cal 时发生错误:{e}")
        return res

    except Exception as e:
        logger.error(f"查询历史数据时发生错误: {e}")
        raise ValueError(f"查询历史数据时发生错误:{e}")



if __name__ == '__main__':
    # 按设备类型获取配置
    from utils.dataframe_utils import get_static_config_by_device_type

    # 获取新风机配置
    fresh_air_fan_df = get_static_config_by_device_type('fresh_air_fan')
    start_time = datetime(2025, 12, 19, 18, 1)
    end_time = datetime(2025, 12, 19, 18, 3)
    fresh_air_fan_df = fresh_air_fan_df.groupby("device_alias").apply(query_data_by_batch_time, start_time, end_time,
                                                                      include_groups=True).reset_index().sort_values(
        "time")
    fresh_air_fan_df
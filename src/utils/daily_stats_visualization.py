"""
基于 MushroomEnvDailyStats 表的天级别环境数据可视化模块

参考 src/utils/visualization.py 的设计模式，从数据库中读取最新批次的温湿度数据
并生成 Violin 图展示每日环境数据的分布特征，包含中位数趋势线和生长阶段标注。
"""

import warnings
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any, Tuple

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from sqlalchemy import text, desc
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
from utils.create_table import MushroomEnvDailyStats
from utils.loguru_setting import logger


def get_latest_batch_data(
    rooms: Optional[List[str]] = None,
    days_back: int = 30,
    min_records_per_room: int = 5
) -> pd.DataFrame:
    """
    从 MushroomEnvDailyStats 表中查询最新批次的数据
    
    Args:
        rooms: 库房列表，如 ['611', '612']，None 表示查询所有库房
        days_back: 向前查询的天数，默认30天
        min_records_per_room: 每个库房最少记录数，用于过滤数据不足的库房
        
    Returns:
        pd.DataFrame: 包含最新批次数据的 DataFrame
    """
    Session = sessionmaker(bind=pgsql_engine)
    session = Session()
    
    try:
        # 构建基础查询
        query = session.query(MushroomEnvDailyStats)
        
        # 时间范围过滤
        cutoff_date = date.today() - timedelta(days=days_back)
        query = query.filter(MushroomEnvDailyStats.stat_date >= cutoff_date)
        
        # 库房过滤
        if rooms:
            query = query.filter(MushroomEnvDailyStats.room_id.in_(rooms))
        
        # 按日期降序排列
        query = query.order_by(desc(MushroomEnvDailyStats.stat_date))
        
        # 执行查询并转换为 DataFrame
        df = pd.read_sql(query.statement, pgsql_engine)
        
        if df.empty:
            logger.warning("未找到符合条件的环境统计数据")
            return pd.DataFrame()
        
        # 过滤数据不足的库房
        room_counts = df['room_id'].value_counts()
        valid_rooms = room_counts[room_counts >= min_records_per_room].index.tolist()
        
        if not valid_rooms:
            logger.warning(f"没有库房的记录数达到最小要求 {min_records_per_room} 条")
            return pd.DataFrame()
        
        df = df[df['room_id'].isin(valid_rooms)]
        
        logger.info(f"成功查询到 {len(df)} 条记录，涉及库房: {sorted(valid_rooms)}")
        logger.info(f"数据时间范围: {df['stat_date'].min()} 到 {df['stat_date'].max()}")
        
        return df
        
    except Exception as e:
        logger.error(f"查询最新批次数据失败: {e}")
        return pd.DataFrame()
    finally:
        session.close()


def get_latest_batch_by_room(room_id: str, max_days_back: int = 60) -> pd.DataFrame:
    """
    获取指定库房的最新批次数据
    
    Args:
        room_id: 库房编号
        max_days_back: 最大回溯天数
        
    Returns:
        pd.DataFrame: 该库房最新批次的完整数据
    """
    Session = sessionmaker(bind=pgsql_engine)
    session = Session()
    
    try:
        # 查找最新的批次日期
        latest_batch_query = session.query(MushroomEnvDailyStats.batch_date)\
            .filter(MushroomEnvDailyStats.room_id == room_id)\
            .filter(MushroomEnvDailyStats.batch_date.isnot(None))\
            .order_by(desc(MushroomEnvDailyStats.stat_date))\
            .limit(1)
        
        latest_batch_result = latest_batch_query.first()
        
        if not latest_batch_result:
            logger.warning(f"库房 {room_id} 未找到批次信息，使用时间范围查询")
            # 回退到时间范围查询
            cutoff_date = date.today() - timedelta(days=max_days_back)
            query = session.query(MushroomEnvDailyStats)\
                .filter(MushroomEnvDailyStats.room_id == room_id)\
                .filter(MushroomEnvDailyStats.stat_date >= cutoff_date)\
                .order_by(desc(MushroomEnvDailyStats.stat_date))
        else:
            latest_batch_date = latest_batch_result[0]
            logger.info(f"库房 {room_id} 最新批次日期: {latest_batch_date}")
            
            # 查询该批次的所有数据
            query = session.query(MushroomEnvDailyStats)\
                .filter(MushroomEnvDailyStats.room_id == room_id)\
                .filter(MushroomEnvDailyStats.batch_date == latest_batch_date)\
                .order_by(MushroomEnvDailyStats.stat_date)
        
        df = pd.read_sql(query.statement, pgsql_engine)
        
        if not df.empty:
            logger.info(f"库房 {room_id} 查询到 {len(df)} 条记录")
        
        return df
        
    except Exception as e:
        logger.error(f"查询库房 {room_id} 最新批次数据失败: {e}")
        return pd.DataFrame()
    finally:
        session.close()


def create_violin_distribution_data(df: pd.DataFrame, room_id: str) -> Dict[str, Any]:
    """
    为 Violin 图创建分布数据
    
    由于 MushroomEnvDailyStats 存储的是每日统计值而非原始分布数据，
    我们使用统计值（min, q25, median, q75, max）来模拟分布
    
    Args:
        df: 单个库房的日统计数据
        room_id: 库房编号
        
    Returns:
        Dict: 包含模拟分布数据的字典
    """
    distribution_data = {
        'dates': [],
        'temperature': {'values': [], 'dates': []},
        'humidity': {'values': [], 'dates': []},
        'co2': {'values': [], 'dates': []}
    }
    
    for _, row in df.iterrows():
        stat_date = row['stat_date']
        
        # 为每个环境参数创建模拟分布数据
        for param in ['temperature', 'humidity', 'co2']:
            min_col = f'{param}_min' if param != 'temperature' else 'temp_min'
            q25_col = f'{param}_q25' if param != 'temperature' else 'temp_q25'
            median_col = f'{param}_median' if param != 'temperature' else 'temp_median'
            q75_col = f'{param}_q75' if param != 'temperature' else 'temp_q75'
            max_col = f'{param}_max' if param != 'temperature' else 'temp_max'
            
            # 检查数据完整性
            values = [row[min_col], row[q25_col], row[median_col], row[q75_col], row[max_col]]
            if all(pd.notna(v) for v in values):
                # 使用统计值创建模拟分布
                # 在各分位数之间插值生成更多数据点
                min_val, q25_val, median_val, q75_val, max_val = values
                
                # 生成模拟数据点（每个统计区间生成多个点）
                simulated_points = []
                
                # min 到 q25 之间
                simulated_points.extend(np.linspace(min_val, q25_val, 5))
                # q25 到 median 之间（更多点，因为这是主要分布区域）
                simulated_points.extend(np.linspace(q25_val, median_val, 8))
                # median 到 q75 之间
                simulated_points.extend(np.linspace(median_val, q75_val, 8))
                # q75 到 max 之间
                simulated_points.extend(np.linspace(q75_val, max_val, 5))
                
                # 添加到分布数据
                distribution_data[param]['values'].extend(simulated_points)
                distribution_data[param]['dates'].extend([stat_date] * len(simulated_points))
    
    return distribution_data


def plot_room_daily_stats_violin(
    df: pd.DataFrame, 
    room_id: str, 
    show: bool = True
) -> go.Figure:
    """
    基于 MushroomEnvDailyStats 数据创建库房环境统计的 Violin 图
    
    Args:
        df: 单个库房的日统计数据
        room_id: 库房编号
        show: 是否显示图表
        
    Returns:
        go.Figure: Plotly 图表对象
    """
    if df.empty:
        logger.warning(f"库房 {room_id} 数据为空，无法生成图表")
        return go.Figure()
    
    # 确保日期列为 datetime 类型
    df = df.copy()
    df['stat_date'] = pd.to_datetime(df['stat_date'])
    df = df.sort_values('stat_date')
    
    # 创建子图
    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=(
            "每日温度分布 (基于统计值模拟)",
            "每日湿度分布 (基于统计值模拟)", 
            "每日CO2分布 (基于统计值模拟)"
        ),
        row_heights=[0.33, 0.33, 0.34]
    )
    
    # 颜色配置
    colors_env = {
        'temperature': {'violin': '#3366CC', 'fill': 'rgba(51, 102, 204, 0.2)', 'line': '#3366CC'},
        'humidity': {'violin': '#00CC96', 'fill': 'rgba(0, 204, 150, 0.2)', 'line': '#00CC96'},
        'co2': {'violin': '#9467BD', 'fill': 'rgba(148, 103, 189, 0.2)', 'line': '#9467BD'}
    }
    
    # 创建模拟分布数据
    distribution_data = create_violin_distribution_data(df, room_id)
    
    # 添加 Violin 图
    param_configs = [
        ('temperature', 1, '温度 (°C)'),
        ('humidity', 2, '湿度 (%)'),
        ('co2', 3, 'CO2 (ppm)')
    ]
    
    for param, row_num, y_title in param_configs:
        if distribution_data[param]['values']:
            color_config = colors_env[param]
            
            fig.add_trace(
                go.Violin(
                    x=distribution_data[param]['dates'],
                    y=distribution_data[param]['values'],
                    name=f'{param.title()} 分布',
                    legendgroup=param,
                    showlegend=True,
                    line_color=color_config['violin'],
                    fillcolor=color_config['fill'],
                    scalemode='width',
                    points='outliers',
                    box_visible=True,
                    meanline_visible=True,
                    hovertemplate='%{x|%Y-%m-%d}<br>%{y:.2f}<extra></extra>'
                ),
                row=row_num,
                col=1
            )
            
            # 添加中位数趋势线
            median_col = f'{param}_median' if param != 'temperature' else 'temp_median'
            if median_col in df.columns:
                median_data = df[df[median_col].notna()]
                if not median_data.empty:
                    fig.add_trace(
                        go.Scatter(
                            x=median_data['stat_date'],
                            y=median_data[median_col],
                            mode='lines+markers',
                            name=f'{param.title()} 中位数趋势',
                            legendgroup=param,
                            showlegend=True,
                            line=dict(color=color_config['line'], width=2.5),
                            marker=dict(size=4, symbol='circle'),
                            hovertemplate='%{x|%Y-%m-%d}<br>中位数: %{y:.2f}<extra></extra>'
                        ),
                        row=row_num,
                        col=1
                    )
        
        # 设置 Y 轴标题
        fig.update_yaxes(title_text=y_title, row=row_num, col=1)
    
    # 添加生长阶段背景
    if 'is_growth_phase' in df.columns and 'in_day_num' in df.columns:
        try:
            # 按生长阶段分组
            df['phase_group'] = (df['is_growth_phase'] != df['is_growth_phase'].shift()).cumsum()
            
            for (is_growth, _), group in df.groupby(['is_growth_phase', 'phase_group']):
                if group.empty:
                    continue
                
                x0 = group['stat_date'].min()
                x1 = group['stat_date'].max()
                
                if is_growth:
                    color = '#E6F7E6'
                    opacity = 0.25
                    # 添加生长阶段标注
                    day_nums = group['in_day_num'].dropna()
                    if not day_nums.empty:
                        day_range = f"Day {day_nums.min()}-{day_nums.max()}" if day_nums.min() != day_nums.max() else f"Day {day_nums.min()}"
                        annotation_text = f"生长期 ({day_range})"
                    else:
                        annotation_text = "生长期"
                else:
                    color = '#FAFAFA'
                    opacity = 0.08
                    annotation_text = "非生长期"
                
                fig.add_vrect(
                    x0=x0,
                    x1=x1,
                    fillcolor=color,
                    opacity=opacity,
                    layer='below',
                    line_width=0,
                    annotation_text=annotation_text,
                    annotation_position='top left',
                    annotation_font_size=10,
                    annotation_font_color='#666666',
                    annotation_showarrow=False
                )
        except Exception as e:
            logger.warning(f"添加生长阶段背景失败: {e}")
    
    # 添加批次信息标注
    if 'batch_date' in df.columns:
        try:
            unique_batches = df['batch_date'].dropna().unique()
            color_palette = px.colors.qualitative.Plotly
            
            for i, batch_date in enumerate(unique_batches):
                batch_data = df[df['batch_date'] == batch_date]
                if not batch_data.empty:
                    x0 = batch_data['stat_date'].min()
                    x1 = batch_data['stat_date'].max()
                    color = color_palette[i % len(color_palette)]
                    
                    # 获取批次的天数范围
                    day_nums = batch_data['in_day_num'].dropna()
                    if not day_nums.empty:
                        day_info = f" (Day {day_nums.min()}-{day_nums.max()})" if day_nums.min() != day_nums.max() else f" (Day {day_nums.min()})"
                    else:
                        day_info = ""
                    
                    label = f"批次 {pd.to_datetime(batch_date).strftime('%Y-%m-%d')}{day_info}"
                    
                    fig.add_vrect(
                        x0=x0,
                        x1=x1,
                        fillcolor=color,
                        opacity=0.12,
                        layer='below',
                        line_width=0,
                        annotation_text=label,
                        annotation_position='top right',
                        annotation_font_size=9,
                        annotation_font_color=color,
                        annotation_showarrow=False
                    )
        except Exception as e:
            logger.warning(f"添加批次信息标注失败: {e}")
    
    # 更新布局
    fig.update_layout(
        height=1200,
        title_text=f"库房 {room_id} 每日环境统计分布 (基于 MushroomEnvDailyStats)",
        hovermode='x unified',
        template='plotly_white',
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        )
    )
    
    # 更新 X 轴
    fig.update_xaxes(title_text="日期", tickformat="%m-%d", row=3, col=1)
    
    if show:
        fig.show()
    
    return fig


def plot_multi_room_comparison(
    df: pd.DataFrame,
    rooms: Optional[List[str]] = None,
    show: bool = True
) -> Dict[str, go.Figure]:
    """
    生成多库房对比的可视化图表
    
    Args:
        df: 多库房的日统计数据
        rooms: 要对比的库房列表，None 表示使用所有库房
        show: 是否显示图表
        
    Returns:
        Dict[str, go.Figure]: 包含各类对比图表的字典
    """
    if df.empty:
        logger.warning("数据为空，无法生成对比图表")
        return {}
    
    # 数据预处理
    df = df.copy()
    df['stat_date'] = pd.to_datetime(df['stat_date'])
    
    if rooms:
        df = df[df['room_id'].isin(rooms)]
    
    available_rooms = sorted(df['room_id'].unique())
    logger.info(f"生成多库房对比图表，涉及库房: {available_rooms}")
    
    figs = {}
    
    # 1. 温度对比图
    fig_temp = px.line(
        df,
        x='stat_date',
        y='temp_median',
        color='room_id',
        title='各库房每日温度中位数对比',
        labels={'temp_median': '温度 (°C)', 'stat_date': '日期', 'room_id': '库房'},
        template='plotly_white'
    )
    fig_temp.update_layout(height=600)
    figs['temperature'] = fig_temp
    
    # 2. 湿度对比图
    fig_hum = px.line(
        df,
        x='stat_date',
        y='humidity_median',
        color='room_id',
        title='各库房每日湿度中位数对比',
        labels={'humidity_median': '湿度 (%)', 'stat_date': '日期', 'room_id': '库房'},
        template='plotly_white'
    )
    fig_hum.update_layout(height=600)
    figs['humidity'] = fig_hum
    
    # 3. CO2对比图
    fig_co2 = px.line(
        df,
        x='stat_date',
        y='co2_median',
        color='room_id',
        title='各库房每日CO2中位数对比',
        labels={'co2_median': 'CO2 (ppm)', 'stat_date': '日期', 'room_id': '库房'},
        template='plotly_white'
    )
    fig_co2.update_layout(height=600)
    figs['co2'] = fig_co2
    
    # 4. 生长阶段热力图
    if 'is_growth_phase' in df.columns:
        try:
            # 创建生长阶段数据透视表
            growth_pivot = df.pivot_table(
                index='room_id',
                columns='stat_date',
                values='is_growth_phase',
                fill_value=0,
                aggfunc='first'
            )
            
            fig_growth = px.imshow(
                growth_pivot,
                labels=dict(x='日期', y='库房', color='生长阶段'),
                title='各库房生长阶段分布热力图',
                aspect='auto',
                color_continuous_scale=['#FAFAFA', '#E6F7E6'],
                template='plotly_white'
            )
            fig_growth.update_layout(height=400)
            figs['growth_phase'] = fig_growth
        except Exception as e:
            logger.warning(f"生成生长阶段热力图失败: {e}")
    
    # 5. 环境参数变异性对比（使用四分位距）
    try:
        df['temp_iqr'] = df['temp_q75'] - df['temp_q25']
        df['humidity_iqr'] = df['humidity_q75'] - df['humidity_q25']
        df['co2_iqr'] = df['co2_q75'] - df['co2_q25']
        
        fig_variability = make_subplots(
            rows=3, cols=1,
            shared_xaxes=True,
            subplot_titles=('温度变异性 (IQR)', '湿度变异性 (IQR)', 'CO2变异性 (IQR)'),
            vertical_spacing=0.08
        )
        
        colors = px.colors.qualitative.Plotly
        
        for i, room in enumerate(available_rooms):
            room_data = df[df['room_id'] == room]
            color = colors[i % len(colors)]
            
            # 温度变异性
            fig_variability.add_trace(
                go.Scatter(
                    x=room_data['stat_date'],
                    y=room_data['temp_iqr'],
                    mode='lines+markers',
                    name=f'库房 {room}',
                    legendgroup=room,
                    line=dict(color=color),
                    showlegend=True
                ),
                row=1, col=1
            )
            
            # 湿度变异性
            fig_variability.add_trace(
                go.Scatter(
                    x=room_data['stat_date'],
                    y=room_data['humidity_iqr'],
                    mode='lines+markers',
                    name=f'库房 {room}',
                    legendgroup=room,
                    line=dict(color=color),
                    showlegend=False
                ),
                row=2, col=1
            )
            
            # CO2变异性
            fig_variability.add_trace(
                go.Scatter(
                    x=room_data['stat_date'],
                    y=room_data['co2_iqr'],
                    mode='lines+markers',
                    name=f'库房 {room}',
                    legendgroup=room,
                    line=dict(color=color),
                    showlegend=False
                ),
                row=3, col=1
            )
        
        fig_variability.update_layout(
            height=1200,
            title_text='各库房环境参数变异性对比 (四分位距)',
            template='plotly_white'
        )
        fig_variability.update_yaxes(title_text='温度 IQR (°C)', row=1, col=1)
        fig_variability.update_yaxes(title_text='湿度 IQR (%)', row=2, col=1)
        fig_variability.update_yaxes(title_text='CO2 IQR (ppm)', row=3, col=1)
        fig_variability.update_xaxes(title_text='日期', row=3, col=1)
        
        figs['variability'] = fig_variability
        
    except Exception as e:
        logger.warning(f"生成变异性对比图失败: {e}")
    
    if show:
        for fig in figs.values():
            fig.show()
    
    return figs


def analyze_and_visualize_latest_batch(
    rooms: Optional[List[str]] = None,
    days_back: int = 30,
    show_individual: bool = True,
    show_comparison: bool = True,
    return_figs: bool = False
) -> Optional[Dict[str, Any]]:
    """
    分析并可视化最新批次的环境数据
    
    Args:
        rooms: 要分析的库房列表
        days_back: 向前查询的天数
        show_individual: 是否显示单个库房的详细图表
        show_comparison: 是否显示多库房对比图表
        return_figs: 是否返回图表对象
        
    Returns:
        Optional[Dict]: 包含图表和数据的字典（当 return_figs=True 时）
    """
    logger.info("开始分析最新批次环境数据...")
    
    # 查询数据
    df = get_latest_batch_data(rooms=rooms, days_back=days_back)
    
    if df.empty:
        logger.error("未查询到有效数据")
        return None
    
    available_rooms = sorted(df['room_id'].unique())
    logger.info(f"数据分析涉及库房: {available_rooms}")
    
    results = {
        'data': df,
        'individual_figs': {},
        'comparison_figs': {}
    }
    
    # 生成单个库房的详细图表
    if show_individual:
        logger.info("生成单个库房详细图表...")
        for room in available_rooms:
            room_data = df[df['room_id'] == room]
            try:
                fig = plot_room_daily_stats_violin(
                    room_data, 
                    room, 
                    show=not return_figs
                )
                results['individual_figs'][room] = fig
                logger.info(f"库房 {room} 图表生成完成")
            except Exception as e:
                logger.error(f"库房 {room} 图表生成失败: {e}")
    
    # 生成多库房对比图表
    if show_comparison and len(available_rooms) > 1:
        logger.info("生成多库房对比图表...")
        try:
            comparison_figs = plot_multi_room_comparison(
                df,
                rooms=available_rooms,
                show=not return_figs
            )
            results['comparison_figs'] = comparison_figs
            logger.info("多库房对比图表生成完成")
        except Exception as e:
            logger.error(f"多库房对比图表生成失败: {e}")
    
    # 生成数据摘要
    summary = {
        'total_records': len(df),
        'rooms_count': len(available_rooms),
        'date_range': {
            'start': df['stat_date'].min(),
            'end': df['stat_date'].max()
        },
        'rooms': available_rooms
    }
    
    # 添加批次信息摘要
    if 'batch_date' in df.columns:
        batch_info = df.groupby('room_id')['batch_date'].agg(['min', 'max', 'nunique']).to_dict('index')
        summary['batch_info'] = batch_info
    
    results['summary'] = summary
    
    logger.info("环境数据分析完成")
    logger.info(f"数据摘要: {summary}")
    
    if return_figs:
        return results
    
    return None


if __name__ == "__main__":
    # 示例用法
    logger.info("开始执行环境数据可视化...")
    
    # 分析所有库房的最新批次数据
    results = analyze_and_visualize_latest_batch(
        rooms=None,  # 所有库房
        days_back=30,
        show_individual=True,
        show_comparison=True,
        return_figs=False
    )
    
    if results:
        print("\n=== 数据分析摘要 ===")
        print(f"总记录数: {results['summary']['total_records']}")
        print(f"库房数量: {results['summary']['rooms_count']}")
        print(f"涉及库房: {results['summary']['rooms']}")
        print(f"数据时间范围: {results['summary']['date_range']['start']} 到 {results['summary']['date_range']['end']}")
        
        if 'batch_info' in results['summary']:
            print("\n=== 批次信息 ===")
            for room, info in results['summary']['batch_info'].items():
                print(f"库房 {room}: 批次数量 {info['nunique']}, 时间范围 {info['min']} 到 {info['max']}")
    
    logger.info("环境数据可视化执行完成")
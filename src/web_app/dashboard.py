import sys
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func, select, desc

from global_const.global_const import pgsql_engine, BASE_DIR
from utils.create_table import (
    MushroomImageEmbedding,
    ImageTextQuality,
    DeviceSetpointChange,
    MushroomEnvDailyStats,
)
from loguru import logger

# --- Database Connection & Caching ---
Session = sessionmaker(bind=pgsql_engine)

@st.cache_data(ttl=300)  # Cache for 5 minutes
def load_room_list():
    """Load distinct room IDs from available data"""
    with Session() as session:
        # Combine room IDs from all sources
        rooms_emb = session.query(MushroomImageEmbedding.room_id).distinct().all()
        rooms_env = session.query(MushroomEnvDailyStats.room_id).distinct().all()
        rooms_dev = session.query(DeviceSetpointChange.room_id).distinct().all()
        
        all_rooms = set(r[0] for r in rooms_emb) | set(r[0] for r in rooms_env) | set(r[0] for r in rooms_dev)
        return sorted(list(all_rooms))

@st.cache_data(ttl=300)
def load_batch_data(room_ids=None, date_range=None):
    """Load batch data from MushroomImageEmbedding"""
    with Session() as session:
        latest_quality = (
            session.query(
                ImageTextQuality.image_path,
                func.max(ImageTextQuality.created_at).label('max_created_at')
            )
            .group_by(ImageTextQuality.image_path)
            .subquery()
        )

        query = session.query(
            MushroomImageEmbedding.room_id,
            MushroomImageEmbedding.in_date,
            MushroomImageEmbedding.in_num,
            func.min(MushroomImageEmbedding.growth_day).label('min_growth_day'),
            func.max(MushroomImageEmbedding.growth_day).label('max_growth_day'),
            func.min(MushroomImageEmbedding.collection_datetime).label('start_time'),
            func.max(MushroomImageEmbedding.collection_datetime).label('end_time'),
            func.avg(ImageTextQuality.image_quality_score).label('avg_quality')
        ).outerjoin(
            latest_quality,
            latest_quality.c.image_path == MushroomImageEmbedding.image_path
        ).outerjoin(
            ImageTextQuality,
            (ImageTextQuality.image_path == latest_quality.c.image_path)
            & (ImageTextQuality.created_at == latest_quality.c.max_created_at)
        ).group_by(
            MushroomImageEmbedding.room_id,
            MushroomImageEmbedding.in_date,
            MushroomImageEmbedding.in_num
        )
        
        if room_ids:
            query = query.filter(MushroomImageEmbedding.room_id.in_(room_ids))
        
        if date_range:
            query = query.filter(MushroomImageEmbedding.in_date >= date_range[0])
            query = query.filter(MushroomImageEmbedding.in_date <= date_range[1])
            
        df = pd.read_sql(query.statement, session.bind)
        return df

@st.cache_data(ttl=300)
def load_image_quality_data(room_ids=None):
    """Load raw image quality data for scatter plot"""
    with Session() as session:
        latest_quality = (
            session.query(
                ImageTextQuality.image_path,
                func.max(ImageTextQuality.created_at).label('max_created_at')
            )
            .group_by(ImageTextQuality.image_path)
            .subquery()
        )

        query = session.query(
            MushroomImageEmbedding.room_id,
            MushroomImageEmbedding.growth_day,
            ImageTextQuality.image_quality_score,
            MushroomImageEmbedding.collection_datetime
        ).outerjoin(
            latest_quality,
            latest_quality.c.image_path == MushroomImageEmbedding.image_path
        ).outerjoin(
            ImageTextQuality,
            (ImageTextQuality.image_path == latest_quality.c.image_path)
            & (ImageTextQuality.created_at == latest_quality.c.max_created_at)
        )
        
        if room_ids:
            query = query.filter(MushroomImageEmbedding.room_id.in_(room_ids))
            
        # Limit to recent 2000 records to avoid performance issues in scatter
        query = query.order_by(desc(MushroomImageEmbedding.collection_datetime)).limit(2000)
        
        df = pd.read_sql(query.statement, session.bind)
        return df

@st.cache_data(ttl=300)
def load_device_changes(room_ids=None, device_types=None):
    """Load device setpoint changes"""
    with Session() as session:
        query = session.query(DeviceSetpointChange)
        
        if room_ids:
            query = query.filter(DeviceSetpointChange.room_id.in_(room_ids))
        
        if device_types:
            query = query.filter(DeviceSetpointChange.device_type.in_(device_types))
            
        query = query.order_by(desc(DeviceSetpointChange.change_time))
        
        df = pd.read_sql(query.statement, session.bind)
        return df

@st.cache_data(ttl=300)
def load_env_stats(room_ids=None, date_range=None):
    """Load environmental daily stats"""
    with Session() as session:
        query = session.query(MushroomEnvDailyStats)
        
        if room_ids:
            query = query.filter(MushroomEnvDailyStats.room_id.in_(room_ids))
            
        if date_range:
            query = query.filter(MushroomEnvDailyStats.stat_date >= date_range[0])
            query = query.filter(MushroomEnvDailyStats.stat_date <= date_range[1])
        
        query = query.order_by(MushroomEnvDailyStats.stat_date)
        
        df = pd.read_sql(query.statement, session.bind)
        return df

def show():
    # --- CSS / Theme Optimization ---
    st.markdown("""
    <style>
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
        }
        .stMetric {
            background-color: #f0f2f6;
            padding: 10px;
            border-radius: 5px;
            border: 1px solid #e0e0e0;
        }
        [data-testid="stMetricLabel"] {
            font-size: 14px;
            font-weight: bold;
        }
        .element-container {
            margin-bottom: 1rem;
        }
    </style>
    """, unsafe_allow_html=True)

    # --- Sidebar Controls ---
    st.sidebar.title("🔍 筛选控制台")

    all_rooms = load_room_list()
    # Unique key for sidebar ensuring no conflict during re-runs
    selected_rooms = st.sidebar.multiselect(
        "选择库房 (Room ID)", 
        options=all_rooms,
        default=all_rooms[:1] if all_rooms else None,
        key="dashboard_sb_room_select"
    )

    # Date Range Filter (Default to last 30 days)
    today = datetime.now().date()
    default_start = today - timedelta(days=30)
    date_range = st.sidebar.date_input(
        "选择时间范围",
        value=(default_start, today),
        max_value=today,
        key="dashboard_sb_date_range"
    )
    # Ensure date_range tuple has 2 elements
    if isinstance(date_range, tuple) and len(date_range) != 2:
        date_range = (default_start, today)
    elif not isinstance(date_range, tuple):
        date_range = (date_range, date_range)

    # Auto-refresh
    refresh_interval = st.sidebar.selectbox("自动刷新间隔", [0, 1, 5, 15, 60], format_func=lambda x: "关闭" if x == 0 else f"{x} 分钟", key="dashboard_sb_refresh_interval")
    if refresh_interval > 0:
        st.empty()  # Placeholder

    st.sidebar.markdown("---")
    st.sidebar.info(f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # --- Main Content ---
    st.title("🍄 食用菌种植监控系统")

    tab1, tab2, tab3 = st.tabs(["🏭 库房-批次关联分析", "🔧 监控点变更追踪", "🌡️ 环境温湿度趋势"])

    # ==========================================
    # Tab 1: 库房-入库批次关联分析
    # ==========================================
    with tab1:
        st.header("库房与入库批次分析")
        
        if not selected_rooms:
            st.warning("请在左侧侧边栏选择至少一个库房")
        else:
            # Load Data
            batch_df = load_batch_data(selected_rooms, date_range)
            quality_df = load_image_quality_data(selected_rooms)
            
            if batch_df.empty:
                st.info("所选范围内无批次数据。")
            else:
                # 1.1 Metrics
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("总批次数量", len(batch_df))
                batch_metrics_df = batch_df.drop_duplicates(subset=["room_id", "in_date", "in_num"])
                in_num_series = pd.to_numeric(batch_metrics_df["in_num"], errors="coerce")
                avg_in_num = int(round(in_num_series.mean() if not in_num_series.empty else 0))
                c2.metric("平均进库包数", f"{avg_in_num}")
                c3.metric("最大生长天数", f"{batch_df['max_growth_day'].max() or 0} 天")
                c4.metric("平均图像评分", f"{batch_df['avg_quality'].mean():.1f}")
                
                # 1.2 Interactive Table
                st.subheader("📋 批次详情表")
                st.dataframe(
                    batch_df.style.format({
                        "avg_quality": "{:.1f}", 
                        "in_num": "{:.0f}"
                    }),
                    width="stretch"
                )
                
                # 1.3 Charts
                c_left, c_right = st.columns(2)
                
                with c_left:
                    st.subheader("📦 进库包数分布")
                    fig_bar = px.bar(
                        batch_df, 
                        x="room_id", 
                        y="in_num", 
                        color="room_id",
                        title="各库房进库包数对比",
                        labels={"in_num": "包数", "room_id": "库房"},
                        hover_data=["in_date"]
                    )
                    st.plotly_chart(fig_bar, width="stretch")
                
                with c_right:
                    st.subheader("📅 进库日期分布")
                    fig_timeline = px.scatter(
                        batch_df,
                        x="in_date",
                        y="room_id",
                        size="in_num",
                        color="room_id",
                        title="库房进库时间轴 (气泡大小=包数)",
                        labels={"in_date": "进库日期", "room_id": "库房"},
                    )
                    st.plotly_chart(fig_timeline, width="stretch")
                
                # 1.4 Quality Correlation
                if not quality_df.empty:
                    st.subheader("🔍 生长天数 vs 图像质量")
                    fig_scatter = px.scatter(
                        quality_df,
                        x="growth_day",
                        y="image_quality_score",
                        color="room_id",
                        title="生长图像质量分布",
                        labels={"growth_day": "生长天数", "image_quality_score": "质量评分"},
                        opacity=0.6
                    )
                    st.plotly_chart(fig_scatter, width="stretch")

    # ==========================================
    # Tab 2: 库房监控点变更追踪
    # ==========================================
    with tab2:
        st.header("设备设定点变更记录")
        
        if not selected_rooms:
            st.warning("请选择库房查看变更记录")
        else:
            # Load Data
            # Get all distinct device types for filter
            with Session() as s:
                all_devices = [r[0] for r in s.query(DeviceSetpointChange.device_type).distinct().all()]
            
            selected_devices = st.multiselect("过滤设备类型", all_devices, default=all_devices, key="dashboard_tab2_device_select")
            
            change_df = load_device_changes(selected_rooms, selected_devices)
            
            # Filter by Magnitude Threshold
            mag_threshold = st.slider("变更幅度告警阈值", 0.0, 50.0, 5.0, 0.5, key="dashboard_tab2_mag_slider")
            
            if change_df.empty:
                st.info("无变更记录。")
            else:
                # Stats
                total_changes = len(change_df)
                abnormal_changes = len(change_df[change_df['change_magnitude'] > mag_threshold])
                recent_change = change_df['change_time'].max()
                
                m1, m2, m3 = st.columns(3)
                m1.metric("总变更次数", total_changes)
                m2.metric("大幅度变更 (>阈值)", abnormal_changes, delta_color="inverse")
                m3.metric("最近变更时间", recent_change.strftime('%Y-%m-%d %H:%M') if pd.notnull(recent_change) else "N/A")
                
                # 2.1 Timeline View
                st.subheader("⏱️ 变更历史时间轴")
                
                # Mark abnormal points
                change_df['is_abnormal'] = change_df['change_magnitude'] > mag_threshold
                change_df['color_col'] = change_df.apply(lambda x: 'Abnormal (>Thres)' if x['is_abnormal'] else x['device_type'], axis=1)
                
                fig_change_timeline = px.scatter(
                    change_df,
                    x="change_time",
                    y="device_name",
                    color="device_type",
                    symbol="is_abnormal",
                    size="change_magnitude",
                    hover_data=["previous_value", "current_value", "point_name", "change_detail"],
                    title="设备变更时间分布",
                )
                st.plotly_chart(fig_change_timeline, width="stretch")
                
                # 2.2 Heatmap
                st.subheader("🔥 变更频率热力图")
                heatmap_data = change_df.groupby(['room_id', 'device_type']).size().reset_index(name='count')
                if not heatmap_data.empty:
                    fig_heat = px.density_heatmap(
                        heatmap_data,
                        x="room_id",
                        y="device_type",
                        z="count",
                        title="库房-设备类型变更频率",
                        labels={"count": "变更次数"}
                    )
                    st.plotly_chart(fig_heat, width="stretch")
                
                # 2.3 Detail Table
                st.subheader("📝 变更详情列表")
                st.warning(f"显示最近 1000 条记录 (共 {len(change_df)} 条)")
                
                display_df = change_df.head(1000).copy()
                
                # Highlight magnitude
                def highlight_abnormal(s):
                    return ['background-color: #ffcccc' if v > mag_threshold else '' for v in s]

                st.dataframe(
                    display_df[[
                        "change_time", "room_id", "device_type", "device_name", 
                        "point_name", "previous_value", "current_value", "change_magnitude", "change_detail"
                    ]].style.apply(lambda x: ['background-color: #ff4b4b; color: white' if x['change_magnitude'] > mag_threshold else '' for i in x], axis=1, subset=['change_magnitude']),
                    width="stretch"
                )

    # ==========================================
    # Tab 3: 当日温湿度变化趋势
    # ==========================================
    with tab3:
        st.header("环境温湿度分析")
        
        if not selected_rooms:
            st.warning("请选择库房")
        else:
            env_df = load_env_stats(selected_rooms, date_range)
            
            if env_df.empty:
                st.info("选定范围内无环境统计数据。")
            else:
                # 3.1 Charts per Room
                for room_id in selected_rooms:
                    room_data = env_df[env_df['room_id'] == room_id].copy()
                    if room_data.empty:
                        continue
                    
                    st.markdown(f"### 🏠 库房: {room_id}")
                    
                    # Summary for the latest day
                    latest_day = room_data.iloc[-1]
                    s1, s2, s3, s4, s5 = st.columns(5)
                    s1.metric("统计日期", str(latest_day['stat_date']))
                    s2.metric("中位温度", f"{latest_day.get('temp_median', 0):.1f} ℃")
                    s3.metric("中位湿度", f"{latest_day.get('humidity_median', 0):.1f} %")
                    s4.metric("中位CO2", f"{latest_day.get('co2_median', 0):.0f} ppm")
                    s5.metric("生长阶段", "是" if latest_day['is_growth_phase'] else "否")
                    
                    # Dual Axis Chart
                    fig = go.Figure()
                    
                    # Temperature (Area with median line)
                    # Q25-Q75 range
                    fig.add_trace(go.Scatter(
                        x=room_data['stat_date'], y=room_data['temp_q75'],
                        mode='lines', line=dict(width=0),
                        showlegend=False, hoverinfo='skip'
                    ))
                    fig.add_trace(go.Scatter(
                        x=room_data['stat_date'], y=room_data['temp_q25'],
                        mode='lines', line=dict(width=0),
                        fill='tonexty', fillcolor='rgba(255, 100, 100, 0.2)',
                        name='温度波动区间 (Q25-Q75)'
                    ))
                    
                    # Median Lines
                    fig.add_trace(go.Scatter(
                        x=room_data['stat_date'], y=room_data['temp_median'],
                        mode='lines+markers', name='温度中位数',
                        line=dict(color='red', width=2),
                        yaxis='y1'
                    ))
                    
                    # Humidity
                    fig.add_trace(go.Scatter(
                        x=room_data['stat_date'], y=room_data['humidity_median'],
                        mode='lines+markers', name='湿度中位数',
                        line=dict(color='blue', width=2),
                        yaxis='y2'
                    ))
                    
                    # Layout
                    fig.update_layout(
                        title=f"库房 {room_id} 温湿度趋势",
                        xaxis_title="日期",
                        yaxis=dict(title="温度 (℃)", side="left", range=[0, 35]),
                        yaxis2=dict(title="湿度 (%)", side="right", overlaying="y", range=[0, 100]),
                        hovermode="x unified",
                        legend=dict(orientation="h", y=1.1)
                    )
                    
                    # Threshold Alerts Lines
                    fig.add_hrect(y0=18, y1=22, line_width=0, fillcolor="green", opacity=0.1, annotation_text="适宜温度 (18-22)", annotation_position="top left")
                    
                    st.plotly_chart(fig, width="stretch")
                    
                    st.markdown("---")

    # --- Footer ---
    st.markdown("""
    <div style="text-align: center; color: gray; font-size: 0.8em; margin-top: 50px;">
        &copy; 2026 Mushroom Monitoring System | Powered by Streamlit
    </div>
    """, unsafe_allow_html=True)
    
    if refresh_interval > 0:
        time.sleep(refresh_interval * 60)
        st.rerun()

# For compatibility if run directly
if __name__ == "__main__":
    show()

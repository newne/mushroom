import re
import time
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import desc, func
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine, static_settings
from utils.create_table import (
    DecisionAnalysisStaticConfig,
    DeviceSetpointChange,
    ImageTextQuality,
    MushroomEnvDailyStats,
    MushroomImageEmbedding,
)

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

        all_rooms = (
            set(r[0] for r in rooms_emb)
            | set(r[0] for r in rooms_env)
            | set(r[0] for r in rooms_dev)
        )
        return sorted(list(all_rooms))


@st.cache_data(ttl=300)
def load_batch_data(room_ids=None, date_range=None):
    """Load batch data from MushroomImageEmbedding"""
    with Session() as session:
        latest_quality = (
            session.query(
                ImageTextQuality.image_path,
                func.max(ImageTextQuality.created_at).label("max_created_at"),
            )
            .group_by(ImageTextQuality.image_path)
            .subquery()
        )

        query = (
            session.query(
                MushroomImageEmbedding.room_id,
                MushroomImageEmbedding.in_date,
                MushroomImageEmbedding.in_num,
                func.min(MushroomImageEmbedding.growth_day).label("min_growth_day"),
                func.max(MushroomImageEmbedding.growth_day).label("max_growth_day"),
                func.min(MushroomImageEmbedding.collection_datetime).label(
                    "start_time"
                ),
                func.max(MushroomImageEmbedding.collection_datetime).label("end_time"),
                func.avg(ImageTextQuality.image_quality_score).label("avg_quality"),
            )
            .outerjoin(
                latest_quality,
                latest_quality.c.image_path == MushroomImageEmbedding.image_path,
            )
            .outerjoin(
                ImageTextQuality,
                (ImageTextQuality.image_path == latest_quality.c.image_path)
                & (ImageTextQuality.created_at == latest_quality.c.max_created_at),
            )
            .group_by(
                MushroomImageEmbedding.room_id,
                MushroomImageEmbedding.in_date,
                MushroomImageEmbedding.in_num,
            )
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
                func.max(ImageTextQuality.created_at).label("max_created_at"),
            )
            .group_by(ImageTextQuality.image_path)
            .subquery()
        )

        query = (
            session.query(
                MushroomImageEmbedding.room_id,
                MushroomImageEmbedding.growth_day,
                ImageTextQuality.image_quality_score,
                MushroomImageEmbedding.collection_datetime,
            )
            .outerjoin(
                latest_quality,
                latest_quality.c.image_path == MushroomImageEmbedding.image_path,
            )
            .outerjoin(
                ImageTextQuality,
                (ImageTextQuality.image_path == latest_quality.c.image_path)
                & (ImageTextQuality.created_at == latest_quality.c.max_created_at),
            )
        )

        if room_ids:
            query = query.filter(MushroomImageEmbedding.room_id.in_(room_ids))

        # Limit to recent 2000 records to avoid performance issues in scatter
        query = query.order_by(desc(MushroomImageEmbedding.collection_datetime)).limit(
            2000
        )

        df = pd.read_sql(query.statement, session.bind)
        return df


@st.cache_data(ttl=300)
def load_device_changes(room_ids=None, device_types=None, date_range=None):
    """Load device setpoint changes"""
    with Session() as session:
        query = session.query(DeviceSetpointChange)

        if room_ids:
            query = query.filter(DeviceSetpointChange.room_id.in_(room_ids))

        if device_types:
            query = query.filter(DeviceSetpointChange.device_type.in_(device_types))

        if date_range:
            query = query.filter(DeviceSetpointChange.change_time >= date_range[0])
            query = query.filter(DeviceSetpointChange.change_time <= date_range[1])

        query = query.order_by(desc(DeviceSetpointChange.change_time))

        df = pd.read_sql(query.statement, session.bind)
        return df


@st.cache_data(ttl=300)
def load_static_config_map(room_ids=None):
    """Load static config info for remarks and aliases"""
    with Session() as session:
        query = session.query(
            DecisionAnalysisStaticConfig.room_id,
            DecisionAnalysisStaticConfig.device_type,
            DecisionAnalysisStaticConfig.device_name,
            DecisionAnalysisStaticConfig.device_alias,
            DecisionAnalysisStaticConfig.point_name,
            DecisionAnalysisStaticConfig.point_alias,
            DecisionAnalysisStaticConfig.remark,
        ).filter(DecisionAnalysisStaticConfig.is_active.is_(True))

        if room_ids:
            query = query.filter(DecisionAnalysisStaticConfig.room_id.in_(room_ids))

        df = pd.read_sql(query.statement, session.bind)
        df = df.rename(columns={"remark": "point_remark"})
        return df


def build_device_remark_map() -> dict:
    """Build device remark map from static settings"""
    remark_map = {}
    datapoint_cfg = static_settings.get("mushroom", {}).get("datapoint", {})
    if not isinstance(datapoint_cfg, dict):
        return remark_map

    for device_type, device_cfg in datapoint_cfg.items():
        if not isinstance(device_cfg, dict):
            continue
        device_list = device_cfg.get("device_list", [])
        if not isinstance(device_list, list):
            continue
        for device in device_list:
            if not isinstance(device, dict):
                continue
            device_alias = device.get("device_alias")
            if not device_alias:
                continue
            remark_map[(device_type, device_alias)] = device.get("remark")

    return remark_map


@st.cache_data(ttl=300)
def load_env_stats(room_ids=None, date_range=None):
    """Load environmental daily stats"""
    with Session() as session:
        from sqlalchemy import inspect, text

        inspector = inspect(session.bind)
        available_columns = {
            col["name"] for col in inspector.get_columns("mushroom_env_daily_stats")
        }
        desired_columns = [
            "id",
            "room_id",
            "stat_date",
            "in_day_num",
            "is_growth_phase",
            "temp_median",
            "temp_min",
            "temp_max",
            "temp_q25",
            "temp_q75",
            "temp_count",
            "humidity_median",
            "humidity_min",
            "humidity_max",
            "humidity_q25",
            "humidity_q75",
            "humidity_count",
            "co2_median",
            "co2_min",
            "co2_max",
            "co2_q25",
            "co2_q75",
            "co2_count",
            "batch_date",
            "remark",
            "created_at",
            "updated_at",
        ]
        selected_columns = [col for col in desired_columns if col in available_columns]

        query = f"SELECT {', '.join(selected_columns)} FROM mushroom_env_daily_stats WHERE 1=1"
        params = {}

        if room_ids:
            query += " AND room_id = ANY(:room_ids)"
            params["room_ids"] = list(room_ids)

        if date_range:
            query += " AND stat_date >= :start_date AND stat_date <= :end_date"
            params["start_date"] = date_range[0]
            params["end_date"] = date_range[1]

        query += " ORDER BY stat_date"

        df = pd.read_sql(text(query), session.bind, params=params)
        return df


def parse_growth_day_from_remark(remark, max_day=365):
    if not remark or not isinstance(remark, str):
        return None

    text = remark.strip()
    if not text:
        return None

    patterns = [
        r"第\s*(\d{1,3})\s*天",
        r"生长\s*第\s*(\d{1,3})\s*天",
        r"\bD\s*(\d{1,3})\b",
        r"\bDay\s*(\d{1,3})\b",
        r"\b(\d{1,3})\s*天\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        day = int(match.group(1))
        if 0 < day <= max_day:
            return day

    return None


def extract_growth_day_from_remarks(device_remark, point_remark):
    day = parse_growth_day_from_remark(point_remark)
    if day is not None:
        return day
    return parse_growth_day_from_remark(device_remark)


def format_value_for_summary(value):
    if value is None or pd.isna(value):
        return "N/A"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def build_action_signature(row):
    device = row.get("device_display") or row.get("device_name") or "unknown_device"
    point = row.get("point_display") or row.get("point_name") or "unknown_point"
    change_type = row.get("change_type") or "unknown"
    return f"{device}/{point} · {change_type}"


def build_action_summary(row):
    device = row.get("device_display") or row.get("device_name") or "unknown_device"
    point = row.get("point_display") or row.get("point_name") or "unknown_point"
    change_type = row.get("change_type") or "unknown"
    prev_value = format_value_for_summary(row.get("previous_value"))
    curr_value = format_value_for_summary(row.get("current_value"))
    detail = row.get("change_detail") or ""
    parts = [f"{device}/{point}", change_type, f"{prev_value} -> {curr_value}"]
    if detail:
        parts.append(detail)
    return " | ".join(parts)


def aggregate_hourly_actions(change_df):
    if change_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    hourly_counts = change_df.groupby("change_hour").size().reset_index(name="count")
    hourly_signature = (
        change_df.groupby(["change_hour", "action_signature"])
        .size()
        .reset_index(name="count")
        .sort_values(["change_hour", "count"], ascending=[True, False])
    )
    top_actions = hourly_signature.groupby("change_hour").head(1).copy()
    top_actions["main_action"] = top_actions.apply(
        lambda row: f"{row['action_signature']} ({row['count']}次)", axis=1
    )
    hourly_summary = hourly_counts.merge(
        top_actions[["change_hour", "main_action"]],
        on="change_hour",
        how="left",
    )
    hourly_summary["main_action"] = hourly_summary["main_action"].fillna("无")
    return hourly_summary, hourly_signature


def show():
    # --- CSS / Theme Optimization ---
    st.markdown(
        """
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
    """,
        unsafe_allow_html=True,
    )

    # --- Sidebar Controls ---
    st.sidebar.title("🔍 筛选控制台")

    all_rooms = load_room_list()
    # Unique key for sidebar ensuring no conflict during re-runs
    selected_rooms = st.sidebar.multiselect(
        "选择库房 (Room ID)",
        options=all_rooms,
        default=all_rooms[:1] if all_rooms else None,
        key="dashboard_sb_room_select",
    )

    # Date Range Filter (Default to last 30 days)
    today = datetime.now().date()
    default_start = today - timedelta(days=30)
    date_range = st.sidebar.date_input(
        "选择时间范围",
        value=(default_start, today),
        max_value=today,
        key="dashboard_sb_date_range",
    )
    # Ensure date_range tuple has 2 elements
    if isinstance(date_range, tuple) and len(date_range) != 2:
        date_range = (default_start, today)
    elif not isinstance(date_range, tuple):
        date_range = (date_range, date_range)

    # Auto-refresh
    refresh_interval = st.sidebar.selectbox(
        "自动刷新间隔",
        [0, 1, 5, 15, 60],
        format_func=lambda x: "关闭" if x == 0 else f"{x} 分钟",
        key="dashboard_sb_refresh_interval",
    )
    if refresh_interval > 0:
        st.empty()  # Placeholder

    st.sidebar.markdown("---")
    st.sidebar.info(f"最后更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # --- Main Content ---
    st.title("🍄 食用菌种植监控系统")

    tab1, tab2, tab3 = st.tabs(
        ["🏭 库房-批次关联分析", "🔧 监控点变更追踪", "🌡️ 环境温湿度趋势"]
    )

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
                batch_metrics_df = batch_df.drop_duplicates(
                    subset=["room_id", "in_date", "in_num"]
                )
                in_num_series = pd.to_numeric(
                    batch_metrics_df["in_num"], errors="coerce"
                )
                avg_in_num = int(
                    round(in_num_series.mean() if not in_num_series.empty else 0)
                )
                c2.metric("平均进库包数", f"{avg_in_num}")
                c3.metric("最大生长天数", f"{batch_df['max_growth_day'].max() or 0} 天")
                c4.metric("平均图像评分", f"{batch_df['avg_quality'].mean():.1f}")

                # 1.2 Interactive Table
                st.subheader("📋 批次详情表")
                st.dataframe(
                    batch_df.style.format(
                        {"avg_quality": "{:.1f}", "in_num": "{:.0f}"}
                    ),
                    width="stretch",
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
                        hover_data=["in_date"],
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
                        labels={
                            "growth_day": "生长天数",
                            "image_quality_score": "质量评分",
                        },
                        opacity=0.6,
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
                all_devices = [
                    r[0]
                    for r in s.query(DeviceSetpointChange.device_type).distinct().all()
                ]

            selected_devices = st.multiselect(
                "过滤设备类型",
                all_devices,
                default=all_devices,
                key="dashboard_tab2_device_select",
            )

            batch_df = load_batch_data(selected_rooms, date_range)
            selected_batch_ids = []
            if not batch_df.empty:
                batch_df = batch_df.copy()
                batch_df["batch_id"] = (
                    batch_df["room_id"].astype(str)
                    + "_"
                    + batch_df["in_date"].astype(str)
                    + "_"
                    + batch_df["in_num"].astype(str)
                )
                batch_df["batch_label"] = (
                    batch_df["room_id"].astype(str)
                    + " | "
                    + batch_df["in_date"].astype(str)
                    + " | 批次"
                    + batch_df["in_num"].astype(str)
                    + " | "
                    + batch_df["start_time"].dt.strftime("%m-%d %H:%M")
                    + " ~ "
                    + batch_df["end_time"].dt.strftime("%m-%d %H:%M")
                )

                batch_options = dict(zip(batch_df["batch_id"], batch_df["batch_label"]))
                selected_batch_ids = st.multiselect(
                    "按入库批次过滤",
                    options=list(batch_options.keys()),
                    default=list(batch_options.keys()),
                    format_func=lambda x: batch_options.get(x, x),
                    key="dashboard_tab2_batch_select",
                )
            else:
                st.info("当前筛选范围内没有可用批次记录。")

            change_df = load_device_changes(
                selected_rooms, selected_devices, date_range
            )

            if not change_df.empty:
                change_df["change_time"] = pd.to_datetime(
                    change_df["change_time"], errors="coerce"
                )

            if change_df.empty:
                st.info("无变更记录。")
            else:
                static_cfg_df = load_static_config_map(selected_rooms)
                if not static_cfg_df.empty:
                    change_df = change_df.merge(
                        static_cfg_df,
                        on=["room_id", "device_type", "device_name", "point_name"],
                        how="left",
                    )

                device_remark_map = build_device_remark_map()
                if "device_alias" in change_df.columns:
                    device_keys = list(
                        zip(change_df["device_type"], change_df["device_alias"])
                    )
                    change_df["device_remark"] = [
                        device_remark_map.get(key) for key in device_keys
                    ]
                else:
                    change_df["device_remark"] = None

                change_df["point_remark"] = change_df["point_remark"].fillna(
                    change_df.get("point_description")
                )

                change_df["device_display"] = change_df["device_remark"].fillna(
                    change_df["device_name"]
                )
                change_df["point_display"] = change_df["point_remark"].fillna(
                    change_df["point_name"]
                )
                change_df["timeline_label"] = (
                    change_df["device_display"] + " · " + change_df["point_display"]
                )

                change_df["growth_day"] = change_df.apply(
                    lambda row: extract_growth_day_from_remarks(
                        row.get("device_remark"), row.get("point_remark")
                    ),
                    axis=1,
                )
                change_df["action_signature"] = change_df.apply(
                    build_action_signature, axis=1
                )
                change_df["action_summary"] = change_df.apply(
                    build_action_summary, axis=1
                )

                if selected_batch_ids:
                    selected_batches = batch_df[
                        batch_df["batch_id"].isin(selected_batch_ids)
                    ][["room_id", "batch_id", "batch_label", "start_time", "end_time"]]

                    batch_filtered = change_df.merge(
                        selected_batches, on="room_id", how="inner"
                    )
                    batch_filtered = batch_filtered[
                        (batch_filtered["change_time"] >= batch_filtered["start_time"])
                        & (batch_filtered["change_time"] <= batch_filtered["end_time"])
                    ]

                    unique_cols = [
                        col
                        for col in [
                            "id",
                            "room_id",
                            "device_name",
                            "point_name",
                            "change_time",
                            "current_value",
                            "previous_value",
                        ]
                        if col in batch_filtered.columns
                    ]
                    if unique_cols:
                        change_df = batch_filtered.drop_duplicates(subset=unique_cols)
                    else:
                        change_df = batch_filtered.drop_duplicates()
                elif "batch_label" not in change_df.columns:
                    change_df["batch_label"] = None

                # Stats
                total_changes = len(change_df)
                device_count = change_df["device_display"].nunique()
                recent_change = change_df["change_time"].max()

                m1, m2, m3 = st.columns(3)
                m1.metric("总变更次数", total_changes)
                m2.metric("涉及设备数", device_count)
                m3.metric(
                    "最近变更时间",
                    recent_change.strftime("%Y-%m-%d %H:%M")
                    if pd.notnull(recent_change)
                    else "N/A",
                )

                st.subheader("🧭 批次操作概览")
                if "batch_label" in change_df.columns:
                    batch_summary = (
                        change_df.groupby(["room_id", "batch_label"])
                        .agg(
                            changes=("change_time", "count"),
                            devices=("device_display", "nunique"),
                            points=("point_display", "nunique"),
                            first_change=("change_time", "min"),
                            last_change=("change_time", "max"),
                        )
                        .reset_index()
                        .sort_values("changes", ascending=False)
                    )
                    st.dataframe(
                        batch_summary,
                        width="stretch",
                    )
                else:
                    st.info("未能关联批次数据，无法生成批次概览。")

                # 2.1 Timeline View
                st.subheader("⏱️ 变更历史时间轴")

                change_type_options = sorted(
                    [t for t in change_df["change_type"].dropna().unique()]
                )
                selected_change_types = st.multiselect(
                    "过滤操作类型",
                    change_type_options,
                    default=change_type_options,
                    key="dashboard_tab2_change_type_select",
                )

                filtered_change_df = change_df.copy()
                if selected_change_types:
                    filtered_change_df = filtered_change_df[
                        filtered_change_df["change_type"].isin(selected_change_types)
                    ]

                growth_days = (
                    filtered_change_df["growth_day"].dropna().astype(int).tolist()
                )
                if growth_days:
                    min_day = int(min(growth_days))
                    max_day = int(max(growth_days))
                    growth_day_range = st.slider(
                        "生长天数范围",
                        min_day,
                        max_day,
                        (min_day, max_day),
                        key="dashboard_tab2_growth_day_range",
                    )
                    filtered_change_df = filtered_change_df[
                        filtered_change_df["growth_day"].between(
                            growth_day_range[0], growth_day_range[1]
                        )
                    ]
                else:
                    st.info("未从设备或点位备注中解析到生长天数，时间轴暂不可用。")

                timeline_df = filtered_change_df.dropna(subset=["growth_day"]).copy()
                if timeline_df.empty:
                    st.info("时间轴暂无可展示数据，请调整筛选条件。")
                else:
                    size_col = (
                        "change_magnitude"
                        if timeline_df["change_magnitude"].notna().any()
                        else None
                    )
                    fig_change_timeline = px.scatter(
                        timeline_df,
                        x="growth_day",
                        y="device_type",
                        color="device_type",
                        symbol="change_type",
                        size=size_col,
                        hover_data=[
                            "device_display",
                            "point_display",
                            "change_type",
                            "previous_value",
                            "current_value",
                            "change_detail",
                            "batch_label",
                        ],
                        title="设备变更时间轴 (生长天数)",
                    )
                    fig_change_timeline.update_layout(
                        xaxis_title="生长第几天",
                        yaxis_title="设备类型",
                    )
                    st.plotly_chart(fig_change_timeline, width="stretch")

                    day_options = sorted(timeline_df["growth_day"].unique())
                    selected_day = st.selectbox(
                        "选择生长天数查看详情",
                        options=day_options,
                        key="dashboard_tab2_timeline_day_select",
                    )
                    day_detail_df = timeline_df[
                        timeline_df["growth_day"] == selected_day
                    ].copy()
                    st.dataframe(
                        day_detail_df[
                            [
                                "change_time",
                                "device_type",
                                "device_display",
                                "point_display",
                                "change_type",
                                "previous_value",
                                "current_value",
                                "change_detail",
                            ]
                        ],
                        width="stretch",
                    )

                st.subheader("📈 操作模式概览")
                filtered_change_df["change_hour"] = filtered_change_df[
                    "change_time"
                ].dt.hour
                hourly_summary, hourly_signature = aggregate_hourly_actions(
                    filtered_change_df
                )
                if not hourly_summary.empty:
                    fig_hour = px.bar(
                        hourly_summary,
                        x="change_hour",
                        y="count",
                        text="main_action",
                        title="操作时间分布 (小时)",
                        labels={"change_hour": "小时", "count": "变更次数"},
                    )
                    fig_hour.update_traces(textposition="outside")
                    st.caption("点击柱形查看该小时的操作详情")
                    selection = None
                    try:
                        selection = st.plotly_chart(
                            fig_hour,
                            width="stretch",
                            on_select="rerun",
                            selection_mode="points",
                        )
                    except TypeError:
                        st.plotly_chart(fig_hour, width="stretch")

                    selected_hour = None
                    if isinstance(selection, dict):
                        points = selection.get("selection", {}).get("points", [])
                        if points:
                            selected_hour = points[0].get("x")
                    if selected_hour is None:
                        selected_hour = st.selectbox(
                            "选择小时查看详情",
                            options=sorted(hourly_summary["change_hour"].unique()),
                            key="dashboard_tab2_hour_select",
                        )

                    hour_detail_df = filtered_change_df[
                        filtered_change_df["change_hour"] == selected_hour
                    ].copy()
                    hour_detail_df = hour_detail_df.sort_values(
                        "change_time", ascending=False
                    )
                    st.dataframe(
                        hour_detail_df[
                            [
                                "change_time",
                                "device_type",
                                "device_display",
                                "point_display",
                                "change_type",
                                "previous_value",
                                "current_value",
                                "change_detail",
                            ]
                        ],
                        width="stretch",
                    )

                top_points = (
                    filtered_change_df.groupby(["device_display", "point_display"])
                    .size()
                    .reset_index(name="count")
                    .sort_values("count", ascending=False)
                    .head(10)
                )
                if not top_points.empty:
                    fig_top = px.bar(
                        top_points,
                        x="count",
                        y="point_display",
                        color="device_display",
                        orientation="h",
                        title="高频操作点位 (TOP 10)",
                        labels={"count": "变更次数", "point_display": "点位"},
                    )
                    fig_top.update_layout(yaxis=dict(categoryorder="total ascending"))
                    st.plotly_chart(fig_top, width="stretch")

                # 2.2 Heatmap
                st.subheader("🔥 变更频率热力图")
                heatmap_data = (
                    filtered_change_df.groupby(["room_id", "device_type"])
                    .size()
                    .reset_index(name="count")
                )
                if not heatmap_data.empty:
                    fig_heat = px.density_heatmap(
                        heatmap_data,
                        x="room_id",
                        y="device_type",
                        z="count",
                        title="库房-设备类型变更频率",
                        labels={"count": "变更次数"},
                    )
                    st.plotly_chart(fig_heat, width="stretch")

                # 2.3 Detail Table
                st.subheader("📝 变更详情列表")
                st.warning(f"显示最近 1000 条记录 (共 {len(change_df)} 条)")

                display_df = change_df.head(1000).copy()

                st.dataframe(
                    display_df[
                        [
                            "change_time",
                            "room_id",
                            "batch_label",
                            "device_type",
                            "device_name",
                            "device_display",
                            "point_name",
                            "point_display",
                            "previous_value",
                            "current_value",
                            "change_magnitude",
                            "change_detail",
                        ]
                    ],
                    width="stretch",
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
                    room_data = env_df[env_df["room_id"] == room_id].copy()
                    if room_data.empty:
                        continue

                    st.markdown(f"### 🏠 库房: {room_id}")

                    # Summary for the latest day
                    latest_day = room_data.iloc[-1]
                    s1, s2, s3, s4, s5 = st.columns(5)
                    s1.metric("统计日期", str(latest_day["stat_date"]))
                    s2.metric("中位温度", f"{latest_day.get('temp_median', 0):.1f} ℃")
                    s3.metric(
                        "中位湿度", f"{latest_day.get('humidity_median', 0):.1f} %"
                    )
                    s4.metric("中位CO2", f"{latest_day.get('co2_median', 0):.0f} ppm")
                    s5.metric(
                        "生长阶段", "是" if latest_day["is_growth_phase"] else "否"
                    )

                    # Dual Axis Chart
                    fig = go.Figure()

                    # Temperature (Area with median line)
                    # Q25-Q75 range
                    fig.add_trace(
                        go.Scatter(
                            x=room_data["stat_date"],
                            y=room_data["temp_q75"],
                            mode="lines",
                            line=dict(width=0),
                            showlegend=False,
                            hoverinfo="skip",
                        )
                    )
                    fig.add_trace(
                        go.Scatter(
                            x=room_data["stat_date"],
                            y=room_data["temp_q25"],
                            mode="lines",
                            line=dict(width=0),
                            fill="tonexty",
                            fillcolor="rgba(255, 100, 100, 0.2)",
                            name="温度波动区间 (Q25-Q75)",
                        )
                    )

                    # Median Lines
                    fig.add_trace(
                        go.Scatter(
                            x=room_data["stat_date"],
                            y=room_data["temp_median"],
                            mode="lines+markers",
                            name="温度中位数",
                            line=dict(color="red", width=2),
                            yaxis="y1",
                        )
                    )

                    # Humidity
                    fig.add_trace(
                        go.Scatter(
                            x=room_data["stat_date"],
                            y=room_data["humidity_median"],
                            mode="lines+markers",
                            name="湿度中位数",
                            line=dict(color="blue", width=2),
                            yaxis="y2",
                        )
                    )

                    # Layout
                    fig.update_layout(
                        title=f"库房 {room_id} 温湿度趋势",
                        xaxis_title="日期",
                        yaxis=dict(title="温度 (℃)", side="left", range=[0, 35]),
                        yaxis2=dict(
                            title="湿度 (%)",
                            side="right",
                            overlaying="y",
                            range=[0, 100],
                        ),
                        hovermode="x unified",
                        legend=dict(orientation="h", y=1.1),
                    )

                    # Threshold Alerts Lines
                    fig.add_hrect(
                        y0=18,
                        y1=22,
                        line_width=0,
                        fillcolor="green",
                        opacity=0.1,
                        annotation_text="适宜温度 (18-22)",
                        annotation_position="top left",
                    )

                    st.plotly_chart(fig, width="stretch")

                    st.markdown("---")

    # --- Footer ---
    st.markdown(
        """
    <div style="text-align: center; color: gray; font-size: 0.8em; margin-top: 50px;">
        &copy; 2026 Mushroom Monitoring System | Powered by Streamlit
    </div>
    """,
        unsafe_allow_html=True,
    )

    if refresh_interval > 0:
        time.sleep(refresh_interval * 60)
        st.rerun()


# For compatibility if run directly
if __name__ == "__main__":
    show()

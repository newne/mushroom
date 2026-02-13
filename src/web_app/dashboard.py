import re
from datetime import date, datetime, timedelta
from io import BytesIO

import numpy as np
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
    MushroomBatchYield,
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
def load_batch_ranges_from_yield(room_ids=None, in_date_range=None) -> pd.DataFrame:
    with Session() as session:
        query = session.query(
            MushroomBatchYield.room_id,
            MushroomBatchYield.in_date,
            func.min(MushroomBatchYield.stat_date).label("min_date"),
            func.max(MushroomBatchYield.stat_date).label("max_date"),
        ).filter(MushroomBatchYield.room_id.isnot(None))

        if room_ids:
            query = query.filter(MushroomBatchYield.room_id.in_(room_ids))

        if in_date_range:
            query = query.filter(MushroomBatchYield.in_date >= in_date_range[0]).filter(
                MushroomBatchYield.in_date <= in_date_range[1]
            )

        query = query.group_by(MushroomBatchYield.room_id, MushroomBatchYield.in_date)
        df = pd.read_sql(query.statement, session.bind)
        if df.empty:
            return df

        df["min_date"] = pd.to_datetime(df["min_date"], errors="coerce").dt.date
        df["max_date"] = pd.to_datetime(df["max_date"], errors="coerce").dt.date
        df["in_date"] = pd.to_datetime(df["in_date"], errors="coerce").dt.date
        df["min_date"] = df["min_date"].fillna(df["in_date"])
        df["max_date"] = df["max_date"].fillna(df["in_date"])
        df["start_time"] = pd.to_datetime(
            df["min_date"].astype(str) + " 00:00:00", errors="coerce"
        )
        df["end_time"] = pd.to_datetime(
            df["max_date"].astype(str) + " 23:59:59", errors="coerce"
        )
        return df.sort_values(["in_date", "room_id"], ascending=[False, True])


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
def load_device_type_list() -> list[str]:
    with Session() as session:
        return sorted(
            [
                r[0]
                for r in session.query(DeviceSetpointChange.device_type)
                .distinct()
                .all()
                if r and r[0]
            ]
        )


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


def dataframe_to_csv_bytes(df: pd.DataFrame) -> bytes:
    if df is None:
        df = pd.DataFrame()
    return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def dataframes_to_excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes | None:
    try:
        import openpyxl
    except Exception:
        return None

    _ = openpyxl.__version__

    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for name, df in sheets.items():
            safe_name = str(name)[:31] if name else "sheet"
            (df if df is not None else pd.DataFrame()).to_excel(
                writer, sheet_name=safe_name, index=False
            )
    return buf.getvalue()


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
    parts = [f"{device}/{point}", change_type, f"{prev_value} -> {curr_value}"]
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
    top_actions["main_action"] = (
        top_actions["action_signature"].astype(str)
        + " ("
        + top_actions["count"].astype(str)
        + "次)"
    )
    hourly_summary = hourly_counts.merge(
        top_actions[["change_hour", "main_action"]],
        on="change_hour",
        how="left",
    )
    hourly_summary["main_action"] = hourly_summary["main_action"].fillna("无")
    return hourly_summary, hourly_signature


def build_batch_key(in_date) -> str:
    return f"{in_date}"


def parse_batch_key(batch_key: str) -> tuple[str, str]:
    parts = str(batch_key).split("|", 1)
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def build_batch_options(batch_df: pd.DataFrame) -> list[tuple[str, str]]:
    if batch_df is None or batch_df.empty:
        return []

    df = batch_df.copy()
    df["batch_key"] = df["in_date"].astype(str)

    agg = (
        df.groupby("batch_key")
        .agg(
            in_date=("in_date", "min"),
            rooms=("room_id", "nunique"),
            start=("start_time", "min")
            if "start_time" in df.columns
            else ("min_date", "min"),
            end=("end_time", "max")
            if "end_time" in df.columns
            else ("max_date", "max"),
        )
        .reset_index()
        .sort_values(["in_date"], ascending=[False])
    )

    options: list[tuple[str, str]] = []
    for _, row in agg.iterrows():
        start = row["start"]
        end = row["end"]
        label = (
            f"批次 {row['in_date']} | 库房{int(row['rooms'])}个 | "
            f"{pd.to_datetime(start).strftime('%m-%d %H:%M')} ~ {pd.to_datetime(end).strftime('%m-%d %H:%M')}"
        )
        options.append((str(row["batch_key"]), label))
    return options


def get_batch_windows(batch_df: pd.DataFrame, batch_key: str) -> pd.DataFrame:
    in_date_str, in_num_str = parse_batch_key(batch_key)
    if not in_date_str or batch_df is None or batch_df.empty:
        return pd.DataFrame(
            columns=["room_id", "in_date", "start_time", "end_time", "batch_label"]
        )

    df = batch_df.copy()
    df["batch_key"] = df["in_date"].astype(str)
    df = df[df["batch_key"] == batch_key].copy()
    if df.empty:
        return pd.DataFrame(
            columns=["room_id", "in_date", "start_time", "end_time", "batch_label"]
        )

    df["batch_label"] = (
        df["room_id"].astype(str)
        + " | "
        + df["in_date"].astype(str)
        + " | "
        + pd.to_datetime(df["start_time"]).dt.strftime("%m-%d %H:%M")
        + " ~ "
        + pd.to_datetime(df["end_time"]).dt.strftime("%m-%d %H:%M")
    )

    return df[
        ["room_id", "in_date", "start_time", "end_time", "batch_label"]
    ].sort_values(["room_id", "start_time"])


def filter_changes_by_windows(
    change_df: pd.DataFrame, windows_df: pd.DataFrame
) -> pd.DataFrame:
    if change_df is None or change_df.empty or windows_df is None or windows_df.empty:
        return pd.DataFrame(
            columns=change_df.columns if isinstance(change_df, pd.DataFrame) else None
        )

    merged = change_df.merge(
        windows_df[["room_id", "start_time", "end_time", "batch_label"]],
        on="room_id",
        how="inner",
    )
    merged = merged[
        (merged["change_time"] >= merged["start_time"])
        & (merged["change_time"] <= merged["end_time"])
    ]
    return merged


@st.cache_data(ttl=300)
def load_device_changes_time_window(
    room_ids: tuple[str, ...],
    device_types: tuple[str, ...] | None,
    start_time: datetime,
    end_time: datetime,
):
    with Session() as session:
        query = session.query(
            DeviceSetpointChange.id,
            DeviceSetpointChange.room_id,
            DeviceSetpointChange.device_type,
            DeviceSetpointChange.device_name,
            DeviceSetpointChange.point_name,
            DeviceSetpointChange.point_description,
            DeviceSetpointChange.change_time,
            DeviceSetpointChange.previous_value,
            DeviceSetpointChange.current_value,
            DeviceSetpointChange.change_type,
            DeviceSetpointChange.in_date,
            DeviceSetpointChange.growth_day,
            DeviceSetpointChange.in_num,
            DeviceSetpointChange.batch_id,
        ).filter(DeviceSetpointChange.room_id.in_(list(room_ids)))

        if device_types:
            query = query.filter(
                DeviceSetpointChange.device_type.in_(list(device_types))
            )

        query = query.filter(DeviceSetpointChange.change_time >= start_time).filter(
            DeviceSetpointChange.change_time <= end_time
        )
        query = query.order_by(desc(DeviceSetpointChange.change_time))
        return pd.read_sql(query.statement, session.bind)


def infer_env_metrics(device_type: str | None, point_display: str | None) -> list[str]:
    text = " ".join([str(device_type or ""), str(point_display or "")]).lower()
    metrics: list[str] = []

    if any(k in text for k in ["humid", "加湿", "湿度"]):
        metrics.append("humidity")
    if any(k in text for k in ["cool", "冷风", "温度", "temp"]):
        metrics.append("temperature")
    if any(k in text for k in ["fresh", "新风", "co2", "二氧化碳", "通风"]):
        metrics.append("co2")

    if not metrics:
        metrics = ["temperature", "humidity", "co2"]
    return metrics


@st.cache_data(ttl=300)
def load_env_timeseries_window(
    room_id: str, start_time: datetime, end_time: datetime
) -> pd.DataFrame:
    from utils.data_preprocessing import query_data_by_batch_time
    from utils.dataframe_utils import get_all_device_configs

    device_configs = get_all_device_configs(room_id=room_id)
    if not device_configs or "mushroom_env_status" not in device_configs:
        return pd.DataFrame(columns=["time", "temperature", "humidity", "co2"])

    env_config = device_configs["mushroom_env_status"]
    if env_config is None or env_config.empty:
        return pd.DataFrame(columns=["time", "temperature", "humidity", "co2"])

    if "device_alias" not in env_config.columns:
        if env_config.index.name == "device_alias":
            env_config = env_config.reset_index()
        else:
            return pd.DataFrame(columns=["time", "temperature", "humidity", "co2"])

    results = []
    for device_alias, group in env_config.groupby("device_alias", as_index=False):
        group = group.copy()
        try:
            group.name = device_alias
        except Exception:
            pass
        res = query_data_by_batch_time(group, start_time, end_time)
        if res is None or res.empty:
            continue
        results.append(res)

    if not results:
        return pd.DataFrame(columns=["time", "temperature", "humidity", "co2"])

    raw_df = pd.concat(results).reset_index(drop=True)
    raw_df["time"] = pd.to_datetime(raw_df["time"], errors="coerce")
    raw_df = raw_df.dropna(subset=["time"])
    if raw_df.empty:
        return pd.DataFrame(columns=["time", "temperature", "humidity", "co2"])

    pivot_col = "point_name"
    pivot_df = (
        raw_df.pivot_table(
            index="time", columns=pivot_col, values="value", aggfunc="mean"
        )
        .sort_index()
        .reset_index()
    )

    rename_map = {}
    for col in pivot_df.columns:
        lower = str(col).lower()
        if lower in ["temperature", "temp"]:
            rename_map[col] = "temperature"
        elif lower in ["humidity", "hum"]:
            rename_map[col] = "humidity"
        elif lower in ["co2"]:
            rename_map[col] = "co2"
    pivot_df = pivot_df.rename(columns=rename_map)

    for col in ["temperature", "humidity", "co2"]:
        if col not in pivot_df.columns:
            pivot_df[col] = pd.NA

    pivot_df = pivot_df[
        pivot_df["time"].between(pd.to_datetime(start_time), pd.to_datetime(end_time))
    ]
    return pivot_df[["time", "temperature", "humidity", "co2"]]


def compute_impact_metrics(
    ts_df: pd.DataFrame,
    metric: str,
    event_time: datetime,
    pre_minutes: int = 30,
    post_minutes: int = 30,
) -> dict:
    if ts_df is None or ts_df.empty or metric not in ts_df.columns:
        return {
            "metric": metric,
            "pre_mean": None,
            "post_mean": None,
            "delta": None,
            "rate_per_min": None,
        }

    df = ts_df.dropna(subset=["time"]).copy()
    df["time"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["time"])
    df = df.dropna(subset=[metric])
    if df.empty:
        return {
            "metric": metric,
            "pre_mean": None,
            "post_mean": None,
            "delta": None,
            "rate_per_min": None,
        }

    start_pre = event_time - timedelta(minutes=pre_minutes)
    end_pre = event_time
    start_post = event_time
    end_post = event_time + timedelta(minutes=post_minutes)

    pre = df[(df["time"] >= start_pre) & (df["time"] < end_pre)]
    post = df[(df["time"] > start_post) & (df["time"] <= end_post)]

    pre_mean = float(pre[metric].mean()) if not pre.empty else None
    post_mean = float(post[metric].mean()) if not post.empty else None
    delta = (
        (post_mean - pre_mean)
        if (pre_mean is not None and post_mean is not None)
        else None
    )

    rate = None
    if not post.empty:
        try:
            first = post.iloc[0]
            last = post.iloc[-1]
            dt_min = (last["time"] - first["time"]).total_seconds() / 60.0
            if dt_min > 0:
                rate = (float(last[metric]) - float(first[metric])) / dt_min
        except Exception:
            rate = None

    return {
        "metric": metric,
        "pre_mean": pre_mean,
        "post_mean": post_mean,
        "delta": delta,
        "rate_per_min": rate,
    }


def show_legacy():
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

    today = datetime.now().date()
    default_start = today - timedelta(days=30)

    batch_yield_df = pd.DataFrame()
    batch_options = []
    if selected_rooms:
        batch_yield_df = load_batch_ranges_from_yield(
            selected_rooms, in_date_range=None
        )
        batch_options = build_batch_options(batch_yield_df)

    selected_batch_key = None
    if batch_options:
        keys = [k for k, _ in batch_options]
        labels = {k: v for k, v in batch_options}
        selected_batch_key = st.sidebar.selectbox(
            "选择批次",
            options=keys,
            format_func=lambda k: labels.get(k, k),
            key="dashboard_sb_batch_select",
        )

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
            if selected_batch_key:
                try:
                    selected_in_date = pd.to_datetime(selected_batch_key).date()
                    batch_df = load_batch_data(
                        selected_rooms, date_range=(selected_in_date, selected_in_date)
                    )
                except Exception:
                    batch_df = load_batch_data(
                        selected_rooms, date_range=(default_start, today)
                    )
            else:
                batch_df = load_batch_data(
                    selected_rooms, date_range=(default_start, today)
                )
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
            if not selected_batch_key:
                st.info("请先在左侧选择一个批次。")
                change_df = pd.DataFrame()
                windows_df = pd.DataFrame()
            else:
                windows_df = get_batch_windows(batch_yield_df, selected_batch_key)
                if windows_df.empty:
                    st.info("未找到该批次的窗口信息。")
                    change_df = pd.DataFrame()
                else:
                    start_time = windows_df["start_time"].min()
                    end_time = windows_df["end_time"].max()
                    change_df = load_device_changes_time_window(
                        tuple(sorted(windows_df["room_id"].unique().tolist())),
                        None,
                        start_time,
                        end_time,
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
                    alias_df = static_cfg_df[
                        [
                            "room_id",
                            "device_type",
                            "device_name",
                            "point_alias",
                            "point_remark",
                        ]
                    ].rename(
                        columns={
                            "point_alias": "point_name",
                            "point_remark": "point_remark_alias",
                        }
                    )
                    change_df = change_df.merge(
                        alias_df,
                        on=["room_id", "device_type", "device_name", "point_name"],
                        how="left",
                    )
                    change_df["point_remark"] = change_df["point_remark"].fillna(
                        change_df["point_remark_alias"]
                    )
                    change_df = change_df.drop(
                        columns=["point_remark_alias"], errors="ignore"
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

                def extract_growth_day_series(
                    series: pd.Series, max_day: int = 365
                ) -> pd.Series:
                    text = series.fillna("").astype(str)
                    pattern = (
                        r"(?:生长|第)?\s*(\d{1,3})\s*(?:天|日)"
                        r"|day\s*(\d{1,3})"
                        r"|(\d{1,3})\s*天"
                    )
                    matches = text.str.extract(pattern, flags=re.IGNORECASE)
                    day = matches.bfill(axis=1).iloc[:, 0]
                    day = pd.to_numeric(day, errors="coerce")
                    return day.where((day > 0) & (day <= max_day))

                inferred_day = extract_growth_day_series(change_df["point_remark"])
                inferred_day = inferred_day.fillna(
                    extract_growth_day_series(change_df["device_remark"])
                )

                if (
                    "growth_day" not in change_df.columns
                    or change_df["growth_day"].isna().all()
                ):
                    change_df["growth_day"] = inferred_day
                else:
                    change_df["growth_day"] = change_df["growth_day"].fillna(
                        inferred_day
                    )
                device_display = change_df["device_display"].fillna(
                    change_df["device_name"].fillna("unknown_device")
                )
                point_display = change_df["point_display"].fillna(
                    change_df["point_name"].fillna("unknown_point")
                )
                change_type = change_df["change_type"].fillna("unknown")

                prev_raw = change_df.get("previous_value")
                curr_raw = change_df.get("current_value")
                prev_num = pd.to_numeric(prev_raw, errors="coerce")
                curr_num = pd.to_numeric(curr_raw, errors="coerce")
                prev_str = prev_num.round(2).astype(str)
                curr_str = curr_num.round(2).astype(str)
                prev_str = prev_str.where(prev_num.notna(), prev_raw.astype(str))
                curr_str = curr_str.where(curr_num.notna(), curr_raw.astype(str))
                prev_str = prev_str.replace({"nan": "N/A", "None": "N/A"})
                curr_str = curr_str.replace({"nan": "N/A", "None": "N/A"})

                change_df["action_signature"] = (
                    device_display + "/" + point_display + " · " + change_type
                )
                change_df["action_summary"] = (
                    device_display
                    + "/"
                    + point_display
                    + " | "
                    + change_type
                    + " | "
                    + prev_str
                    + " -> "
                    + curr_str
                )

                change_df["delta_value"] = pd.to_numeric(
                    change_df.get("current_value"), errors="coerce"
                ) - pd.to_numeric(change_df.get("previous_value"), errors="coerce")
                change_df["abs_delta_value"] = change_df["delta_value"].abs()

                if not windows_df.empty:
                    change_df = filter_changes_by_windows(change_df, windows_df)
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
                        if col in change_df.columns
                    ]
                    if unique_cols:
                        change_df = change_df.drop_duplicates(subset=unique_cols)
                    else:
                        change_df = change_df.drop_duplicates()

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

                st.subheader("🗓️ 批次每日操作总览")
                change_df["op_day"] = change_df["change_time"].dt.date
                daily_counts = (
                    change_df.groupby(["room_id", "op_day"])
                    .size()
                    .reset_index(name="count")
                    .sort_values(["op_day", "room_id"])
                )
                if daily_counts.empty:
                    st.info("该批次下无可用日级操作数据。")
                    day_ops = pd.DataFrame()
                else:
                    heat = daily_counts.pivot(
                        index="room_id", columns="op_day", values="count"
                    ).fillna(0)
                    fig_daily = px.imshow(
                        heat,
                        aspect="auto",
                        labels=dict(x="日期", y="库房", color="操作次数"),
                        title="库房-日期 操作次数热力图",
                    )
                    st.plotly_chart(
                        fig_daily,
                        width="stretch",
                        config={"scrollZoom": True, "responsive": True},
                    )

                    rooms_for_batch = sorted(change_df["room_id"].unique().tolist())
                    selected_room = st.selectbox(
                        "选择库房查看每日操作",
                        options=rooms_for_batch,
                        key="dashboard_tab2_room_select",
                    )
                    room_days = sorted(
                        change_df.loc[change_df["room_id"] == selected_room, "op_day"]
                        .unique()
                        .tolist()
                    )
                    selected_day = st.selectbox(
                        "选择日期",
                        options=room_days,
                        key="dashboard_tab2_day_select",
                    )
                    day_ops = change_df[
                        (change_df["room_id"] == selected_room)
                        & (change_df["op_day"] == selected_day)
                    ].copy()
                    day_ops = day_ops.sort_values("change_time")

                if not daily_counts.empty:
                    st.subheader("⏱️ 当日操作时间轴")
                    fig_ops = px.scatter(
                        day_ops,
                        x="change_time",
                        y="timeline_label",
                        color="change_type",
                        symbol="device_type",
                        hover_data=[
                            "device_display",
                            "point_display",
                            "change_type",
                            "previous_value",
                            "current_value",
                            "delta_value",
                        ],
                        title=f"{selected_room} | {selected_day} 操作时间轴",
                    )
                    fig_ops.update_layout(
                        xaxis_title="操作时间", yaxis_title="操作点位"
                    )
                    st.plotly_chart(
                        fig_ops,
                        width="stretch",
                        config={"scrollZoom": True, "responsive": True},
                    )

                    st.subheader("📝 当日操作明细")
                    st.dataframe(
                        day_ops[
                            [
                                "change_time",
                                "device_type",
                                "device_display",
                                "point_display",
                                "change_type",
                                "previous_value",
                                "current_value",
                                "delta_value",
                                "abs_delta_value",
                            ]
                        ],
                        width="stretch",
                    )

                    env_ts_for_export = pd.DataFrame()

                    st.subheader("🧪 操作影响分析")
                    if day_ops.empty:
                        st.info("当日无操作记录，无法进行影响分析。")
                    else:
                        ops_df = day_ops.reset_index(drop=True).copy()
                        op_times = pd.to_datetime(
                            ops_df["change_time"], errors="coerce"
                        ).dt.strftime("%H:%M:%S")
                        action_summary = (
                            ops_df["action_summary"]
                            if "action_summary" in ops_df.columns
                            else pd.Series("", index=ops_df.index)
                        )
                        ops_df["op_label"] = (
                            op_times.fillna("N/A")
                            .astype(str)
                            .str.cat(action_summary.fillna("").astype(str), sep=" | ")
                        )
                        selected_op_idx = st.selectbox(
                            "选择一条操作记录",
                            options=list(range(len(ops_df))),
                            format_func=lambda i: ops_df.loc[i, "op_label"],
                            key="dashboard_tab2_op_select",
                        )
                        op_row = ops_df.loc[selected_op_idx]
                        event_time = pd.to_datetime(
                            op_row["change_time"]
                        ).to_pydatetime()

                        window_minutes = st.selectbox(
                            "环境曲线时间窗口（分钟）",
                            options=[30, 60, 180],
                            index=1,
                            key="dashboard_tab2_env_window",
                        )
                        ts_start = event_time - timedelta(minutes=int(window_minutes))
                        ts_end = event_time + timedelta(minutes=int(window_minutes))
                        env_ts = load_env_timeseries_window(
                            selected_room, ts_start, ts_end
                        )
                        env_ts_for_export = env_ts
                        selected_metrics = infer_env_metrics(
                            op_row.get("device_type"),
                            op_row.get("point_display"),
                        )

                        c_left, c_right = st.columns([2, 1])
                        with c_left:
                            if env_ts.empty:
                                st.warning(
                                    "环境时序数据为空（可能无传感器配置或历史数据查询失败）。"
                                )
                            else:
                                fig_env = go.Figure()
                                for metric in selected_metrics:
                                    if (
                                        metric in env_ts.columns
                                        and env_ts[metric].notna().any()
                                    ):
                                        fig_env.add_trace(
                                            go.Scatter(
                                                x=env_ts["time"],
                                                y=env_ts[metric],
                                                mode="lines",
                                                name=metric,
                                            )
                                        )
                                fig_env.add_vline(
                                    x=event_time,
                                    line_width=2,
                                    line_dash="dash",
                                    line_color="red",
                                )
                                fig_env.update_layout(
                                    title=f"{selected_room} 环境参数趋势（围绕 {event_time.strftime('%Y-%m-%d %H:%M:%S')}）",
                                    xaxis_title="时间",
                                    yaxis_title="数值",
                                    legend_title="指标",
                                )
                                st.plotly_chart(
                                    fig_env,
                                    width="stretch",
                                    config={"scrollZoom": True, "responsive": True},
                                )

                        with c_right:
                            metrics_rows = []
                            for metric in selected_metrics:
                                metrics_rows.append(
                                    compute_impact_metrics(env_ts, metric, event_time)
                                )
                            metrics_df = pd.DataFrame(metrics_rows)
                            st.dataframe(metrics_df, width="stretch")

                    st.subheader("📤 数据导出")
                    ops_export = change_df.sort_values("change_time").copy()
                    ops_export = ops_export[
                        [
                            "change_time",
                            "room_id",
                            "batch_label",
                            "device_type",
                            "device_name",
                            "device_display",
                            "point_name",
                            "point_display",
                            "change_type",
                            "previous_value",
                            "current_value",
                            "delta_value",
                            "abs_delta_value",
                        ]
                    ]
                    csv_bytes = dataframe_to_csv_bytes(ops_export)
                    safe_batch_key = (
                        str(selected_batch_key).replace("|", "_").replace("/", "-")
                    )
                    st.download_button(
                        "导出批次操作记录（CSV）",
                        data=csv_bytes,
                        file_name=f"batch_operations_{safe_batch_key}.csv",
                        mime="text/csv",
                        key="dashboard_tab2_export_ops_csv",
                    )

                    sheets = {
                        "operations": ops_export,
                    }
                    if (
                        isinstance(env_ts_for_export, pd.DataFrame)
                        and not env_ts_for_export.empty
                    ):
                        sheets["env_window"] = env_ts_for_export
                    xlsx_bytes = dataframes_to_excel_bytes(sheets)
                    if xlsx_bytes is None:
                        st.caption(
                            "Excel 导出需要 openpyxl 依赖，当前环境不可用，已提供 CSV 导出。"
                        )
                    else:
                        st.download_button(
                            "导出批次数据（Excel）",
                            data=xlsx_bytes,
                            file_name=f"batch_export_{safe_batch_key}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="dashboard_tab2_export_xlsx",
                        )

                st.subheader("📎 批次内全量操作列表（截断预览）")
                st.warning(f"显示最近 1000 条记录 (共 {len(change_df)} 条)")
                display_df = (
                    change_df.sort_values("change_time", ascending=False)
                    .head(1000)
                    .copy()
                )
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
                            "delta_value",
                            "abs_delta_value",
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
            today = datetime.now().date()
            default_start = today - timedelta(days=30)
            env_date_range = (default_start, today)
            if selected_batch_key:
                windows_df = get_batch_windows(batch_yield_df, selected_batch_key)
                if not windows_df.empty:
                    env_start = windows_df["start_time"].min().date()
                    env_end = windows_df["end_time"].max().date()
                    env_date_range = (env_start, env_end)

            env_df = load_env_stats(selected_rooms, env_date_range)

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


def show():
    from web_app.control_ops_dashboard.analysis import (
        compute_batch_metrics,
        compute_cooccurrence_matrix,
        compute_stability_metrics,
    )
    from web_app.control_ops_dashboard.data import (
        attach_batch_and_growth_day,
        load_batch_windows_from_yield,
        load_device_setpoint_changes,
        load_room_list_from_yield,
    )
    from web_app.control_ops_dashboard.exporting import (
        dataframes_to_excel_bytes,
        dataframe_to_csv_bytes,
    )

    st.markdown(
        """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600;700&display=swap');
html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', sans-serif;
}
[data-testid="stAppViewContainer"] {
    background: radial-gradient(circle at 15% 15%, #f5efe6 0%, #f7f3ea 35%, #f2f6f2 70%, #eef4f4 100%);
}
[data-testid="stHeader"] {background: transparent;}
[data-testid="stSidebar"] {display: none;}
.insight-card {
    background: linear-gradient(135deg, #0b3d2e 0%, #0f5a46 60%, #127a5b 100%);
    color: #f6f2e8;
    padding: 14px 18px;
    border-radius: 14px;
    border: 1px solid rgba(255, 255, 255, 0.1);
    box-shadow: 0 12px 30px rgba(11, 61, 46, 0.25);
}
.insight-card h3 {
    margin: 0 0 6px 0;
    font-size: 18px;
    letter-spacing: 0.3px;
}
.insight-card p {
    margin: 0;
    font-size: 13px;
    opacity: 0.92;
}
.insight-kpi {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
    margin-top: 10px;
}
.insight-kpi div {
    background: rgba(255, 255, 255, 0.08);
    padding: 8px 10px;
    border-radius: 10px;
    font-size: 12px;
}
.insight-kpi strong {
    display: block;
    font-size: 16px;
    font-weight: 700;
}
</style>
""",
        unsafe_allow_html=True,
    )

    st.title("📊 调控操作可视化")

    @st.cache_data(ttl=300)
    def cached_rooms():
        return load_room_list_from_yield()

    @st.cache_data(ttl=300)
    def cached_windows(room_ids: tuple[str, ...]):
        return load_batch_windows_from_yield(list(room_ids))

    @st.cache_data(ttl=60)
    def cached_changes(room_ids: tuple[str, ...], in_dates: tuple[date, ...]):
        return load_device_setpoint_changes(
            list(room_ids), in_dates=list(in_dates) if in_dates else None
        )

    rooms = cached_rooms()
    if not rooms:
        st.warning("暂无可用库房。")
        return

    top = st.container()
    with top:
        c1, c2, c3, c4 = st.columns([2, 2, 4, 3])
        with c1:
            selected_rooms = st.multiselect(
                "库房",
                options=rooms,
                default=rooms[:1],
                key="control_ops_rooms",
            )

        if not selected_rooms:
            st.info("请选择至少一个库房。")
            return

        windows_all = cached_windows(tuple(selected_rooms))
        if windows_all.empty:
            st.info("所选库房暂无批次数据。")
            return

        if "control_ops_show_all" not in st.session_state:
            st.session_state["control_ops_show_all"] = False
        with c2:
            st.session_state["control_ops_show_all"] = st.toggle(
                "显示更多批次",
                value=bool(st.session_state["control_ops_show_all"]),
                key="control_ops_show_all_toggle",
            )

        windows_sorted = windows_all.sort_values(
            ["room_id", "in_date"], ascending=[True, False]
        )
        default_windows = windows_sorted.groupby("room_id").head(3).copy()
        default_batches = default_windows["batch_key"].dropna().unique().tolist()
        pick_windows = (
            windows_all if st.session_state["control_ops_show_all"] else default_windows
        )

        label_map = {}
        for _, row in pick_windows.iterrows():
            k = row["batch_key"]
            label_map[k] = f"{row['room_id']} | {row['in_date']}"
        batch_options = sorted(label_map.keys())
        with c3:
            selected_batches = st.multiselect(
                "批次（可多选对比）",
                options=batch_options,
                default=[b for b in default_batches if b in batch_options],
                format_func=lambda k: label_map.get(k, str(k)),
                key="control_ops_batches",
            )

        if not selected_batches:
            st.info("请选择至少一个批次。")
            return

        selected_windows = windows_all[
            windows_all["batch_key"].isin(selected_batches)
        ].copy()
        if selected_windows.empty:
            st.info("未匹配到批次窗口。")
            return

        with c4:
            st.caption("已显示所选批次的全部生长天数")

    with st.spinner("加载调控记录..."):
        selected_in_dates = tuple(
            sorted(
                pd.to_datetime(selected_windows["in_date"], errors="coerce")
                .dt.date.dropna()
                .unique()
                .tolist()
            )
        )
        raw_changes = cached_changes(
            tuple(sorted(selected_windows["room_id"].unique().tolist())),
            selected_in_dates,
        )
        changes = attach_batch_and_growth_day(raw_changes, selected_windows)

    if not changes.empty:
        static_cfg_df = load_static_config_map(selected_rooms)
        if not static_cfg_df.empty:
            changes = changes.merge(
                static_cfg_df,
                on=["room_id", "device_type", "device_name", "point_name"],
                how="left",
            )
            alias_df = static_cfg_df[
                [
                    "room_id",
                    "device_type",
                    "device_name",
                    "point_alias",
                    "point_remark",
                ]
            ].rename(
                columns={
                    "point_alias": "point_name",
                    "point_remark": "point_remark_alias",
                }
            )
            changes = changes.merge(
                alias_df,
                on=["room_id", "device_type", "device_name", "point_name"],
                how="left",
            )
            alias_type_df = static_cfg_df[
                [
                    "room_id",
                    "device_type",
                    "point_alias",
                    "point_remark",
                ]
            ].rename(
                columns={
                    "point_alias": "point_name",
                    "point_remark": "point_remark_by_type",
                }
            )
            changes = changes.merge(
                alias_type_df,
                on=["room_id", "device_type", "point_name"],
                how="left",
            )
            changes["point_remark"] = changes["point_remark"].fillna(
                changes["point_remark_alias"]
            )
            changes["point_remark"] = changes["point_remark"].fillna(
                changes["point_remark_by_type"]
            )
            changes = changes.drop(columns=["point_remark_alias"], errors="ignore")
            changes = changes.drop(columns=["point_remark_by_type"], errors="ignore")
            if "point_remark" in changes.columns:
                changes["point_group"] = changes["point_remark"].fillna(
                    changes.get("point_group")
                )

    if changes.empty:
        st.warning("当前筛选条件下无调控记录。")
        return

    changes = changes[changes["batch_key"].isin(selected_batches)].copy()
    changes["growth_day_num"] = pd.to_numeric(
        changes.get("growth_day"), errors="coerce"
    )
    changes["delta_value"] = pd.to_numeric(
        changes.get("current_value"), errors="coerce"
    ) - pd.to_numeric(changes.get("previous_value"), errors="coerce")
    changes["abs_magnitude"] = changes["delta_value"].abs()

    with st.popover("筛选面板"):
        device_types = sorted(
            [t for t in changes["device_type"].dropna().unique().tolist() if t]
        )
        selected_device_types = st.multiselect(
            "参数类型（device_type）",
            options=device_types,
            default=device_types,
            key="control_ops_device_types",
        )
        top_groups = (
            changes.groupby("point_group")
            .size()
            .sort_values(ascending=False)
            .head(50)
            .index.tolist()
        )
        selected_point_groups = st.multiselect(
            "参数点位（Top50）",
            options=top_groups,
            default=top_groups,
            key="control_ops_point_groups",
        )

    if selected_device_types:
        changes = changes[changes["device_type"].isin(selected_device_types)].copy()
    if selected_point_groups:
        changes = changes[changes["point_group"].isin(selected_point_groups)].copy()

    if changes.empty:
        st.warning("当前筛选条件下无调控记录。")
        return

    st.subheader("🧭 批次概览")
    metrics_df = compute_batch_metrics(changes)
    st.dataframe(metrics_df, width="stretch")

    st.subheader("⏱️ 调控时间轴（按设备类型分项）")
    for room_id in selected_rooms:
        room_df = changes[changes["room_id"] == room_id].copy()
        if room_df.empty:
            st.info(f"{room_id} 无调控记录。")
            continue

        room_batches = (
            selected_windows[selected_windows["room_id"] == room_id]
            .sort_values("in_date", ascending=False)["batch_key"]
            .tolist()
        )
        latest_key = room_batches[0] if room_batches else None
        unique_batches = sorted(room_df["batch_key"].unique().tolist())
        color_map = {k: "#1f77b4" for k in unique_batches}
        if latest_key in color_map:
            color_map[latest_key] = "#d62728"

        device_types = sorted(
            [t for t in room_df["device_type"].dropna().unique().tolist() if t]
        )
        if not device_types:
            device_types = ["unknown"]
            room_df["device_type"] = room_df["device_type"].fillna("unknown")

        for device_type in device_types:
            type_df = room_df[room_df["device_type"] == device_type].copy()
            if type_df.empty:
                continue

            batch_options = ["全部批次"] + unique_batches
            selected_batch = st.selectbox(
                f"{room_id} | {device_type} 批次选择",
                options=batch_options,
                index=0,
                key=f"control_ops_batch_filter_{room_id}_{device_type}",
            )
            if selected_batch != "全部批次":
                type_df = type_df[type_df["batch_key"] == selected_batch].copy()
            if type_df.empty:
                st.info(f"{room_id} | {device_type} 无可视化数据。")
                continue

            top_points = (
                type_df["point_group"].fillna("unknown").value_counts().head(25).index
            )
            type_df = type_df[type_df["point_group"].isin(top_points)].copy()

            prev_vals = type_df["previous_value"].where(
                type_df["previous_value"].notna(), "N/A"
            )
            curr_vals = type_df["current_value"].where(
                type_df["current_value"].notna(), "N/A"
            )
            type_df["change_label"] = prev_vals.astype(str).str.cat(
                curr_vals.astype(str), sep=" -> "
            )

            fig = px.scatter(
                type_df,
                x="growth_day_num",
                y="point_group",
                color="batch_key",
                facet_row="batch_key" if len(unique_batches) > 1 else None,
                color_discrete_map=color_map,
                text="change_label",
                hover_data=[
                    "change_time",
                    "device_name",
                    "point_name",
                    "previous_value",
                    "current_value",
                ],
                title=f"库房 {room_id} | 设备类型: {device_type} 调控明细",
            )
            fig.update_traces(
                textposition="top center",
                textfont=dict(size=10),
                marker=dict(size=10, opacity=0.8),
                hovertemplate=(
                    "天数 %{x}<br>测点 %{y}<br>时间 %{customdata[0]}"
                    "<br>设备 %{customdata[1]} | 点位 %{customdata[2]}"
                    "<br>前值 %{customdata[3]} -> 后值 %{customdata[4]}<extra></extra>"
                ),
            )
            fig.update_layout(
                height=min(1100, 36 * max(10, type_df["point_group"].nunique())),
                xaxis_title="生长天数",
                yaxis_title="测点类型",
            )
            st.plotly_chart(
                fig, width="stretch", config={"scrollZoom": True, "responsive": True}
            )

        st.markdown("### 🧠 操作方式画像")
        room_batch_options = sorted(room_df["batch_key"].dropna().unique().tolist())
        batch_selection = st.selectbox(
            f"{room_id} | 操作方式画像批次",
            options=room_batch_options,
            key=f"control_ops_profile_batch_{room_id}",
        )
        profile_df = room_df[room_df["batch_key"] == batch_selection].copy()
        profile_left, profile_right = st.columns([2, 3])
        with profile_left:
            total_changes = len(profile_df)
            active_days = profile_df["change_time"].dt.date.nunique()
            unique_devices = profile_df["device_type"].nunique()
            unique_points = profile_df["point_group"].nunique()
            latest_change = profile_df["change_time"].max()
            html = f"""
<div class="insight-card">
  <h3>库房 {room_id} 调控画像</h3>
  <p>批次：{batch_selection}（基于该批次的完整调控记录）</p>
  <div class="insight-kpi">
    <div><strong>{total_changes}</strong>调控次数</div>
    <div><strong>{active_days}</strong>活跃天数</div>
    <div><strong>{unique_devices}</strong>设备类型</div>
    <div><strong>{unique_points}</strong>测点类型</div>
  </div>
  <p style="margin-top:10px;">最近操作：{latest_change.strftime("%Y-%m-%d %H:%M") if pd.notnull(latest_change) else "N/A"}</p>
</div>
"""
            st.markdown(html, unsafe_allow_html=True)

            day_density = (
                profile_df.groupby("growth_day_num")
                .size()
                .reset_index(name="count")
                .sort_values("growth_day_num")
            )
            if not day_density.empty:
                fig_density = px.bar(
                    day_density,
                    x="growth_day_num",
                    y="count",
                    title="生长阶段调控密度",
                    labels={"growth_day_num": "生长天数", "count": "调控次数"},
                )
                st.plotly_chart(
                    fig_density,
                    width="stretch",
                    config={"scrollZoom": True, "responsive": True},
                )

        with profile_right:
            type_counts = (
                profile_df["device_type"].fillna("unknown").value_counts().head(12)
            )
            point_counts = (
                profile_df["point_group"].fillna("unknown").value_counts().head(15)
            )

            type_df = type_counts.reset_index()
            type_df.columns = ["device_type", "count"]
            fig_device = px.bar(
                type_df,
                x="device_type",
                y="count",
                title="设备类型调控频次（Top 12）",
                labels={"device_type": "设备类型", "count": "调控次数"},
            )
            st.plotly_chart(
                fig_device,
                width="stretch",
                config={"scrollZoom": True, "responsive": True},
            )

            point_df = point_counts.reset_index()
            point_df.columns = ["point_group", "count"]
            fig_point = px.bar(
                point_df,
                x="point_group",
                y="count",
                title="测点类型调控频次（Top 15）",
                labels={"point_group": "测点类型", "count": "调控次数"},
            )
            st.plotly_chart(
                fig_point,
                width="stretch",
                config={"scrollZoom": True, "responsive": True},
            )

        st.markdown("### 🧭 关键调整模式")
        st.caption("聚合展示具体设备/测点的调整方向与可能意图（经验提示）。")

        summary_df = profile_df.copy()
        summary_df["direction"] = np.select(
            [summary_df["delta_value"] > 0, summary_df["delta_value"] < 0],
            ["上调", "下调"],
            default="不变",
        )
        intent_text = (
            summary_df["device_type"]
            .fillna("")
            .astype(str)
            .str.cat(summary_df["point_group"].fillna("").astype(str), sep=" ")
            .str.lower()
        )
        summary_df["intent"] = np.select(
            [
                intent_text.str.contains("temp|温|temperature|tem", regex=True),
                intent_text.str.contains("hum|湿|humidity", regex=True),
                intent_text.str.contains("co2|二氧化碳", regex=True),
                intent_text.str.contains("风|fan|fresh|vent|air", regex=True),
                intent_text.str.contains("光|light|illum", regex=True),
                intent_text.str.contains("冷|cool", regex=True),
            ],
            ["控制温度", "调湿", "控制 CO2", "通风换气", "补光管理", "制冷/降温"],
            default="通用调控",
        )
        pattern_df = (
            summary_df.groupby(
                ["device_type", "point_group", "change_type", "direction", "intent"]
            )
            .agg(
                changes=("change_time", "count"),
                avg_delta=("delta_value", "mean"),
            )
            .reset_index()
            .sort_values(["changes", "avg_delta"], ascending=[False, False])
        )
        if not pattern_df.empty:
            pattern_df["avg_delta"] = pattern_df["avg_delta"].round(2)
            st.dataframe(pattern_df.head(40), width="stretch")
        else:
            st.info("暂无可用于总结的调整模式。")

        st.subheader(f"📈 {room_id} 参数设定点趋势（批次对比）")
        top_points = (
            room_df.groupby("point_group")
            .size()
            .sort_values(ascending=False)
            .head(30)
            .index.tolist()
        )
        selected_point = st.selectbox(
            "选择参数点位",
            options=top_points,
            key=f"control_ops_point_{room_id}",
        )
        line_df = (
            room_df[room_df["point_group"] == selected_point]
            .copy()
            .sort_values(["growth_day_num", "change_time"])
        )
        fig_line = go.Figure()
        for b in unique_batches:
            bdf = line_df[line_df["batch_key"] == b].copy()
            if bdf.empty:
                continue
            fig_line.add_trace(
                go.Scatter(
                    x=bdf["growth_day_num"],
                    y=bdf["current_value"],
                    mode="lines+markers",
                    line_shape="hv",
                    name=b,
                    hovertemplate="天数 %{x}<br>设定值 %{y}<extra></extra>",
                )
            )
        fig_line.update_layout(
            title=f"{room_id} | {selected_point}",
            xaxis_title="生长天数",
            yaxis_title="设定值",
            legend_title="批次",
        )
        st.plotly_chart(
            fig_line, width="stretch", config={"scrollZoom": True, "responsive": True}
        )

        with st.expander(f"🔍 {room_id} 调控效果评估（真实环境参数）", expanded=False):
            ops_df = (
                room_df.sort_values("change_time", ascending=False)
                .head(500)
                .reset_index(drop=True)
            )
            op_times = pd.to_datetime(
                ops_df["change_time"], errors="coerce"
            ).dt.strftime("%Y-%m-%d %H:%M:%S")
            growth_days = (
                pd.to_numeric(ops_df.get("growth_day"), errors="coerce")
                .fillna(0)
                .astype(int)
            )
            point_groups = (
                ops_df["point_group"]
                if "point_group" in ops_df.columns
                else pd.Series("", index=ops_df.index)
            )
            ops_df["op_label"] = (
                op_times.fillna("N/A").astype(str)
                + " | D"
                + growth_days.astype(str)
                + " | "
                + point_groups.fillna("").astype(str)
            )
            selected_op_idx = st.selectbox(
                "选择一条调控记录",
                options=list(range(len(ops_df))),
                format_func=lambda i: ops_df.loc[i, "op_label"],
                key=f"control_ops_eval_op_{room_id}",
            )
            op_row = ops_df.loc[selected_op_idx]
            event_time = pd.to_datetime(op_row["change_time"]).to_pydatetime()
            window_minutes = st.selectbox(
                "环境曲线窗口（分钟）",
                options=[30, 60, 180],
                index=1,
                key=f"control_ops_eval_window_{room_id}",
            )
            ts_start = event_time - timedelta(minutes=int(window_minutes))
            ts_end = event_time + timedelta(minutes=int(window_minutes))

            env_ts = load_env_timeseries_window(room_id, ts_start, ts_end)
            metrics = infer_env_metrics(
                op_row.get("device_type"),
                op_row.get("point_group"),
            )

            c_left, c_right = st.columns([2, 1])
            with c_left:
                if env_ts is None or env_ts.empty:
                    st.warning("物联环境数据为空或查询失败。")
                else:
                    fig_env = go.Figure()
                    for m in metrics:
                        if m in env_ts.columns and env_ts[m].notna().any():
                            fig_env.add_trace(
                                go.Scatter(
                                    x=env_ts["time"], y=env_ts[m], mode="lines", name=m
                                )
                            )
                    fig_env.add_vline(
                        x=event_time, line_width=2, line_dash="dash", line_color="red"
                    )
                    fig_env.update_layout(
                        title="真实环境参数趋势",
                        xaxis_title="时间",
                        yaxis_title="数值",
                    )
                    st.plotly_chart(
                        fig_env,
                        width="stretch",
                        config={"scrollZoom": True, "responsive": True},
                    )

            with c_right:
                rows = [compute_impact_metrics(env_ts, m, event_time) for m in metrics]
                st.dataframe(pd.DataFrame(rows), width="stretch")

    st.subheader("🔗 参数共现分析")
    co = compute_cooccurrence_matrix(changes, window_minutes=30)
    if not co.empty:
        top_keys = (
            changes.groupby("point_group")
            .size()
            .sort_values(ascending=False)
            .head(20)
            .index.tolist()
        )
        co_view = co.loc[top_keys, top_keys]
        fig_co = px.imshow(co_view, aspect="auto", title="参数共现次数（30分钟窗口）")
        st.plotly_chart(
            fig_co, width="stretch", config={"scrollZoom": True, "responsive": True}
        )
    else:
        st.info("暂无可用共现数据。")

    st.subheader("✅ 调控后稳定性（再调控率）")
    stab = compute_stability_metrics(changes, post_minutes=30)
    st.dataframe(stab.head(200), width="stretch")

    st.subheader("📤 导出报表")
    export_df = changes[
        [
            "room_id",
            "batch_key",
            "in_date",
            "growth_day",
            "change_time",
            "device_type",
            "device_name",
            "point_name",
            "point_group",
            "previous_value",
            "current_value",
            "change_type",
            "delta_value",
            "abs_magnitude",
        ]
    ].sort_values(["room_id", "batch_key", "change_time"])

    st.download_button(
        "导出调控数据（CSV）",
        data=dataframe_to_csv_bytes(export_df),
        file_name="control_ops_export.csv",
        mime="text/csv",
        key="control_ops_export_csv",
    )
    st.download_button(
        "导出调控数据（Excel）",
        data=dataframes_to_excel_bytes(
            {
                "operations": export_df,
                "batch_metrics": metrics_df,
                "stability": stab,
            }
        ),
        file_name="control_ops_export.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="control_ops_export_xlsx",
    )


# For compatibility if run directly
if __name__ == "__main__":
    show()

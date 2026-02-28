from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy.orm import sessionmaker

from global_const.global_const import pgsql_engine
from utils.create_table import (
    ControlStrategyKnowledgeBaseRun,
    DecisionAnalysisStaticConfig,
    create_tables,
    store_control_strategy_knowledge_base,
)
from web_app.control_ops_dashboard.data import (
    load_device_setpoint_changes,
    load_room_list_from_yield,
)


Session = sessionmaker(bind=pgsql_engine)


def safe_mode(series: pd.Series) -> str | None:
    if series is None or series.empty:
        return None
    valid = series.dropna().astype(str)
    if valid.empty:
        return None
    mode_vals = valid.mode()
    if mode_vals.empty:
        return None
    return str(mode_vals.iloc[0])


def to_json_safe(v):
    if v is None:
        return None
    if isinstance(v, (np.floating, float)) and pd.isna(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v


def load_static_point_map(room_ids: list[str] | None) -> pd.DataFrame:
    with Session() as session:
        query = session.query(
            DecisionAnalysisStaticConfig.room_id,
            DecisionAnalysisStaticConfig.device_type,
            DecisionAnalysisStaticConfig.device_name,
            DecisionAnalysisStaticConfig.point_name,
            DecisionAnalysisStaticConfig.device_alias,
            DecisionAnalysisStaticConfig.point_alias,
            DecisionAnalysisStaticConfig.remark,
        ).filter(DecisionAnalysisStaticConfig.is_active.is_(True))

        if room_ids:
            query = query.filter(DecisionAnalysisStaticConfig.room_id.in_(room_ids))

        df = pd.read_sql(query.statement, session.bind)

    if df.empty:
        return df
    return df.rename(columns={"remark": "point_remark"})


def normalize_changes(raw_changes: pd.DataFrame, room_ids: list[str]) -> pd.DataFrame:
    if raw_changes is None or raw_changes.empty:
        return pd.DataFrame()

    df = raw_changes.copy()
    df["change_time"] = pd.to_datetime(df["change_time"], errors="coerce")
    df["in_date"] = pd.to_datetime(df.get("in_date"), errors="coerce").dt.date
    df = df.dropna(
        subset=["change_time", "room_id", "device_type", "point_name", "in_date"]
    )

    df["batch_key"] = df["room_id"].astype(str) + "|" + df["in_date"].astype(str)

    growth_day_raw = pd.to_numeric(df.get("growth_day"), errors="coerce")
    growth_day_infer = (
        pd.to_datetime(df["change_time"]).dt.normalize()
        - pd.to_datetime(df["in_date"]).dt.normalize()
    ).dt.days + 1
    df["growth_day_num"] = growth_day_raw.fillna(growth_day_infer)
    df = df[df["growth_day_num"].notna()].copy()
    df["growth_day_num"] = df["growth_day_num"].astype(int)

    static_df = load_static_point_map(room_ids)
    if not static_df.empty:
        df = df.merge(
            static_df,
            on=["room_id", "device_type", "device_name", "point_name"],
            how="left",
        )

    if "point_alias" in df.columns:
        df["point_key"] = df["point_alias"].fillna(df["point_name"]).astype(str)
    else:
        df["point_key"] = df["point_name"].astype(str)

    if "point_remark" in df.columns:
        df["point_display"] = df["point_remark"].fillna(df["point_name"]).astype(str)
    else:
        df["point_display"] = df["point_name"].astype(str)

    df["current_value_num"] = pd.to_numeric(df.get("current_value"), errors="coerce")
    df["previous_value_num"] = pd.to_numeric(df.get("previous_value"), errors="coerce")
    df["delta_value"] = df["current_value_num"] - df["previous_value_num"]
    df["change_hour"] = df["change_time"].dt.hour
    return df


def build_daily_features(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    ordered = df.sort_values("change_time").copy()
    keys = [
        "room_id",
        "batch_key",
        "in_date",
        "device_type",
        "point_key",
        "point_display",
        "growth_day_num",
    ]

    daily = (
        ordered.groupby(keys, as_index=False)
        .agg(
            daily_changes=("id", "count"),
            current_value_last=("current_value_num", "last"),
            current_value_median=("current_value_num", "median"),
            current_value_std=("current_value_num", "std"),
            delta_sum=("delta_value", "sum"),
            delta_median=("delta_value", "median"),
            hour_median=("change_hour", "median"),
            change_type_mode=("change_type", safe_mode),
        )
        .sort_values(
            ["device_type", "point_key", "room_id", "batch_key", "growth_day_num"]
        )
    )

    daily["current_value_std"] = daily["current_value_std"].fillna(0.0)
    daily["delta_sum"] = daily["delta_sum"].fillna(0.0)
    daily["delta_median"] = daily["delta_median"].fillna(0.0)
    daily["hour_median"] = daily["hour_median"].fillna(0.0)

    daily["prev_day_value"] = daily.groupby(
        ["room_id", "batch_key", "device_type", "point_key"]
    )["current_value_last"].shift(1)
    daily["day_to_day_delta"] = daily["current_value_last"] - daily["prev_day_value"]
    daily["day_to_day_delta"] = daily["day_to_day_delta"].fillna(0.0)
    return daily


def choose_kmeans_labels(
    point_df: pd.DataFrame,
    feature_cols: list[str],
    min_k: int = 2,
    max_k: int = 6,
    random_state: int = 42,
) -> tuple[np.ndarray, int, float | None, str]:
    n = len(point_df)
    if n < 10:
        return np.zeros(n, dtype=int), 1, None, "single_cluster_fallback"

    growth_unique = point_df["growth_day_num"].nunique()
    if growth_unique < 3:
        return np.zeros(n, dtype=int), 1, None, "single_cluster_fallback"

    x = point_df[feature_cols].copy()
    for col in feature_cols:
        x[col] = pd.to_numeric(x[col], errors="coerce")
        x[col] = x[col].fillna(x[col].median())

    x_mat = x.to_numpy(dtype=float)
    x_std = StandardScaler().fit_transform(x_mat)

    k_upper = min(max_k, max(min_k, n // 12))
    if k_upper < min_k:
        return np.zeros(n, dtype=int), 1, None, "single_cluster_fallback"

    best_score = -1.0
    best_labels = np.zeros(n, dtype=int)
    best_k = 1

    for k in range(min_k, k_upper + 1):
        model = KMeans(n_clusters=k, n_init=20, random_state=random_state)
        labels = model.fit_predict(x_std)
        if len(np.unique(labels)) < 2:
            continue
        score = silhouette_score(x_std, labels)
        if score > best_score:
            best_score = float(score)
            best_labels = labels
            best_k = k

    if best_k == 1:
        return np.zeros(n, dtype=int), 1, None, "single_cluster_fallback"
    return best_labels, best_k, best_score, "kmeans"


def build_cluster_profiles(
    daily_df: pd.DataFrame,
    min_samples_per_point: int = 12,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if daily_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    feature_cols = [
        "growth_day_num",
        "current_value_last",
        "daily_changes",
        "day_to_day_delta",
        "hour_median",
    ]

    frames: list[pd.DataFrame] = []
    meta_rows: list[dict] = []

    for (device_type, point_key), point_df in daily_df.groupby(
        ["device_type", "point_key"]
    ):
        point_df = point_df.copy().sort_values(
            ["growth_day_num", "room_id", "batch_key"]
        )

        if len(point_df) < min_samples_per_point:
            labels = np.zeros(len(point_df), dtype=int)
            k = 1
            score = None
            method = "single_cluster_fallback"
        else:
            labels, k, score, method = choose_kmeans_labels(point_df, feature_cols)

        point_df["cluster_raw"] = labels
        center = (
            point_df.groupby("cluster_raw", as_index=False)["growth_day_num"]
            .median()
            .sort_values("growth_day_num")
            .reset_index(drop=True)
        )
        center["cluster_id"] = np.arange(len(center))
        id_map = dict(zip(center["cluster_raw"], center["cluster_id"]))

        point_df["cluster_id"] = point_df["cluster_raw"].map(id_map).astype(int)
        point_df["cluster_name"] = "cluster_" + (point_df["cluster_id"] + 1).astype(str)
        point_df["cluster_raw_count"] = int(k)
        point_df["cluster_score"] = score
        point_df = point_df.drop(columns=["cluster_raw"], errors="ignore")
        frames.append(point_df)

        meta_rows.append(
            {
                "device_type": device_type,
                "point_key": point_key,
                "cluster_count": int(k),
                "silhouette_score": to_json_safe(score),
                "sample_count": int(len(point_df)),
                "cluster_method": method,
                "feature_columns": feature_cols,
            }
        )

    clustered = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    meta = pd.DataFrame(meta_rows)
    return clustered, meta


def _trend_label(x: pd.Series, y: pd.Series) -> str:
    valid = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(valid) < 3:
        return "insufficient"
    xs = valid["x"].to_numpy(dtype=float)
    ys = valid["y"].to_numpy(dtype=float)
    if np.allclose(ys.std(), 0.0):
        return "stable"
    slope = np.polyfit(xs, ys, 1)[0]
    if slope > 0.05:
        return "increasing"
    if slope < -0.05:
        return "decreasing"
    return "stable"


def build_cluster_summary(cluster_df: pd.DataFrame) -> pd.DataFrame:
    if cluster_df.empty:
        return pd.DataFrame()

    grp = ["device_type", "point_key", "cluster_id", "cluster_name"]
    summary = (
        cluster_df.groupby(grp, as_index=False)
        .agg(
            point_display=("point_display", safe_mode),
            sample_days=("growth_day_num", "count"),
            active_rooms=("room_id", "nunique"),
            active_batches=("batch_key", "nunique"),
            growth_day_min=("growth_day_num", "min"),
            growth_day_max=("growth_day_num", "max"),
            growth_day_median=("growth_day_num", "median"),
            daily_changes_median=("daily_changes", "median"),
            value_median=("current_value_last", "median"),
            value_p25=("current_value_last", lambda s: s.quantile(0.25)),
            value_p75=("current_value_last", lambda s: s.quantile(0.75)),
            value_min=("current_value_last", "min"),
            value_max=("current_value_last", "max"),
            day_delta_median=("day_to_day_delta", "median"),
            preferred_change_type=("change_type_mode", safe_mode),
            preferred_hour=("hour_median", "median"),
        )
        .sort_values(["device_type", "point_key", "cluster_id"])
    )

    trend_df = (
        cluster_df.groupby(grp)
        .apply(lambda g: _trend_label(g["growth_day_num"], g["current_value_last"]))
        .reset_index(name="value_trend")
    )

    summary = summary.merge(trend_df, on=grp, how="left")
    return summary


def to_nested_knowledge(
    raw_df: pd.DataFrame,
    cluster_summary_df: pd.DataFrame,
    cluster_meta_df: pd.DataFrame,
) -> dict:
    if raw_df.empty or cluster_summary_df.empty:
        return {
            "generated_at": datetime.now().isoformat(),
            "description": "鹿茸菇设备调控知识库（聚类版）",
            "cluster_method": "kmeans",
            "devices": {},
        }

    devices_payload: dict[str, dict] = {}
    for device_type, dev_df in cluster_summary_df.groupby("device_type"):
        points_payload: dict[str, dict] = {}
        for point_key, point_df in dev_df.groupby("point_key"):
            point_df = point_df.sort_values("cluster_id").copy()
            point_display = safe_mode(point_df["point_display"]) or point_key

            meta_row = cluster_meta_df[
                (cluster_meta_df["device_type"] == device_type)
                & (cluster_meta_df["point_key"] == point_key)
            ]
            if meta_row.empty:
                cluster_meta = {
                    "cluster_count": int(point_df["cluster_id"].nunique()),
                    "sample_count": int(point_df["sample_days"].sum()),
                    "cluster_method": "unknown",
                    "feature_columns": [],
                    "silhouette_score": None,
                }
            else:
                cluster_meta = {
                    k: to_json_safe(v) for k, v in meta_row.iloc[0].to_dict().items()
                }

            clusters = []
            for _, row in point_df.iterrows():
                item = {k: to_json_safe(v) for k, v in row.to_dict().items()}
                item["growth_window"] = (
                    f"D{int(row['growth_day_min'])}-D{int(row['growth_day_max'])}"
                )
                clusters.append(item)

            points_payload[str(point_key)] = {
                "point_display": point_display,
                "cluster_meta": cluster_meta,
                "time_clusters": clusters,
            }

        dev_raw = raw_df[raw_df["device_type"] == device_type]
        devices_payload[str(device_type)] = {
            "control_count": int(len(dev_raw)),
            "active_rooms": int(dev_raw["room_id"].nunique()),
            "active_batches": int(dev_raw["batch_key"].nunique()),
            "points": points_payload,
        }

    return {
        "generated_at": datetime.now().isoformat(),
        "description": "鹿茸菇设备调控知识库（基于全库房全批次日粒度聚类）",
        "cluster_method": "kmeans",
        "pipeline": {
            "source_table": "device_setpoint_changes",
            "data_scope": "all_rooms_all_batches",
            "analysis_grain": "room_batch_growth_day",
            "features": [
                "growth_day_num",
                "current_value_last",
                "daily_changes",
                "day_to_day_delta",
                "hour_median",
            ],
        },
        "scope": {
            "rooms": sorted(raw_df["room_id"].dropna().astype(str).unique().tolist()),
            "batch_count": int(raw_df["batch_key"].nunique()),
            "record_count": int(len(raw_df)),
            "growth_day_range": [
                int(raw_df["growth_day_num"].min()),
                int(raw_df["growth_day_num"].max()),
            ],
            "date_range": {
                "start": raw_df["change_time"].min().strftime("%Y-%m-%d %H:%M:%S"),
                "end": raw_df["change_time"].max().strftime("%Y-%m-%d %H:%M:%S"),
            },
        },
        "devices": devices_payload,
    }


def build_knowledge_base(
    room_ids: list[str] | None,
    min_samples_per_point: int,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    rooms = room_ids or load_room_list_from_yield()
    if not rooms:
        empty = pd.DataFrame()
        return to_nested_knowledge(empty, empty, empty), empty, empty

    raw_changes = load_device_setpoint_changes(rooms, in_dates=None)
    normalized = normalize_changes(raw_changes, rooms)
    if normalized.empty:
        empty = pd.DataFrame()
        return to_nested_knowledge(empty, empty, empty), empty, empty

    daily_df = build_daily_features(normalized)
    clustered_df, cluster_meta_df = build_cluster_profiles(
        daily_df,
        min_samples_per_point=min_samples_per_point,
    )
    cluster_summary_df = build_cluster_summary(clustered_df)
    payload = to_nested_knowledge(normalized, cluster_summary_df, cluster_meta_df)
    return payload, cluster_summary_df, clustered_df


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="基于全库房全批次设备调控数据，构建聚类版控制知识库。"
    )
    parser.add_argument(
        "--rooms",
        nargs="*",
        default=None,
        help="仅处理指定库房，例如: --rooms 607 611",
    )
    parser.add_argument(
        "--min-samples-per-point",
        type=int,
        default=12,
        help="单测点最小样本数，低于该值时不聚类，使用单簇回退",
    )
    parser.add_argument(
        "--interval-days",
        type=int,
        default=27,
        help="执行间隔天数（默认27天）；若距离上次落库未超过该值则跳过",
    )
    parser.add_argument(
        "--force-run",
        action="store_true",
        help="强制执行，忽略间隔天数限制",
    )
    parser.add_argument(
        "--skip-persist",
        action="store_true",
        help="仅计算不落库（默认会落库）",
    )
    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="额外导出 cluster_summary/cluster_records 两份 CSV 以便人工校验",
    )
    parser.add_argument(
        "--export-prefix",
        default="output/control_strategy_cluster_kb",
        help="导出CSV前缀路径（仅在 --export-csv 时生效）",
    )
    return parser.parse_args()


def get_latest_cluster_kb_generated_at() -> datetime | None:
    with Session() as session:
        latest_active = (
            session.query(ControlStrategyKnowledgeBaseRun.generated_at)
            .filter(
                ControlStrategyKnowledgeBaseRun.kb_type == "cluster",
                ControlStrategyKnowledgeBaseRun.is_active.is_(True),
            )
            .order_by(ControlStrategyKnowledgeBaseRun.generated_at.desc())
            .first()
        )
        if latest_active and latest_active[0]:
            return latest_active[0]

        latest_any = (
            session.query(ControlStrategyKnowledgeBaseRun.generated_at)
            .filter(ControlStrategyKnowledgeBaseRun.kb_type == "cluster")
            .order_by(ControlStrategyKnowledgeBaseRun.generated_at.desc())
            .first()
        )
        return latest_any[0] if latest_any and latest_any[0] else None


def build_and_persist_cluster_kb(
    room_ids: list[str] | None = None,
    min_samples_per_point: int = 12,
    interval_days: int = 27,
    force_run: bool = False,
    persist: bool = True,
    export_csv: bool = False,
    export_prefix: str = "output/control_strategy_cluster_kb",
) -> dict:
    """构建聚类知识库并按需落库。

    Returns:
        包含是否执行、是否跳过、run_id、统计信息的结果字典。
    """
    if not force_run and int(interval_days) > 0:
        latest_generated_at = get_latest_cluster_kb_generated_at()
        if latest_generated_at is not None:
            next_due_at = latest_generated_at + timedelta(days=int(interval_days))
            if datetime.now() < next_due_at:
                return {
                    "executed": False,
                    "skipped": True,
                    "reason": "interval_not_reached",
                    "interval_days": int(interval_days),
                    "last_generated_at": latest_generated_at.isoformat(),
                    "next_due_at": next_due_at.isoformat(),
                }

    payload, cluster_summary_df, cluster_records_df = build_knowledge_base(
        room_ids,
        min_samples_per_point=int(min_samples_per_point),
    )

    persist_stats = None
    if persist:
        create_tables()
        persist_stats = store_control_strategy_knowledge_base(
            kb_payload=payload,
            kb_type="cluster",
            source_file=None,
            mark_previous_inactive=True,
        )

    summary_path = None
    records_path = None
    if export_csv:
        export_prefix_path = Path(export_prefix)
        export_prefix_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path = export_prefix_path.with_name(
            export_prefix_path.name + "_cluster_summary.csv"
        )
        records_path = export_prefix_path.with_name(
            export_prefix_path.name + "_cluster_records.csv"
        )
        cluster_summary_df.to_csv(summary_path, index=False, encoding="utf-8-sig")
        cluster_records_df.to_csv(records_path, index=False, encoding="utf-8-sig")

    result = {
        "executed": True,
        "skipped": False,
        "interval_days": int(interval_days),
        "room_count": len(payload.get("scope", {}).get("rooms", [])),
        "scope": payload.get("scope", {}),
        "persisted": bool(persist),
        "persist_stats": persist_stats,
        "export_csv": bool(export_csv),
        "summary_csv": str(summary_path) if summary_path else None,
        "records_csv": str(records_path) if records_path else None,
    }
    return result


def main() -> None:
    args = parse_args()
    result = build_and_persist_cluster_kb(
        room_ids=args.rooms,
        min_samples_per_point=int(args.min_samples_per_point),
        interval_days=int(args.interval_days),
        force_run=bool(args.force_run),
        persist=not bool(args.skip_persist),
        export_csv=bool(args.export_csv),
        export_prefix=args.export_prefix,
    )

    if result.get("skipped"):
        print(
            "跳过执行：距离上次聚类知识库落库未满 "
            f"{result.get('interval_days', 27)} 天。"
        )
        print(f"上次生成时间: {result.get('last_generated_at')}")
        print(f"下次可执行时间: {result.get('next_due_at')}")
        return

    print("知识库已计算完成。")
    print(f"覆盖库房数: {result.get('room_count', 0)}")
    persist_stats = result.get("persist_stats") or {}
    if result.get("persisted") and persist_stats:
        print(
            "已落库: "
            f"run_id={persist_stats.get('run_id')} "
            f"cluster_meta={persist_stats.get('cluster_meta_count')} "
            f"cluster_rules={persist_stats.get('cluster_rule_count')}"
        )
    elif not result.get("persisted"):
        print("未落库：已按 --skip-persist 跳过数据库写入。")

    if result.get("export_csv"):
        print("已导出校验文件:")
        print(f"- {result.get('summary_csv')}")
        print(f"- {result.get('records_csv')}")


if __name__ == "__main__":
    main()

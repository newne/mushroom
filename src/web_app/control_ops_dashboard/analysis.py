from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd


def compute_growth_day(in_date, change_time) -> int | None:
    try:
        d0 = pd.to_datetime(in_date).date()
        t = pd.to_datetime(change_time).date()
        return (t - d0).days + 1
    except Exception:
        return None


def compute_batch_metrics(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(
            columns=[
                "room_id",
                "batch_key",
                "changes",
                "unique_points",
                "avg_magnitude",
            ]
        )

    tmp = df.copy()
    tmp["abs_magnitude"] = (
        pd.to_numeric(tmp.get("current_value"), errors="coerce")
        - pd.to_numeric(tmp.get("previous_value"), errors="coerce")
    ).abs()
    agg = (
        tmp.groupby(["room_id", "batch_key"])
        .agg(
            changes=("change_time", "count"),
            unique_points=("point_group", "nunique"),
            avg_magnitude=("abs_magnitude", "mean"),
        )
        .reset_index()
        .sort_values(["room_id", "changes"], ascending=[True, False])
    )
    return agg


def compute_cooccurrence_matrix(
    df: pd.DataFrame,
    window_minutes: int = 30,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    tmp = df.dropna(
        subset=["room_id", "batch_key", "change_time", "point_group"]
    ).copy()
    tmp["change_time"] = pd.to_datetime(tmp["change_time"], errors="coerce")
    tmp = tmp.dropna(subset=["change_time"])

    bucket = (
        tmp["change_time"].astype("int64") // (window_minutes * 60 * 1_000_000_000)
    ).astype("int64")
    tmp["bucket"] = bucket
    grouped = (
        tmp.groupby(["room_id", "batch_key", "bucket"])["point_group"]
        .apply(lambda s: sorted(set(s)))
        .tolist()
    )

    all_points = sorted(set(tmp["point_group"].astype(str).tolist()))
    index = {p: i for i, p in enumerate(all_points)}
    mat = np.zeros((len(all_points), len(all_points)), dtype=int)
    for points in grouped:
        for i, a in enumerate(points):
            ia = index[a]
            mat[ia, ia] += 1
            for b in points[i + 1 :]:
                ib = index[b]
                mat[ia, ib] += 1
                mat[ib, ia] += 1

    return pd.DataFrame(mat, index=all_points, columns=all_points)


def compute_stability_metrics(
    df: pd.DataFrame,
    post_minutes: int = 30,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(
            columns=["room_id", "batch_key", "point_group", "changes", "rechange_rate"]
        )

    tmp = df.dropna(
        subset=["room_id", "batch_key", "change_time", "point_group"]
    ).copy()
    tmp["change_time"] = pd.to_datetime(tmp["change_time"], errors="coerce")
    tmp = tmp.dropna(subset=["change_time"])
    tmp = tmp.sort_values(["room_id", "batch_key", "point_group", "change_time"])

    tmp["next_time"] = tmp.groupby(["room_id", "batch_key", "point_group"])[
        "change_time"
    ].shift(-1)
    tmp["delta_min"] = (tmp["next_time"] - tmp["change_time"]).dt.total_seconds() / 60.0
    tmp["rechange"] = tmp["delta_min"].notna() & (
        tmp["delta_min"] <= float(post_minutes)
    )

    agg = (
        tmp.groupby(["room_id", "batch_key", "point_group"])
        .agg(changes=("change_time", "count"), rechange_rate=("rechange", "mean"))
        .reset_index()
        .sort_values(
            ["room_id", "batch_key", "changes"], ascending=[True, False, False]
        )
    )
    return agg

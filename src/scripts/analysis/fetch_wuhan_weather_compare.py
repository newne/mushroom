#!/usr/bin/env python3
"""Fetch Wuhan daily weather for Jan 2025 and Jan 2026 and plot max/min/avg."""

from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd
import plotly.graph_objects as go
import requests
from plotly.subplots import make_subplots


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float
    longitude: float
    timezone: str


WUHAN = Location(
    name="Wuhan", latitude=30.5928, longitude=114.3055, timezone="Asia/Shanghai"
)


def fetch_daily_weather(
    location: Location,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    base_url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": location.latitude,
        "longitude": location.longitude,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,temperature_2m_mean",
        "timezone": location.timezone,
    }
    response = requests.get(base_url, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    daily = payload.get("daily")
    if not daily:
        raise ValueError("Open-Meteo response missing daily data")

    dates = pd.to_datetime(daily["time"]).date
    df = pd.DataFrame(
        {
            "date": dates,
            "temp_max": daily["temperature_2m_max"],
            "temp_min": daily["temperature_2m_min"],
            "temp_mean": daily["temperature_2m_mean"],
        }
    )
    return df


def build_month_df(location: Location, year: int, month: int) -> pd.DataFrame:
    start = date(year, month, 1)
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)

    df = fetch_daily_weather(location, start, end)
    df["year"] = year
    df["month"] = month
    df["day"] = pd.to_datetime(df["date"]).dt.day
    return df


def plot_compare(df: pd.DataFrame, output_html: Path) -> None:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.75, 0.25],
        vertical_spacing=0.08,
        subplot_titles=(
            "Wuhan Daily Temperature (Jan 2025 vs Jan 2026)",
            "Daily Mean Difference (2026 - 2025)",
        ),
    )

    year_styles = {
        2025: {
            "color": "#1f77b4",
            "fill": "rgba(31,119,180,0.18)",
            "line": "rgba(31,119,180,0.45)",
        },
        2026: {
            "color": "#d62728",
            "fill": "rgba(214,39,40,0.18)",
            "line": "rgba(214,39,40,0.45)",
        },
    }

    for year in sorted(df["year"].unique()):
        df_year = df[df["year"] == year].sort_values("day")
        styles = year_styles[year]

        fig.add_trace(
            go.Scatter(
                x=df_year["day"],
                y=df_year["temp_min"],
                mode="lines",
                line={"color": "rgba(0,0,0,0)", "width": 0},
                name=f"{year} Range",
                showlegend=False,
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df_year["day"],
                y=df_year["temp_max"],
                mode="lines",
                line={"color": styles["line"], "width": 1},
                fill="tonexty",
                fillcolor=styles["fill"],
                name=f"{year} Range",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=df_year["day"],
                y=df_year["temp_mean"],
                mode="lines+markers",
                name=f"{year} Mean",
                line={"color": styles["color"], "width": 3},
                marker={"size": 5},
            ),
            row=1,
            col=1,
        )

    df_2025 = df[df["year"] == 2025][["day", "temp_mean"]].rename(
        columns={"temp_mean": "temp_mean_2025"}
    )
    df_2026 = df[df["year"] == 2026][["day", "temp_mean"]].rename(
        columns={"temp_mean": "temp_mean_2026"}
    )
    delta = df_2026.merge(df_2025, on="day", how="inner")
    delta["diff"] = delta["temp_mean_2026"] - delta["temp_mean_2025"]
    delta_colors = [
        year_styles[2026]["color"] if value >= 0 else year_styles[2025]["color"]
        for value in delta["diff"]
    ]

    fig.add_trace(
        go.Bar(
            x=delta["day"],
            y=delta["diff"],
            name="Mean Δ",
            marker={"color": delta_colors},
            opacity=0.75,
            hovertemplate="Day %{x}<br>Mean Δ %{y:.2f}°C<extra></extra>",
        ),
        row=2,
        col=1,
    )

    fig.update_layout(
        legend_title="Series",
        template="plotly_white",
        hovermode="x unified",
    )
    fig.update_xaxes(tickmode="linear", tick0=1, dtick=1, range=[0.5, 31.5])
    fig.update_xaxes(title_text="Day of Month", row=2, col=1)
    fig.update_yaxes(title_text="Temperature (°C)", row=1, col=1)
    fig.update_yaxes(title_text="Mean Δ (°C)", row=2, col=1)
    fig.write_html(output_html, include_plotlyjs="cdn")


def parse_args(args: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Wuhan daily weather for Jan 2025/2026 and plot comparison.",
    )
    parser.add_argument(
        "--output-dir", default="output", help="Directory to save results"
    )
    return parser.parse_args(args)


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    df_2025 = build_month_df(WUHAN, 2025, 1)
    df_2026 = build_month_df(WUHAN, 2026, 1)
    df = pd.concat([df_2025, df_2026], ignore_index=True)

    csv_path = output_dir / "wuhan_daily_temp_2025_01_2026_01.csv"
    html_path = output_dir / "wuhan_daily_temp_2025_01_2026_01.html"

    df.to_csv(csv_path, index=False)
    plot_compare(df, html_path)

    print(f"Saved data to: {csv_path}")
    print(f"Saved chart to: {html_path}")


if __name__ == "__main__":
    main()

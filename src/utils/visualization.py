from environment.processor import create_env_data_processor
from typing import List, Optional, Dict, Any
from datetime import datetime
import warnings
from typing import List, Dict, Optional

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from utils.data_preprocessing import query_data_by_batch_time


def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    if not pd.api.types.is_datetime64_any_dtype(df.index):
        try:
            df.index = pd.to_datetime(df.index)
        except Exception:
            raise ValueError("Could not convert DataFrame index to datetime")
    return df


def plot_room_daily_environment(df1: pd.DataFrame, room: str, *, show: bool = True) -> go.Figure:
    """
    Produce the 4-row daily environment figure for a single room.

    Parameters
    - df1: pivoted DataFrame (index=time, columns possibly MultiIndex)
    - room: room id string, e.g. '611'
    - show: whether to call `fig.show()` before returning

    Returns: plotly Figure
    """
    df1 = df1.copy()
    _ensure_datetime_index(df1)

    # Build expected top-level keys
    mushroom_pref = f"mushroom_info_{room}"
    env_pref = f"env_status_{room}"
    light_pref = f"grow_light_{room}"

    # Column tuples used in original notebook
    in_year_col = (mushroom_pref, "in_year")
    in_month_col = (mushroom_pref, "in_month")
    in_day_col = (mushroom_pref, "in_day")
    batch_date_col = (mushroom_pref, "batch_date")
    in_day_num_col = (mushroom_pref, "in_day_num")

    target_columns = {
        "Temperature": (env_pref, "temperature"),
        "Humidity": (env_pref, "humidity"),
        "CO2": (env_pref, "co2"),
    }

    cols_light_on = (light_pref, "on_mset")
    cols_light_off = (light_pref, "off_mset")

    # Validate presence of at least one of the expected column groups
    cols_present = set(df1.columns)
    expected = {in_year_col, in_month_col, in_day_col}
    if not expected & cols_present:
        warnings.warn(
            f"Mushroom meta columns for room {room} not found; plotting may be incomplete.")

    # Create batch_date if possible (works with MultiIndex columns)
    try:
        years = df1[in_year_col]
        months = df1[in_month_col]
        days = df1[in_day_col]
        df1[(mushroom_pref, "batch_date")] = pd.to_datetime({
            "year": years,
            "month": months,
            "day": days,
        }, errors="coerce")
    except Exception:
        # If any of the keys missing, ignore: batch_date may already exist or be unavailable
        pass

    # Prepare df_viz and Date column
    df_viz = df1.copy()
    df_viz["Date"] = df_viz.index.date

    # Light hours calculation (resample daily on the datetime index)
    light_daily = None
    try:
        light_daily = df_viz[[cols_light_on,
                              cols_light_off]].resample("D").mean()
        on_m = light_daily[cols_light_on].fillna(0)
        off_m = light_daily[cols_light_off].fillna(0)
        total_cycle = on_m + off_m
        light_daily["light_hours"] = np.where(
            total_cycle > 0, (on_m / total_cycle) * 24, 0)
    except Exception:
        light_daily = pd.DataFrame({"light_hours": []})

    # Build subplots
    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        subplot_titles=(
            "Daily Temperature Distribution (Violin)",
            "Daily Humidity Distribution (Violin)",
            "Daily CO2 Distribution (Violin)",
            "Daily Light Duration (Based on Settings)",
        ),
        row_heights=[0.25, 0.25, 0.25, 0.25],
    )

    colors_env = {
        "Temperature": {"violin": "#3366CC", "fill": "rgba(51, 102, 204, 0.2)"},
        "Humidity": {"violin": "#00CC96", "fill": "rgba(0, 204, 150, 0.2)"},
        "CO2": {"violin": "#9467BD", "fill": "rgba(148, 103, 189, 0.2)"},
    }

    # Add violins
    for i, (label, col) in enumerate(target_columns.items()):
        row_num = i + 1
        c = colors_env[label]
        y_series = None
        try:
            y_series = df_viz[col]
        except Exception:
            # try without top-level if columns were de-levelled
            flat_col = col[1]
            if flat_col in df_viz.columns:
                y_series = df_viz[flat_col]

        if y_series is None:
            warnings.warn(
                f"Missing data for {label} in room {room}; skipping violin.")
            continue

        fig.add_trace(
            go.Violin(
                x=df_viz["Date"],
                y=y_series,
                name=label,
                legendgroup=label,
                showlegend=True,
                line_color=c["violin"],
                fillcolor=c["fill"],
                hoverinfo="y",
                scalemode="width",
                points="outliers",
                box_visible=True,
                meanline_visible=True,
            ),
            row=row_num,
            col=1,
        )

    # Light bar
    try:
        fig.add_trace(
            go.Bar(
                x=light_daily.index,
                y=light_daily["light_hours"],
                name="Light Hours (Est.)",
                marker_color="#FECB52",
                hovertemplate="%{x|%Y-%m-%d}<br>Duration: %{y:.1f} hrs<extra></extra>",
            ),
            row=4,
            col=1,
        )
    except Exception:
        warnings.warn("Could not add light hours bar (missing light settings)")

    # Median trend lines
    try:
        daily_median = df_viz.groupby("Date").agg({
            target_columns["Temperature"]: "median",
            target_columns["Humidity"]: "median",
            target_columns["CO2"]: "median",
        }).reset_index()

        daily_median["Date_dt"] = pd.to_datetime(daily_median["Date"])

        line_colors = {"Temperature": "#3366CC",
                       "Humidity": "#00CC96", "CO2": "#9467BD"}

        for i, (label, col) in enumerate(target_columns.items()):
            row_num = i + 1
            try:
                y_vals = daily_median[col]
            except Exception:
                # try flattened
                y_vals = daily_median.get(col[1], pd.Series(dtype=float))

            x_vals = daily_median["Date_dt"]
            if y_vals.notna().any():
                fig.add_trace(
                    go.Scatter(
                        x=x_vals,
                        y=y_vals,
                        mode="lines+markers",
                        name=f"{label} Median",
                        legendgroup=label,
                        showlegend=True,
                        line=dict(color=line_colors[label], width=2.5),
                        marker=dict(size=4, symbol="circle"),
                        hovertemplate="%{x|%Y-%m-%d}<br>Median: %{y:.2f}<extra></extra>",
                    ),
                    row=row_num,
                    col=1,
                )
    except Exception:
        warnings.warn("Could not compute median trend lines; skipping.")

    # Colored bands per batch
    try:
        batch_series = df1[batch_date_col].dropna()
        if batch_series.empty:
            # no batch
            pass
        else:
            unique_batches = pd.to_datetime(np.sort(batch_series.unique()))
            color_palette = px.colors.qualitative.Plotly
            for i, batch_dt in enumerate(unique_batches):
                mask = df1[batch_date_col] == batch_dt
                if mask.any():
                    x0 = df1.index[mask].min()
                    x1 = df1.index[mask].max()
                    color = color_palette[i % len(color_palette)]
                    day_nums = df1.loc[mask, in_day_num_col].dropna(
                    ) if in_day_num_col in df1.columns else pd.Series()
                    day_num = int(
                        day_nums.iloc[0]) if not day_nums.empty else "N/A"
                    label = f"Batch {pd.to_datetime(batch_dt).strftime('%Y-%m-%d')} (Day {day_num})"
                    fig.add_vrect(
                        x0=x0,
                        x1=x1,
                        fillcolor=color,
                        opacity=0.12,
                        layer="below",
                        line_width=0,
                        annotation_text=label,
                        annotation_position="top left",
                        annotation_font_size=10,
                        annotation_font_color=color,
                        annotation_showarrow=False,
                    )
    except Exception:
        warnings.warn("Could not add batch vrects; skipping.")

    # Growth-phase backgrounds based on in_day_num
    try:
        if in_day_num_col in df_viz.columns:
            is_growth = df_viz[in_day_num_col].between(1, 27, inclusive="both")
            df_viz["phase"] = np.where(is_growth, "growth", "non_growth")
            df_viz["phase_group"] = (
                df_viz["phase"] != df_viz["phase"].shift()).cumsum()
            for (phase, _), group in df_viz.groupby(["phase", "phase_group"]):
                if group.empty:
                    continue
                x0, x1 = group.index.min(), group.index.max()
                color = "#E6F7E6" if phase == "growth" else "#FAFAFA"
                opacity = 0.25 if phase == "growth" else 0.08
                fig.add_vrect(x0=x0, x1=x1, fillcolor=color,
                              opacity=opacity, layer="below", line_width=0)
    except Exception:
        warnings.warn("Could not add growth-phase backgrounds; skipping.")

    fig.update_layout(
        height=1200,
        title_text=f"Room {room} Daily Environment Statistics with Mushroom Batch Highlighting",
        hovermode="x unified",
        template="plotly_white",
        showlegend=True,
    )

    fig.update_yaxes(title_text="Temperature (°C)", row=1, col=1)
    fig.update_yaxes(title_text="Humidity (%)", row=2, col=1)
    fig.update_yaxes(title_text="CO2 (ppm)", row=3, col=1)
    fig.update_yaxes(title_text="Hours (h)", row=4, col=1)
    fig.update_xaxes(title_text="Date", tickformat="%m-%d", row=4, col=1)

    if show:
        fig.show()

    return fig


def analyze_and_plot_rooms(
    rooms: List[str],
    start_time: Optional[pd.Timestamp] = None,
    end_time: Optional[pd.Timestamp] = None,
    processor=None,
    return_figs: bool = False,
    verbose: bool = False,
) -> Optional[Dict[str, go.Figure]]:
    """
    High-level wrapper: for each room, query/prepare df1 (if processor supplied) and call plotting.

    If `processor` is None, the caller should prepare `df1` and call `plot_room_daily_environment` directly.
    When `return_figs=True`, returns dict mapping room -> Figure.
    """
    figs = {}
    for room in rooms:
        df1 = None

        # Try processor-provided helper methods first
        if processor is not None:
            # Preferred: a method that returns the pivoted df1 directly
            for method_name in ("query_room_pivoted", "get_pivoted_for_room", "get_pivoted_df_for_room"):
                try:
                    method = getattr(processor, method_name, None)
                    if callable(method):
                        df1 = method(room, start_time=start_time,
                                     end_time=end_time)
                        if df1 is not None:
                            break
                except Exception:
                    df1 = None

            # Fallback: reconstruct df1 similarly to notebook logic
            if df1 is None:
                try:
                    configs = processor._get_device_configs_cached(room)
                    all_query_df = pd.concat(
                        configs.values(), ignore_index=True)
                    df = all_query_df.groupby("device_alias", group_keys=False).apply(
                        query_data_by_batch_time, start_time, end_time
                    ).reset_index().sort_values("time")
                    df["room"] = df["device_name"].apply(
                        lambda x: x.split("_")[-1])
                    df1 = df.pivot_table(index="time", columns=[
                                         "room", "device_name", "point_name"], values="value")
                except Exception as e:
                    df1 = None
                    if verbose:
                        warnings.warn(
                            f"Failed to build df1 for room {room} via processor fallback: {e}")

        if df1 is None:
            warnings.warn(
                f"No df1 provided or returned for room {room}; skipping.")
            continue

        # Diagnostics when requested
        if verbose:
            try:
                print(f"\n--- Diagnostics for room {room} ---")
                print("df1.shape:", getattr(df1, 'shape', None))
                print("Columns sample:", list(df1.columns[:20]))
                # key checks
                mushroom_pref = f"mushroom_info_{room}"
                env_pref = f"env_status_{room}"
                light_pref = f"grow_light_{room}"
                checks = [
                    (mushroom_pref, "in_year"),
                    (mushroom_pref, "in_month"),
                    (mushroom_pref, "in_day"),
                    (mushroom_pref, "in_day_num"),
                    (env_pref, "temperature"),
                    (env_pref, "humidity"),
                    (env_pref, "co2"),
                    (light_pref, "on_mset"),
                    (light_pref, "off_mset"),
                ]
                for c in checks:
                    try:
                        s = df1[c]
                        print(f"{c}: non-null count =", s.notna().sum())
                    except Exception:
                        print(f"{c}: MISSING")
                # show head
                try:
                    print("df1 head:")
                    print(df1.head(3))
                except Exception:
                    pass
            except Exception:
                pass

        try:
            fig = plot_room_daily_environment(df1, room, show=False)
            figs[room] = fig
            if not return_figs:
                fig.show()
        except Exception as e:
            warnings.warn(f"Failed to plot room {room}: {e}")

    if return_figs:
        return figs


"""Multi-room environment data analysis and visualization utilities.

Provides a single entry function `analyze_and_plot_rooms` which mirrors
the notebook logic: fetch historical IoT data for rooms, compute daily
metrics (median temp/hum/CO2, estimated light hours, growth fraction)
and produce Plotly figures. Designed to be imported and called from
notebooks or scripts.
"""


def _compute_daily_metrics(df1: pd.DataFrame, room: str) -> pd.DataFrame:
    """Compute daily median env metrics and light/growth stats for one room.

    Expects `df1` with MultiIndex columns like ('env_status_<room>', 'temperature').
    Returns a DataFrame indexed by date with columns: temp_median, hum_median,
    co2_median, light_hours, growth_frac, room.
    """
    df_viz = df1.copy()
    df_viz.index = pd.to_datetime(df_viz.index)

    temp_col = (f'env_status_{room}', 'temperature')
    hum_col = (f'env_status_{room}', 'humidity')
    co2_col = (f'env_status_{room}', 'co2')
    grow_on_col = (f'grow_light_{room}', 'on_mset')
    grow_off_col = (f'grow_light_{room}', 'off_mset')
    in_daynum_col = (f'mushroom_info_{room}', 'in_day_num')

    # daily median for env vars
    daily = df_viz.resample('D').median()
    # safe selection: if missing columns produce NaNs
    for col, name in ((temp_col, 'temp_median'), (hum_col, 'hum_median'), (co2_col, 'co2_median')):
        daily[name] = daily.get(col)

    # ensure consistent index and Date column
    daily.index = pd.to_datetime(daily.index)
    daily['Date'] = daily.index.date

    # estimated light hours
    try:
        light_daily = df_viz[[grow_on_col, grow_off_col]].resample('D').mean()
        on_m = light_daily[grow_on_col].fillna(0)
        off_m = light_daily[grow_off_col].fillna(0)
        total_cycle = on_m + off_m
        light_hours = np.where(total_cycle > 0, (on_m / total_cycle) * 24, 0)
        daily['light_hours'] = light_hours
    except Exception:
        daily['light_hours'] = 0

    # growth fraction (1-27 -> growth)
    if in_daynum_col in df_viz.columns:
        is_growth = df_viz[in_daynum_col].between(1, 27)
        growth_frac = is_growth.groupby(df_viz.index.date).mean()
        daily['growth_frac'] = daily['Date'].map(
            lambda d: growth_frac.get(d, 0))
    else:
        daily['growth_frac'] = 0

    daily['room'] = room
    # keep only needed columns
    return daily.reset_index(drop=False)[['Date', 'temp_median', 'hum_median', 'co2_median', 'light_hours', 'growth_frac', 'room']]


def analyze_and_plot_rooms(
    rooms: List[str],
    start_time: datetime,
    end_time: datetime,
    processor: Optional[Any] = None,
    return_figs: bool = False
) -> Optional[Dict[str, Any]]:
    """Fetch historical data for `rooms`, compute daily metrics and plot.

    - `processor` can be an existing EnvironmentDataProcessor; if None, a new one is created.
    - Returns dict of Plotly figures when `return_figs=True`, else shows figures inline.
    """
    processor = processor or create_env_data_processor()
    all_rooms_daily = []

    for room in rooms:
        room_configs = processor._get_device_configs_cached(room)
        if not room_configs:
            # skip empty
            continue

        all_query_df = pd.concat(room_configs.values(), ignore_index=True)
        df = all_query_df.groupby("device_alias", group_keys=False).apply(
            query_data_by_batch_time, start_time, end_time
        ).reset_index().sort_values("time")

        if df.empty:
            continue

        df['room'] = df['device_name'].apply(lambda x: x.split('_')[-1])
        df1 = df.pivot_table(index='time', columns=[
                             'room', 'device_name', 'point_name'], values='value')

        # attempt to compute batch_date but ignore failures
        in_year_col = (f'mushroom_info_{room}', 'in_year')
        in_month_col = (f'mushroom_info_{room}', 'in_month')
        in_day_col = (f'mushroom_info_{room}', 'in_day')
        if in_year_col in df1.columns and in_month_col in df1.columns and in_day_col in df1.columns:
            try:
                years = df1[in_year_col]
                months = df1[in_month_col]
                days = df1[in_day_col]
                df1[(f'mushroom_info_{room}', 'batch_date')] = pd.to_datetime(
                    {'year': years, 'month': months, 'day': days}, errors='coerce')
            except Exception:
                df1[(f'mushroom_info_{room}', 'batch_date')] = pd.NaT

        daily = _compute_daily_metrics(df1, room)
        all_rooms_daily.append(daily)

        # per-room detailed figure (mirror notebook 单元8)
        try:
            df_viz = df1.copy()
            df_viz.index = pd.to_datetime(df_viz.index)
            df_viz['Date'] = df_viz.index.date

            target_columns = {
                'Temperature': (f'env_status_{room}', 'temperature'),
                'Humidity': (f'env_status_{room}', 'humidity'),
                'CO2': (f'env_status_{room}', 'co2')
            }
            cols_light_on = (f'grow_light_{room}', 'on_mset')
            cols_light_off = (f'grow_light_{room}', 'off_mset')

            # skip room if no environment data
            has_env = any(col in df_viz.columns and df_viz[col].notna(
            ).any() for col in target_columns.values())
            if has_env:
                fig_room = make_subplots(
                    rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                    subplot_titles=(
                        'Daily Temperature Distribution (Violin)',
                        'Daily Humidity Distribution (Violin)',
                        'Daily CO2 Distribution (Violin)',
                        'Daily Light Duration (Based on Settings)'
                    ), row_heights=[0.25, 0.25, 0.25, 0.25]
                )

                colors_env = {
                    'Temperature': {'violin': '#3366CC', 'fill': 'rgba(51, 102, 204, 0.2)'},
                    'Humidity': {'violin': '#00CC96', 'fill': 'rgba(0, 204, 150, 0.2)'},
                    'CO2': {'violin': '#9467BD', 'fill': 'rgba(148, 103, 189, 0.2)'}
                }

                for i, (label, col) in enumerate(target_columns.items()):
                    if col in df_viz.columns and df_viz[col].notna().any():
                        c = colors_env[label]
                        fig_room.add_trace(
                            go.Violin(
                                x=df_viz['Date'],
                                y=df_viz[col],
                                name=label,
                                legendgroup=label,
                                showlegend=True,
                                line_color=c['violin'],
                                fillcolor=c['fill'],
                                hoverinfo='y',
                                scalemode='width',
                                points='outliers',
                                box_visible=True,
                                meanline_visible=True
                            ),
                            row=i+1, col=1,
                        )

                # light hours bar
                try:
                    light_daily = df_viz[[cols_light_on,
                                          cols_light_off]].resample('D').mean()
                    on_m = light_daily[cols_light_on].fillna(0)
                    off_m = light_daily[cols_light_off].fillna(0)
                    total_cycle = on_m + off_m
                    light_hours = np.where(
                        total_cycle > 0, (on_m / total_cycle) * 24, 0)
                    fig_room.add_trace(
                        go.Bar(x=light_daily.index, y=light_hours,
                               name='Light Hours (Est.)', marker_color='#FECB52'),
                        row=4, col=1
                    )
                except Exception:
                    pass

                # median lines
                try:
                    daily_median = df_viz.groupby('Date').agg(
                        {v: 'median' for v in target_columns.values()}).reset_index()
                    daily_median['Date_dt'] = pd.to_datetime(
                        daily_median['Date'])
                    line_colors = {'Temperature': '#3366CC',
                                   'Humidity': '#00CC96', 'CO2': '#9467BD'}
                    for i, (label, col) in enumerate(target_columns.items()):
                        if col in daily_median:
                            fig_room.add_trace(
                                go.Scatter(x=daily_median['Date_dt'], y=daily_median[col], mode='lines+markers',
                                           name=f"{label} Median", legendgroup=label,
                                           line=dict(color=line_colors[label], width=2.5), marker=dict(size=4)),
                                row=i+1, col=1
                            )
                except Exception:
                    pass

                # batch vrects
                batch_date_col = (f'mushroom_info_{room}', 'batch_date')
                in_day_num_col = (f'mushroom_info_{room}', 'in_day_num')
                if batch_date_col in df1.columns:
                    batch_series = df1[batch_date_col].dropna()
                    if not batch_series.empty:
                        unique_batches = pd.to_datetime(
                            np.sort(batch_series.unique()))
                        color_palette = px.colors.qualitative.Plotly
                        for i, batch_dt in enumerate(unique_batches):
                            mask = df1[batch_date_col] == batch_dt
                            if mask.any():
                                x0 = df1.index[mask].min()
                                x1 = df1.index[mask].max()
                                color = color_palette[i % len(color_palette)]
                                day_nums = df1.loc[mask, in_day_num_col].dropna(
                                ) if in_day_num_col in df1.columns else pd.Series(dtype=float)
                                day_num = int(
                                    day_nums.iloc[0]) if not day_nums.empty else "N/A"
                                label = f"Batch {batch_dt.strftime('%Y-%m-%d')} (Day {day_num})"
                                fig_room.add_vrect(x0=x0, x1=x1, fillcolor=color, opacity=0.12, layer='below', line_width=0,
                                                   annotation_text=label, annotation_position='top left', annotation_font_size=10,
                                                   annotation_font_color=color, annotation_showarrow=False)

                # growth phase background
                if in_day_num_col in df_viz.columns:
                    try:
                        is_growth = df_viz[in_day_num_col].between(1, 27)
                        phase_df = pd.DataFrame(
                            {'is_growth': is_growth, 'idx': df_viz.index})
                        phase_df['group'] = (
                            phase_df['is_growth'] != phase_df['is_growth'].shift()).cumsum()
                        for _, group in phase_df.groupby('group'):
                            if group.empty:
                                continue
                            phase = 'growth' if group['is_growth'].iloc[0] else 'non_growth'
                            x0, x1 = group['idx'].min(), group['idx'].max()
                            color = '#E6F7E6' if phase == 'growth' else '#FAFAFA'
                            opacity = 0.25 if phase == 'growth' else 0.08
                            fig_room.add_vrect(
                                x0=x0, x1=x1, fillcolor=color, opacity=opacity, layer='below', line_width=0)
                    except Exception:
                        pass

                fig_room.update_layout(
                    height=1200, title_text=f"Room {room} Daily Environment Statistics", hovermode='x unified', template='plotly_white')
                # collect per-room figs
                if 'per_room_figs' not in locals():
                    per_room_figs = {}
                per_room_figs[room] = fig_room
                # attach to all return when requested later
                globals()['per_room_figs'] = per_room_figs
        except Exception:
            # don't fail the loop on per-room plotting errors
            pass

    if not all_rooms_daily:
        print('No daily data collected for given rooms/time window')
        return None

    all_daily_df = pd.concat(all_rooms_daily, ignore_index=True)

    # Flatten column names: some upstream operations can produce tuple/Multiple levels
    # (e.g. ('Date', '', '')) which Plotly does not accept as x/y keys.
    def _flatten_col(col):
        if isinstance(col, tuple):
            for part in col:
                try:
                    if part is not None and str(part).strip() != "":
                        return str(part)
                except Exception:
                    continue
            return "_".join([str(p) for p in col if p is not None])
        return str(col)

    all_daily_df.columns = [_flatten_col(c) for c in all_daily_df.columns]
    # ensure Date is datetime/date
    if 'Date' in all_daily_df.columns:
        all_daily_df['Date'] = pd.to_datetime(all_daily_df['Date'])

    # Figures
    figs = {}
    figs['temp'] = px.line(all_daily_df, x='Date', y='temp_median',
                           color='room', title='Daily Median Temperature by Room')
    figs['hum'] = px.line(all_daily_df, x='Date', y='hum_median',
                          color='room', title='Daily Median Humidity by Room')
    figs['co2'] = px.line(all_daily_df, x='Date', y='co2_median',
                          color='room', title='Daily Median CO2 by Room')
    figs['light'] = px.bar(all_daily_df, x='Date', y='light_hours', color='room',
                           barmode='group', title='Estimated Daily Light Hours by Room')

    pivot_growth = all_daily_df.pivot_table(
        index='room', columns='Date', values='growth_frac', fill_value=0)
    figs['growth'] = px.imshow(pivot_growth, labels=dict(
        x='Date', y='Room', color='Growth Fraction'), aspect='auto', title='Daily Growth Fraction (1-27 days)')

    if return_figs:
        return figs

    # show inline
    for f in figs.values():
        f.show()

    return None

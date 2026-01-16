import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px

# Ensure index is datetime (should already be)
df1.index = pd.to_datetime(df1.index)

# ==============================
# Step 1: Create batch_date from in_year, in_month, in_day
# ==============================
# Define the exact column tuples
in_year_col = ('mushroom_info_611', 'in_year')
in_month_col = ('mushroom_info_611', 'in_month')
in_day_col = ('mushroom_info_611', 'in_day')

# Extract and clean
years = df1[in_year_col]
months = df1[in_month_col]
days = df1[in_day_col]

# Combine into datetime — this works even with MultiIndex columns!
# pd.to_datetime can accept dict-like with Series values
df1[('mushroom_info_611', 'batch_date')] = pd.to_datetime({
    'year': years,
    'month': months,
    'day': days
}, errors='coerce')  # 'coerce' turns invalid dates to NaT

# ==============================
# Step 2: Prepare visualization data
# ==============================
target_columns = {
    'Temperature': ('env_status_611', 'temperature'),
    'Humidity': ('env_status_611', 'humidity'),
    'CO2': ('env_status_611', 'co2')
}

cols_light_on = ('grow_light_611', 'on_mset')
cols_light_off = ('grow_light_611', 'off_mset')

df_viz = df1.copy()
df_viz['Date'] = df_viz.index.date

# Light hours calculation
light_daily = df_viz[[cols_light_on, cols_light_off]].resample('D').mean()
on_m = light_daily[cols_light_on].fillna(0)
off_m = light_daily[cols_light_off].fillna(0)
total_cycle = on_m + off_m
light_daily['light_hours'] = np.where(total_cycle > 0, (on_m / total_cycle) * 24, 0)

# ==============================
# Step 3: Create subplots
# ==============================
fig = make_subplots(
    rows=4, cols=1,
    shared_xaxes=True,
    vertical_spacing=0.06,
    subplot_titles=(
        'Daily Temperature Distribution (Violin)', 
        'Daily Humidity Distribution (Violin)', 
        'Daily CO2 Distribution (Violin)',
        'Daily Light Duration (Based on Settings)'
    ),
    row_heights=[0.25, 0.25, 0.25, 0.25]
)

colors_env = {
    'Temperature': {'violin': '#3366CC', 'fill': 'rgba(51, 102, 204, 0.2)'},
    'Humidity': {'violin': '#00CC96', 'fill': 'rgba(0, 204, 150, 0.2)'},
    'CO2': {'violin': '#9467BD', 'fill': 'rgba(148, 103, 189, 0.2)'}
}

for i, (label, col) in enumerate(target_columns.items()):
    row_num = i + 1
    c = colors_env[label]
    fig.add_trace(
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
        row=row_num, col=1
    )

fig.add_trace(
    go.Bar(
        x=light_daily.index,
        y=light_daily['light_hours'],
        name='Light Hours (Est.)',
        marker_color='#FECB52',
        hovertemplate='%{x|%Y-%m-%d}<br>Duration: %{y:.1f} hrs<extra></extra>'
    ),
    row=4, col=1
)
# ==============================
# Step 3.5: Add median trend lines for each environment variable
# ==============================
# Resample to daily median
daily_median = df_viz.groupby('Date').agg({
    target_columns['Temperature']: 'median',
    target_columns['Humidity']: 'median',
    target_columns['CO2']: 'median'
}).reset_index()

# Convert 'Date' back to datetime for plotting (Plotly prefers datetime on x-axis)
daily_median['Date_dt'] = pd.to_datetime(daily_median['Date'])

# Define line colors (same as violin outline for consistency)
line_colors = {
    'Temperature': '#3366CC',
    'Humidity': '#00CC96',
    'CO2': '#9467BD'
}

# Add median lines to each subplot
for i, (label, col) in enumerate(target_columns.items()):
    row_num = i + 1
    y_vals = daily_median[col]
    x_vals = daily_median['Date_dt']
    
    # Only plot if there's at least one non-NaN value
    if y_vals.notna().any():
        fig.add_trace(
            go.Scatter(
                x=x_vals,
                y=y_vals,
                mode='lines+markers',
                name=f"{label} Median",
                legendgroup=label,
                showlegend=True,
                line=dict(color=line_colors[label], width=2.5),
                marker=dict(size=4, symbol='circle'),
                hovertemplate='%{x|%Y-%m-%d}<br>Median: %{y:.2f}<extra></extra>'
            ),
            row=row_num, col=1
        )

# ==============================
# Step 4: Add colored bands per mushroom batch
# ==============================
batch_date_col = ('mushroom_info_611', 'batch_date')
in_day_num_col = ('mushroom_info_611', 'in_day_num')

batch_series = df1[batch_date_col].dropna()
if batch_series.empty:
    print("Warning: No valid batch dates found.")
else:
    # Sort unique batch dates using numpy, then ensure Timestamp type
    unique_batches = pd.to_datetime(np.sort(batch_series.unique()))

    color_palette = px.colors.qualitative.Plotly
    for i, batch_dt in enumerate(unique_batches):
        mask = df1[batch_date_col] == batch_dt
        if mask.any():
            x0 = df1.index[mask].min()
            x1 = df1.index[mask].max()
            color = color_palette[i % len(color_palette)]
            
            # Get representative in_day_num for this batch (first non-NaN)
            day_nums = df1.loc[mask, in_day_num_col].dropna()
            day_num = int(day_nums.iloc[0]) if not day_nums.empty else "N/A"
            
            label = f"Batch {batch_dt.strftime('%Y-%m-%d')} (Day {day_num})"
            
            fig.add_vrect(
                x0=x0,
                x1=x1,
                fillcolor=color,
                opacity=0.12,
                layer='below',
                line_width=0,
                annotation_text=label,
                annotation_position="top left",
                annotation_font_size=10,
                annotation_font_color=color,
                annotation_showarrow=False
            )
# ==============================
# Step 5: Highlight growth phase using ONLY in_day_num (1-27 = growth)
# ==============================
# ==============================
# Step: Add growth phase background based solely on in_day_num
# ==============================
in_day_num_col = ('mushroom_info_611', 'in_day_num')

# Create phase mask: 1–27 → growth, else → non_growth
is_growth = df_viz[in_day_num_col].between(1, 27, inclusive='both')
df_viz['phase'] = np.where(is_growth, 'growth', 'non_growth')

# Group contiguous segments
df_viz['phase_group'] = (df_viz['phase'] != df_viz['phase'].shift()).cumsum()

# Generate continuous intervals
for (phase, _), group in df_viz.groupby(['phase', 'phase_group']):
    if group.empty:
        continue
    x0, x1 = group.index.min(), group.index.max()
    color = '#E6F7E6' if phase == 'growth' else '#FAFAFA'
    opacity = 0.25 if phase == 'growth' else 0.08
    fig.add_vrect(
        x0=x0, x1=x1,
        fillcolor=color,
        opacity=opacity,
        layer='below',
        line_width=0
        # ⚠️ No annotation_text → avoids "new text" clutter
    )
fig.update_layout(
    height=1200,
    title_text="Room 611 Daily Environment Statistics with Mushroom Batch Highlighting",
    hovermode="x unified",
    template="plotly_white",
    showlegend=True
)

fig.update_yaxes(title_text="Temperature (°C)", row=1, col=1)
fig.update_yaxes(title_text="Humidity (%)", row=2, col=1)
fig.update_yaxes(title_text="CO2 (ppm)", row=3, col=1)
fig.update_yaxes(title_text="Hours (h)", row=4, col=1)
fig.update_xaxes(title_text="Date", tickformat="%m-%d", row=4, col=1)

fig.show()
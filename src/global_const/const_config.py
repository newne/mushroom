"""
@Project ：get_data
@File    ：const_config.py
@IDE     ：PyCharm
@Author  ：niucg1@lenovo.com
@Date    ：2024/11/5 18:32
@Desc     :
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List

import numpy as np
import pandas as pd
import plotly.express as px


@dataclass
class TemperatureConfig:
    """Temperature thresholds and configuration"""

    NORMAL_TEMP_THRESHOLD = 26.0
    IQR_MULTIPLIER = 1.5


@dataclass
class PlotConfig:
    """Plot configuration parameters"""

    title: str = ""
    xaxis_title: str = "Time"
    yaxis_title: str = "Value"
    color_scheme: str = "blues"
    show_legend: bool = True
    height: Optional[int] = None
    width: Optional[int] = None


class PlotType(Enum):
    """Plot type enumeration"""

    HISTOGRAM = "histogram"
    LINE = "line"
    SCATTER = "scatter"
    BOX = "box"
    VIOLIN = "violin"
    POLAR = "polar"


@dataclass
class ThemeConfig:
    """Theme configuration"""

    primary_color: str = "#1f77b4"
    secondary_color: str = "#ff7f0e"
    background_color: str = "#ffffff"
    font_family: str = "Arial"
    font_size: int = 10

    def get_rgba(self, color: str, alpha: float) -> str:
        """Convert hex color to rgba"""
        rgb = px.colors.hex_to_rgb(color)
        return f"rgba{tuple(list(rgb) + [alpha])}"


@dataclass
class PlotConfig:
    """Enhanced plot configuration"""

    title: str = field(default="")
    xaxis_title: str = field(default="Time")
    yaxis_title: str = field(default="Value")
    height: int = field(default=500)
    width: Optional[int] = field(default=None)
    show_legend: bool = field(default=True)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    interactive: bool = field(default=True)

    def get_layout(self) -> Dict:
        """Get plotly layout configuration"""
        return {
            "title": self.title,
            "xaxis_title": self.xaxis_title,
            "yaxis_title": self.yaxis_title,
            "height": self.height,
            "width": self.width,
            "showlegend": self.show_legend,
            "paper_bgcolor": self.theme.background_color,
            "plot_bgcolor": self.theme.background_color,
            "font": {"family": self.theme.font_family, "size": self.theme.font_size},
        }


class DataValidator:
    """Data validation utilities"""

    @staticmethod
    def validate_numeric_columns(df: pd.DataFrame, columns: List[str]) -> bool:
        """Validate if columns are numeric"""
        return all(np.issubdtype(df[col].dtype, np.number) for col in columns)

    @staticmethod
    def remove_outliers(
        df: pd.DataFrame, column: str, threshold: float = 3
    ) -> pd.DataFrame:
        """Remove outliers using z-score method"""
        z_scores = np.abs((df[column] - df[column].mean()) / df[column].std())
        return df[z_scores < threshold]

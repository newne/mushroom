"""
@Project ：Mushroom Solution
@File    ：streamlit_app.py
@Desc     : Streamlit Application Entry Point
"""

import sys
from pathlib import Path
import streamlit as st

# Must be the first Streamlit command
st.set_page_config(
    page_title="食用菌种植监控系统",
    page_icon="🍄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Use global_const for path management if needed, though running from src usually handles it
try:
    from global_const.global_const import BASE_DIR
except ImportError:
    # Fallback if run from outside src without modules installed
    current_path = Path(__file__).resolve().parent
    if str(current_path) not in sys.path:
        sys.path.append(str(current_path))
    from global_const.global_const import BASE_DIR

def main():
    # Lazy import to avoid circular dependencies or early execution
    import web_app.dashboard as dashboard

    pg = st.navigation(
        [
            st.Page(dashboard.show, title="数据看板", icon="📊"),
        ]
    )
    pg.run()

if __name__ == "__main__":
    main()

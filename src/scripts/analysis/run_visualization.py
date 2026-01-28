#!/usr/bin/env python3
"""Run per-room visualization with diagnostics and save HTML outputs."""
from datetime import datetime
import os
import sys
import traceback

# 使用BASE_DIR统一管理路径
from global_const.global_const import ensure_src_path
ensure_src_path()

try:
    from environment.processor import create_env_data_processor
    from utils.visualization import analyze_and_plot_rooms
except Exception:
    # Fallback: import by file path when package import fails
    import importlib.util

    def _load_from_path(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    repo_src = os.path.join(SCRIPT_ROOT, "src")
    env_path = os.path.join(repo_src, "environment", "processor.py")
    vis_path = os.path.join(repo_src, "utils", "visualization.py")
    env_mod = _load_from_path("env_data_processor", env_path)
    vis_mod = _load_from_path("visualization", vis_path)
    create_env_data_processor = getattr(env_mod, "create_env_data_processor")
    analyze_and_plot_rooms = getattr(vis_mod, "analyze_and_plot_rooms")


def main():
    rooms = ["607", "608", "611", "612"]
    start_time = datetime(2025, 12, 19, 0, 0, 0)
    end_time = datetime(2026, 1, 10, 0, 0, 0)

    processor = create_env_data_processor()

    out_dir = os.path.join("outputs", "visualization")
    os.makedirs(out_dir, exist_ok=True)

    try:
        figs = analyze_and_plot_rooms(
            rooms, start_time, end_time, processor=processor, return_figs=True, verbose=True)
    except Exception:
        print("analyze_and_plot_rooms raised an exception:\n",
              traceback.format_exc())
        figs = None

    if not figs:
        print("No figures returned.")
        return

    for room, fig in figs.items():
        try:
            out_path = os.path.join(out_dir, f"room_{room}.html")
            fig.write_html(out_path)
            print(f"Saved figure for room {room} -> {out_path}")
        except Exception:
            print(
                f"Failed to save figure for room {room}:\n", traceback.format_exc())


if __name__ == "__main__":
    main()

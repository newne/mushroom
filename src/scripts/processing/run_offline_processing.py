import sys
from datetime import datetime
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from global_const.paths import BASE_DIR, ensure_src_path

ensure_src_path()

from scripts.processing.offline_batch_runner import run_offline_batch


def main() -> int:
    start_date = "2025-12-22"
    end_date = datetime.now().strftime("%Y-%m-%d")

    return run_offline_batch(
        limit_per_room_day=2,
        start_date=start_date,
        end_date=end_date,
        log_file=str(BASE_DIR.parent / "logs" / "offline_processing_{time}.log"),
    )

if __name__ == "__main__":
    raise SystemExit(main())

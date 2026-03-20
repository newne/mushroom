import glob
import os
from typing import Optional

import pandas as pd

from utils import log_task_event


def _dataset_log(event: str, message: str, level: str = "INFO", **context):
    log_task_event(
        "VISION_OFFLINE_DATASET",
        event,
        message,
        level=level,
        task_type="helper",
        **context,
    )


class DatasetLoader:
    def __init__(self, dataset_dir: str = "data/dataset_v6"):
        self.dataset_dir = dataset_dir
        if not os.path.exists(self.dataset_dir):
            # Try absolute path or project root relative
            project_root = os.getcwd()
            self.dataset_dir = os.path.join(project_root, dataset_dir)

    def load_dataset(self, limit: Optional[int] = None) -> pd.DataFrame:
        """
        Loads images from the dataset directory into a DataFrame.
        Expected structure: data/dataset_v6/*.jpg
        """
        # Support common image extensions
        extensions = ["*.jpg", "*.jpeg", "*.png", "*.JPG", "*.PNG"]
        image_files = []

        for ext in extensions:
            image_files.extend(glob.glob(os.path.join(self.dataset_dir, ext)))

        if not image_files:
            # If no files found, return empty DF or raise warning
            _dataset_log(
                "VISION_OFFLINE_DATASET_EMPTY",
                "数据集目录中未发现图像",
                level="WARNING",
                dataset_dir=self.dataset_dir,
                status="skipped",
            )
            return pd.DataFrame(columns=["image_path", "filename"])

        if limit:
            image_files = image_files[:limit]

        data = []
        for path in image_files:
            data.append(
                {
                    "image_path": os.path.abspath(path),
                    "filename": os.path.basename(path),
                }
            )

        return pd.DataFrame(data)

    def get_evaluation_set(self, size: int = 10) -> pd.DataFrame:
        """Returns a subset for evaluation"""
        return self.load_dataset(limit=size)

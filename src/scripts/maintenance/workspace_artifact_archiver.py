#!/usr/bin/env python3
"""Archive runtime artifacts in a safe and repeatable way.

This script moves log/output/runtime-generated files into a timestamped
archive directory under .archive/. It is non-destructive by default and
keeps relative paths for traceability.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable, List


def _project_root() -> Path:
    """Return repository root based on current file location."""
    return Path(__file__).resolve().parents[3]


def _iter_files(path: Path, patterns: Iterable[str]) -> Iterable[Path]:
    """Yield files in path that match any glob pattern."""
    if not path.exists():
        return []

    matched: List[Path] = []
    for pattern in patterns:
        matched.extend(p for p in path.glob(pattern) if p.is_file())
    return matched


def _move_with_parents(src: Path, dst: Path) -> None:
    """Move src file to dst, creating parent directories if needed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))


def _archive_logs(root: Path, archive_base: Path) -> List[Path]:
    """Archive logs from logs/ and src/logs/."""
    moved: List[Path] = []
    targets = [root / "logs", root / "src" / "logs"]

    for target in targets:
        for log_file in _iter_files(target, patterns=["*.log"]):
            destination = archive_base / "logs" / log_file.name
            _move_with_parents(log_file, destination)
            moved.append(destination)

    return moved


def _archive_output(root: Path, archive_base: Path) -> List[Path]:
    """Archive all files in output/ and preserve relative hierarchy."""
    moved: List[Path] = []
    output_dir = root / "output"
    if not output_dir.exists():
        return moved

    for file_path in output_dir.rglob("*"):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(root)
        destination = archive_base / "output" / relative
        _move_with_parents(file_path, destination)
        moved.append(destination)

    return moved


def _archive_runtime(root: Path, archive_base: Path) -> List[Path]:
    """Archive runtime database and similar local artifacts."""
    moved: List[Path] = []
    runtime_files = [root / "mlflow.db"]

    for runtime_file in runtime_files:
        if not runtime_file.exists() or not runtime_file.is_file():
            continue
        destination = archive_base / "runtime" / runtime_file.name
        _move_with_parents(runtime_file, destination)
        moved.append(destination)

    return moved


def archive_workspace_artifacts(tag: str | None = None) -> List[Path]:
    """Archive known runtime artifacts and return moved file list.

    Args:
        tag: Optional archive suffix. If omitted, use current date.

    Returns:
        A list of archived destination paths.
    """
    root = _project_root()
    date_tag = tag or datetime.now().strftime("%Y%m%d")
    archive_base = root / ".archive" / f"workspace_cleanup_{date_tag}"
    archive_base.mkdir(parents=True, exist_ok=True)

    moved: List[Path] = []
    moved.extend(_archive_logs(root, archive_base))
    moved.extend(_archive_output(root, archive_base))
    moved.extend(_archive_runtime(root, archive_base))
    return moved


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Archive logs/output/runtime artifacts into .archive/."
    )
    parser.add_argument(
        "--tag",
        type=str,
        default=None,
        help="Archive suffix, e.g. 20260421. Defaults to today.",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    moved_files = archive_workspace_artifacts(tag=args.tag)

    print(f"Archived files: {len(moved_files)}")
    for path in moved_files:
        print(path)


if __name__ == "__main__":
    main()

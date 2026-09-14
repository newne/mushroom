"""Command-line entry point for the scheduling package."""

from .core.scheduler import run_scheduler


if __name__ == "__main__":
    run_scheduler()
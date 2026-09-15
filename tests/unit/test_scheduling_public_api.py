from scheduling import OptimizedScheduler, run_scheduler
from scheduling.core.scheduler import (
    OptimizedScheduler as CoreOptimizedScheduler,
    run_scheduler as core_run_scheduler,
)


def test_scheduling_package_exports_core_scheduler():
    assert OptimizedScheduler is CoreOptimizedScheduler
    assert run_scheduler is core_run_scheduler


def test_scheduling_main_uses_core_entrypoint():
    import scheduling.__main__ as scheduling_main

    assert scheduling_main.run_scheduler is core_run_scheduler
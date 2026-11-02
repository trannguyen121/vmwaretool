from concurrent.futures import Future
from pathlib import Path

import vmware_cleaner.task_manager as task_manager
from vmware_cleaner.models import CleanupResult, CleanupStatus, VirtualMachine
from vmware_cleaner.task_manager import ConcurrentTaskManager, TaskCallbacks


class ImmediateFutureExecutor:
    def __init__(self, **_kwargs):
        pass

    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, wait=False):
        pass


class ImmediateEngine:
    def cleanup(self, vm):
        return CleanupResult(vm, CleanupStatus.SKIPPED, "Trả kết quả tức thì")


def test_immediate_future_completion_does_not_deadlock(monkeypatch) -> None:
    monkeypatch.setattr(task_manager, "ThreadPoolExecutor", ImmediateFutureExecutor)
    vm = VirtualMachine("Demo", Path("Demo.vmx"))
    statuses = []
    results = []
    completed = []
    manager = ConcurrentTaskManager(
        ImmediateEngine(),
        TaskCallbacks(
            status=lambda item, status, detail: statuses.append((item, status, detail)),
            finished=results.append,
            all_finished=lambda: completed.append(True),
        ),
    )

    manager.start([vm], "cleanup", 1)

    assert not manager.running
    assert results[0].status == CleanupStatus.SKIPPED
    assert completed == [True]

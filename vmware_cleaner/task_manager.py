from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Literal

from .cleanup import CleanupEngine
from .models import CleanupResult, CleanupStatus, VirtualMachine

LOG = logging.getLogger(__name__)
Mode = Literal["scan", "dry_run", "cleanup"]


@dataclass(slots=True)
class TaskCallbacks:
    status: Callable[[VirtualMachine, CleanupStatus, str], None]
    finished: Callable[[CleanupResult], None]
    all_finished: Callable[[], None]


class ConcurrentTaskManager:
    """Queues VMs while preventing shared VMDK chains or host volumes in parallel."""

    def __init__(self, engine: CleanupEngine, callbacks: TaskCallbacks) -> None:
        self.engine = engine
        self.callbacks = callbacks
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._pending: list[VirtualMachine] = []
        self._active: dict[Future[CleanupResult], VirtualMachine] = {}
        self._active_chains: set[str] = set()
        self._active_volumes: set[str] = set()
        self._stopping = False
        self._mode: Mode = "scan"
        self._max_workers = 1

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._pending or self._active)

    def start(self, vms: list[VirtualMachine], mode: Mode, max_workers: int) -> None:
        with self._lock:
            if self._pending or self._active:
                raise RuntimeError("Đang có đợt xử lý khác")
            self._mode = mode
            self._max_workers = max(1, min(4, max_workers))
            self._pending = list(vms)
            self._stopping = False
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers, thread_name_prefix="vm-cleanup"
            )
            self._schedule_locked()

    def stop_scheduling(self) -> None:
        skipped: list[VirtualMachine] = []
        with self._lock:
            self._stopping = True
            skipped, self._pending = self._pending, []
        for vm in skipped:
            self.callbacks.status(vm, CleanupStatus.SKIPPED, "Đã dừng trước khi xếp lịch")
            self.callbacks.finished(CleanupResult(vm, CleanupStatus.SKIPPED, "Đã dừng trước khi xếp lịch"))
        self._notify_if_done()

    def _resources_free(self, vm: VirtualMachine) -> bool:
        chain = {str(p).casefold() for p in vm.chain_files}
        return not (chain & self._active_chains or vm.volume_ids & self._active_volumes)

    def _schedule_locked(self) -> None:
        if self._stopping or not self._executor:
            return
        while len(self._active) < self._max_workers:
            index = next((i for i, vm in enumerate(self._pending) if self._resources_free(vm)), None)
            if index is None:
                break
            vm = self._pending.pop(index)
            self._active_chains.update(str(p).casefold() for p in vm.chain_files)
            self._active_volumes.update(vm.volume_ids)
            self.callbacks.status(vm, CleanupStatus.RUNNING, "")
            future = self._executor.submit(getattr(self.engine, self._mode), vm)
            self._active[future] = vm
            future.add_done_callback(self._completed)

    def _completed(self, future: Future[CleanupResult]) -> None:
        with self._lock:
            vm = self._active.pop(future)
            self._active_chains.difference_update(str(p).casefold() for p in vm.chain_files)
            self._active_volumes.difference_update(vm.volume_ids)
        try:
            result = future.result()
        except Exception as exc:  # defensive boundary around worker threads
            LOG.exception("Unhandled worker error")
            result = CleanupResult(vm, CleanupStatus.FAILED, str(exc))
        self.callbacks.status(vm, result.status, result.message)
        self.callbacks.finished(result)
        with self._lock:
            self._schedule_locked()
        self._notify_if_done()

    def _notify_if_done(self) -> None:
        executor: ThreadPoolExecutor | None = None
        with self._lock:
            if self._pending or self._active:
                return
            executor, self._executor = self._executor, None
        if executor:
            executor.shutdown(wait=False)
        self.callbacks.all_finished()

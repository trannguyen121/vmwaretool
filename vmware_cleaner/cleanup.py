from __future__ import annotations

from pathlib import Path

from .models import CleanupResult, CleanupStatus, VirtualMachine, VMwareEnvironment
from .safety import list_running_vms, validate_gui_cleanup
from .ui_automation import VMwareUIAutomation

class CleanupEngine:
    """Workstation GUI Clean Up Disks orchestration."""

    def __init__(self, environment: VMwareEnvironment) -> None:
        self.environment = environment

    def scan(self, vm: VirtualMachine) -> CleanupResult:
        validation = validate_gui_cleanup(vm, self.environment, list_running_vms(self.environment))
        if not validation.safe:
            return CleanupResult(vm, CleanupStatus.SKIPPED, "; ".join(validation.reasons))
        size = self._disk_bytes(vm.chain_files)
        return CleanupResult(
            vm, CleanupStatus.DONE, "Có thể chạy VMware Clean Up Disks; chưa thay đổi dữ liệu",
            before_bytes=size, after_bytes=size,
        )

    def dry_run(self, vm: VirtualMachine) -> CleanupResult:
        validation = validate_gui_cleanup(vm, self.environment, list_running_vms(self.environment))
        if not validation.safe:
            return CleanupResult(vm, CleanupStatus.SKIPPED, "; ".join(validation.reasons))
        before = self._disk_bytes(vm.chain_files)
        return CleanupResult(
            vm, CleanupStatus.DONE,
            "DryRun: VMware > VM > Manage > Clean Up Disks > Clean up now",
            before_bytes=before, after_bytes=before,
        )

    def cleanup(self, vm: VirtualMachine) -> CleanupResult:
        validation = validate_gui_cleanup(vm, self.environment, list_running_vms(self.environment))
        if not validation.safe:
            return CleanupResult(vm, CleanupStatus.SKIPPED, "; ".join(validation.reasons))
        return VMwareUIAutomation(self.environment).cleanup(vm)

    @staticmethod
    def _disk_bytes(paths: frozenset[Path]) -> int:
        total = 0
        for path in paths:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total


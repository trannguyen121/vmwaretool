from __future__ import annotations

import logging

from .models import CleanupResult, CleanupStatus, VirtualMachine, VMwareEnvironment
from .safety import list_running_vms, validate_gui_cleanup
from .ui_automation import VMwareUIAutomation

LOG = logging.getLogger(__name__)
class CleanupEngine:
    """Scan/DryRun plus serial Workstation UI cleanup orchestration."""

    def __init__(self, environment: VMwareEnvironment) -> None:
        self.environment = environment

    def scan(self, vm: VirtualMachine) -> CleanupResult:
        validation = validate_gui_cleanup(vm, self.environment, list_running_vms(self.environment))
        if not validation.safe:
            return CleanupResult(vm, CleanupStatus.SKIPPED, "; ".join(validation.reasons))
        return CleanupResult(vm, CleanupStatus.DONE, "Có thể cleanup qua VMware UI; chưa thay đổi dữ liệu")

    def dry_run(self, vm: VirtualMachine) -> CleanupResult:
        validation = validate_gui_cleanup(vm, self.environment, list_running_vms(self.environment))
        if not validation.safe:
            return CleanupResult(vm, CleanupStatus.SKIPPED, "; ".join(validation.reasons))
        return CleanupResult(
            vm, CleanupStatus.DONE,
            "DryRun: chọn VM → VM → Manage → Clean Up Disks → Clean up now (tuần tự)",
        )

    def cleanup(self, vm: VirtualMachine) -> CleanupResult:
        validation = validate_gui_cleanup(vm, self.environment, list_running_vms(self.environment))
        if not validation.safe:
            return CleanupResult(vm, CleanupStatus.SKIPPED, "; ".join(validation.reasons))
        return VMwareUIAutomation(self.environment).cleanup(vm)


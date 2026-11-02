"""Optional headless VMDK compact backend.

This module is intentionally not used by the application's Clean Up Disks
workflow. ``vmware-vdiskmanager -k`` is not equivalent to Workstation's GUI
Clean Up Disks operation, but the implementation is retained for diagnostics
or a future explicitly separate Compact feature.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .models import CleanupResult, CleanupStatus, VirtualMachine, VMwareEnvironment
from .safety import list_running_vms, validate_force_compact, validate_vm

LOG = logging.getLogger(__name__)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CompactEngine:
    def __init__(self, environment: VMwareEnvironment) -> None:
        self.environment = environment

    def compact(self, vm: VirtualMachine, force: bool = False) -> CleanupResult:
        validator = validate_force_compact if force else validate_vm
        validation = validator(vm, self.environment, list_running_vms(self.environment))
        if not validation.safe:
            return CleanupResult(vm, CleanupStatus.SKIPPED, "; ".join(validation.reasons))
        assert self.environment.vdiskmanager_exe is not None
        before = self.disk_bytes(vm.chain_files)
        for disk in vm.vmdks:
            completed = subprocess.run(
                [str(self.environment.vdiskmanager_exe), "-k", str(disk)],
                capture_output=True, text=True, errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            if completed.returncode:
                after = self.disk_bytes(vm.chain_files)
                output = (completed.stderr or completed.stdout).strip()
                return CleanupResult(
                    vm, CleanupStatus.FAILED,
                    f"vmware-vdiskmanager trả mã {completed.returncode}: {output[-500:]}",
                    before_bytes=before, after_bytes=after,
                    reclaimed_bytes=max(0, before - after),
                )
        after = self.disk_bytes(vm.chain_files)
        return CleanupResult(
            vm, CleanupStatus.DONE, "Compact VMDK hoàn tất",
            before_bytes=before, after_bytes=after,
            reclaimed_bytes=max(0, before - after),
        )

    @staticmethod
    def disk_bytes(paths: frozenset[Path]) -> int:
        total = 0
        for path in paths:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

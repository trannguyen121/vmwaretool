from __future__ import annotations

import argparse
import json
import sys

from .discovery import discover_vms
from .environment import detect_environment
from .logging_config import configure_logging
from .models import CleanupResult, CleanupStatus
from .safety import list_running_vms, validate_gui_cleanup


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only scanner for VMware Disk Cleaner")
    parser.add_argument("--mode", choices=("scan", "dry-run"), default="scan")
    args = parser.parse_args()
    configure_logging()
    environment = detect_environment()
    vms = discover_vms(environment)
    running_vms = list_running_vms(environment)
    results = []
    for vm in vms:
        validation = validate_gui_cleanup(vm, environment, running_vms)
        if not validation.safe:
            result = CleanupResult(vm, CleanupStatus.SKIPPED, "; ".join(validation.reasons))
        elif args.mode == "scan":
            result = CleanupResult(vm, CleanupStatus.DONE, "Scan an toàn; chưa thay đổi dữ liệu")
        else:
            result = CleanupResult(
                vm, CleanupStatus.DONE,
                "DryRun: VMware UI > VM > Manage > Clean Up Disks > Clean up now",
            )
        results.append(
            {
                "name": vm.name,
                "vmx": str(vm.vmx_path),
                "vmdks": [str(path) for path in vm.vmdks],
                "size_bytes": vm.size_bytes,
                "status": result.status.value,
                "message": result.message,
            }
        )
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(
        json.dumps(
            {
                "vmware_version": environment.version,
                "cleanup_backend": "VMware Workstation UI Automation (serial)",
                "inventory": str(environment.inventory_path or ""),
                "mode": args.mode,
                "vm_count": len(vms),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


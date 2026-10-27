from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .discovery import read_vmware_text
from .models import ValidationResult, VirtualMachine, VMwareEnvironment

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def list_running_vms(environment: VMwareEnvironment) -> set[str] | None:
    if not environment.vmrun_exe:
        return None
    try:
        result = subprocess.run(
            [str(environment.vmrun_exe), "-T", "ws", "list"],
            capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines()[1:] if line.strip()]
    return {_norm(Path(line)) for line in lines}


def _has_snapshots(vm: VirtualMachine) -> bool:
    if any("-0000" in disk.stem for disk in vm.vmdks):
        return True
    vmsd = vm.vmx_path.with_suffix(".vmsd")
    if vmsd.is_file():
        try:
            text = read_vmware_text(vmsd)
            count = re.search(r'snapshot\.numSnapshots\s*=\s*"(\d+)"', text, re.I)
            entries = re.search(r'^snapshot\d+\.', text, re.I | re.M)
            if (count and int(count.group(1)) > 0) or entries:
                return True
        except OSError:
            return True
    return any(vm.vmx_path.parent.glob("*-Snapshot*.vmsn"))


def _is_linked(vm: VirtualMachine) -> bool:
    for disk in vm.vmdks:
        try:
            header = read_vmware_text(disk, 1024 * 1024)
        except OSError:
            continue
        if "parentFileNameHint" in header or 'ddb.deletable = "true"' in header:
            return True
    return False


def _has_locks(vm: VirtualMachine) -> bool:
    try:
        if any(p.name.lower().endswith(".lck") for p in vm.vmx_path.parent.iterdir()):
            return True
        return any(Path(str(disk) + ".lck").exists() for disk in vm.vmdks)
    except OSError:
        return True


def _is_writable(path: Path) -> bool:
    if not path.is_file() or not os.access(path, os.R_OK | os.W_OK):
        return False
    try:
        descriptor = os.open(path, os.O_RDWR)
        os.close(descriptor)
        return True
    except OSError:
        return False


def _disk_type_error(vm: VirtualMachine) -> str | None:
    supported = {"monolithicsparse", "twogbmaxextentsparse"}
    for disk in vm.vmdks:
        try:
            header = read_vmware_text(disk, 1024 * 1024)
        except OSError:
            continue
        match = re.search(r'^createType\s*=\s*"([^"]+)"', header, re.I | re.M)
        if not match:
            return f"Không xác định được loại VMDK: {disk.name}"
        if match.group(1).casefold() not in supported:
            return f"Loại VMDK không hỗ trợ compact an toàn: {match.group(1)}"
    return None


def validate_vm(
    vm: VirtualMachine,
    environment: VMwareEnvironment,
    running_vms: set[str] | None = None,
) -> ValidationResult:
    reasons: list[str] = []
    warnings: list[str] = []
    if not environment.cleanup_available:
        reasons.append("Không tìm thấy vmware-vdiskmanager.exe")
    if not vm.vmx_path.is_file():
        reasons.append("Không tìm thấy file VMX")
    if running_vms is None:
        reasons.append("Không xác minh được trạng thái Powered Off")
    elif _norm(vm.vmx_path) in running_vms:
        reasons.append("VM đang hoạt động")
    if not vm.vmdks:
        reasons.append("VMX không tham chiếu VMDK nào")
    missing = [p.name for p in vm.chain_files if not p.is_file()]
    if missing:
        reasons.append("Thiếu VMDK: " + ", ".join(missing))
    if _has_snapshots(vm):
        reasons.append("Phát hiện snapshot; ứng dụng không tự xóa snapshot")
    if _is_linked(vm):
        reasons.append("Phát hiện linked clone/parent VMDK")
    disk_type_error = _disk_type_error(vm)
    if disk_type_error:
        reasons.append(disk_type_error)
    if _has_locks(vm):
        reasons.append("Phát hiện file/thư mục khóa .lck")
    readonly = [p.name for p in vm.vmdks if p.exists() and not _is_writable(p)]
    if readonly:
        reasons.append("Không có quyền đọc/ghi VMDK: " + ", ".join(readonly))
    try:
        usage = os.statvfs(vm.vmx_path.parent) if os.name != "nt" else None
        free = usage.f_bavail * usage.f_frsize if usage else None
        if os.name == "nt":
            import shutil
            free = shutil.disk_usage(vm.vmx_path.parent).free
        allocated = sum(p.stat().st_size for p in vm.chain_files if p.is_file())
        required = max(1024**3, allocated)
        if free is not None and free < required:
            reasons.append(
                f"Dung lượng trống không đủ (cần dự phòng {required / 1024**3:.1f} GB)"
            )
    except OSError:
        reasons.append("Không xác minh được dung lượng trống")
    if vm.vmdks:
        warnings.append("Backend dùng vmware-vdiskmanager -k; không bảo đảm tương đương Clean Up Disks trong GUI")
    return ValidationResult(safe=not reasons, reasons=reasons, warnings=warnings)


def validate_gui_cleanup(
    vm: VirtualMachine,
    environment: VMwareEnvironment,
    running_vms: set[str] | None = None,
) -> ValidationResult:
    """Validate invariants before delegating disk-chain handling to Workstation GUI."""
    reasons: list[str] = []
    if not environment.vmware_exe or not environment.vmware_exe.is_file():
        reasons.append("Không tìm thấy VMware Workstation")
    if not vm.vmx_path.is_file():
        reasons.append("Không tìm thấy file VMX")
    if running_vms is None:
        reasons.append("Không xác minh được trạng thái Powered Off")
    elif _norm(vm.vmx_path) in running_vms:
        reasons.append("VM đang hoạt động")
    if not vm.vmdks:
        reasons.append("VMX không tham chiếu VMDK đang hoạt động nào")
    missing = [p.name for p in vm.chain_files if not p.is_file()]
    if missing:
        reasons.append("Thiếu VMDK đang hoạt động: " + ", ".join(missing))
    readonly = [p.name for p in vm.vmdks if p.exists() and not _is_writable(p)]
    if readonly:
        reasons.append("Không có quyền đọc/ghi VMDK: " + ", ".join(readonly))
    try:
        import shutil

        free = shutil.disk_usage(vm.vmx_path.parent).free
        if free < 1024**3:
            reasons.append("Dung lượng trống dưới mức dự phòng tối thiểu 1 GB")
    except OSError:
        reasons.append("Không xác minh được dung lượng trống")
    return ValidationResult(
        safe=not reasons,
        reasons=reasons,
        warnings=["Cleanup thật dùng UI Automation tuần tự qua VMware Workstation"],
    )

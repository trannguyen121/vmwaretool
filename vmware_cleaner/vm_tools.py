from __future__ import annotations

import re
import secrets
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .discovery import read_vmware_text
from .environment import CREATE_NO_WINDOW


ProgressCallback = Callable[[int, int, str], None]
LogCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class CloneSettings:
    template_vmx: Path
    output_root: Path
    snapshot_name: str
    vm_count: int = 1
    name_prefix: str = "Clone"
    cpu: int = 2
    ram_mb: int = 4096
    start_vms: bool = False

    def validate(self) -> None:
        if not self.template_vmx.is_file() or self.template_vmx.suffix.casefold() != ".vmx":
            raise ValueError(f"Không tìm thấy VMX mẫu hợp lệ: {self.template_vmx}")
        if not self.snapshot_name.strip():
            raise ValueError("Tên snapshot không được để trống.")
        if not self.name_prefix.strip():
            raise ValueError("Tiền tố tên VM không được để trống.")
        if re.search(r'[<>:"/\\|?*]', self.name_prefix):
            raise ValueError('Tiền tố tên VM chứa ký tự Windows không hợp lệ: < > : " / \\ | ? *')
        if not 1 <= self.vm_count <= 999:
            raise ValueError("Số lượng VM phải nằm trong khoảng 1–999.")
        if not 1 <= self.cpu <= 128:
            raise ValueError("Số CPU phải nằm trong khoảng 1–128.")
        if not 512 <= self.ram_mb <= 1048576:
            raise ValueError("RAM phải nằm trong khoảng 512–1.048.576 MB.")


def _run_vmrun(vmrun: Path, arguments: list[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    if not vmrun.is_file():
        raise FileNotFoundError(f"Không tìm thấy vmrun.exe: {vmrun}")
    try:
        result = subprocess.run(
            [str(vmrun), "-T", "ws", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("vmrun hết thời gian chờ.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"vmrun thất bại (mã {result.returncode}).")
    return result


def list_snapshots(vmrun: Path, vmx: Path) -> list[str]:
    result = _run_vmrun(vmrun, ["listSnapshots", str(vmx)], timeout=30)
    snapshots: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or re.match(r"^Total snapshots:\s*\d+$", line, re.I):
            continue
        snapshots.append(line)
    return snapshots


def list_running_vms(vmrun: Path) -> list[Path]:
    result = _run_vmrun(vmrun, ["list"], timeout=30)
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip().casefold().endswith(".vmx")]


def stop_vms(
    vmrun: Path,
    vms: Iterable[Path],
    mode: str,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> list[tuple[Path, str]]:
    if mode not in {"soft", "hard"}:
        raise ValueError("Chế độ tắt phải là soft hoặc hard.")
    items = list(vms)
    running = {str(path.resolve()).casefold() for path in list_running_vms(vmrun)}
    failures: list[tuple[Path, str]] = []
    for index, vmx in enumerate(items, 1):
        if cancel and cancel.is_set():
            break
        vmx = vmx.resolve()
        if not vmx.is_file():
            message = f"Bỏ qua {vmx.name}: entry Library không còn file VMX."
        elif str(vmx).casefold() not in running:
            message = f"Đã tắt sẵn: {vmx.name}"
        else:
            try:
                _run_vmrun(vmrun, ["stop", str(vmx), mode], timeout=120)
                message = f"Đã gửi lệnh {mode}: {vmx.name}"
            except (OSError, RuntimeError) as exc:
                message = f"Không tắt được {vmx.name}: {exc}"
                failures.append((vmx, str(exc)))
        if progress:
            progress(index, len(items), message)
    return failures


def delete_vms(
    vmrun: Path,
    vms: Iterable[Path],
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> list[tuple[Path, str]]:
    """Delete powered-off VMs through VMware's own deleteVM command."""
    items = list(vms)
    running = {str(path.resolve()).casefold() for path in list_running_vms(vmrun)}
    failures: list[tuple[Path, str]] = []
    for index, vmx in enumerate(items, 1):
        if cancel and cancel.is_set():
            break
        vmx = vmx.resolve()
        if str(vmx).casefold() in running:
            error = "VM đang chạy; hãy tắt VM trước khi xóa."
            failures.append((vmx, error))
            message = f"Bỏ qua {vmx.name}: {error}"
        elif not vmx.is_file():
            message = f"File VMX đã không còn; sẽ dọn hiển thị Library: {vmx.name}"
        else:
            try:
                _run_vmrun(vmrun, ["deleteVM", str(vmx)], timeout=300)
                message = f"Đã xóa VM: {vmx.stem}"
            except (OSError, RuntimeError) as exc:
                failures.append((vmx, str(exc)))
                message = f"Xóa thất bại {vmx.name}: {exc}"
        if progress:
            progress(index, len(items), message)
    return failures


def _set_vmx_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(r"^" + re.escape(key) + r"\s*=.*$", re.MULTILINE | re.IGNORECASE)
    line = f'{key} = "{value}"'
    if pattern.search(text):
        return pattern.sub(lambda _match: line, text)
    return text.rstrip() + "\n" + line + "\n"


def _remove_vmx_value(text: str, key: str) -> str:
    pattern = re.compile(r"^" + re.escape(key) + r"\s*=.*(?:\r?\n|$)", re.MULTILINE | re.IGNORECASE)
    return pattern.sub("", text)


def _static_mac(used: set[str]) -> str:
    # VMware permits manually assigned addresses in 00:50:56:00:00:00–00:50:56:3F:FF:FF.
    while True:
        tail = secrets.randbelow(0x400000)
        mac = f"00:50:56:{(tail >> 16) & 0x3F:02X}:{(tail >> 8) & 0xFF:02X}:{tail & 0xFF:02X}"
        if mac not in used:
            used.add(mac)
            return mac


def _configure_clone(vmx_path: Path, name: str, cpu: int, ram_mb: int, used_macs: set[str]) -> None:
    text = read_vmware_text(vmx_path)
    text = _set_vmx_value(text, "displayName", name)
    text = _set_vmx_value(text, "numvcpus", str(cpu))
    text = _set_vmx_value(text, "memsize", str(ram_mb))
    adapters = sorted({int(value) for value in re.findall(r"(?im)^ethernet(\d+)\.", text)} or {0})
    for adapter in adapters:
        prefix = f"ethernet{adapter}"
        text = _set_vmx_value(text, f"{prefix}.connectionType", "nat")
        text = _set_vmx_value(text, f"{prefix}.addressType", "static")
        text = _set_vmx_value(text, f"{prefix}.address", _static_mac(used_macs))
        text = _remove_vmx_value(text, f"{prefix}.generatedAddress")
        text = _remove_vmx_value(text, f"{prefix}.generatedAddressOffset")
    vmx_path.write_text(text, encoding="utf-8", newline="\n")


def create_linked_clones(
    vmrun: Path,
    settings: CloneSettings,
    progress: ProgressCallback | None = None,
    log: LogCallback | None = None,
    cancel: threading.Event | None = None,
) -> list[Path]:
    settings.validate()
    snapshots = list_snapshots(vmrun, settings.template_vmx)
    if settings.snapshot_name not in snapshots:
        available = ", ".join(snapshots) if snapshots else "không có"
        raise ValueError(f"Không thấy snapshot '{settings.snapshot_name}'. Snapshot hiện có: {available}.")
    template_key = str(settings.template_vmx.resolve()).casefold()
    running_keys = {str(path.resolve()).casefold() for path in list_running_vms(vmrun)}
    if template_key in running_keys:
        raise RuntimeError("VM mẫu đang chạy. Hãy tắt VM mẫu trước khi tạo linked clone.")

    settings.output_root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    used_macs: set[str] = set()
    for index in range(1, settings.vm_count + 1):
        if cancel and cancel.is_set():
            if log:
                log("Đã hủy; các clone tạo xong trước đó được giữ nguyên.")
            break
        name = f"{settings.name_prefix.strip()}-{index}"
        folder = settings.output_root / name
        destination = folder / f"{name}.vmx"
        if folder.exists():
            message = f"Bỏ qua {name}: thư mục đã tồn tại."
            if log:
                log(message)
            if progress:
                progress(index, settings.vm_count, message)
            continue
        folder.mkdir()
        try:
            _run_vmrun(
                vmrun,
                [
                    "clone", str(settings.template_vmx), str(destination), "linked",
                    f"-snapshot={settings.snapshot_name}", f"-cloneName={name}",
                ],
            )
            if not destination.is_file():
                raise RuntimeError("vmrun không tạo file VMX đích.")
            _configure_clone(destination, name, settings.cpu, settings.ram_mb, used_macs)
            if settings.start_vms:
                _run_vmrun(vmrun, ["start", str(destination), "nogui"], timeout=120)
            created.append(destination)
        except Exception:
            # This directory was created by this invocation and is safe to clean up on failure.
            shutil.rmtree(folder, ignore_errors=True)
            raise
        message = f"Đã tạo {name} ({settings.cpu} CPU, {settings.ram_mb} MB RAM, NAT)."
        if log:
            log(message)
        if progress:
            progress(index, settings.vm_count, message)
    return created

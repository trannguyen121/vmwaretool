from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable

from .environment import CREATE_NO_WINDOW


CONFIG_LINE = re.compile(r'^\s*(vmlist\d+)\.config\s*=\s*"(.*)"\s*$', re.I)
ENTRY_LINE = re.compile(r'^\s*(vmlist\d+)\.', re.I)


@dataclass(frozen=True, slots=True)
class LibraryRemovalResult:
    removed: int
    backup_path: Path | None


def is_workstation_running() -> bool:
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq vmware.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        # Fail closed: editing inventory while Workstation may be running can
        # cause VMware to overwrite the updated Library.
        return True
    return '"vmware.exe"' in result.stdout.casefold()


def close_workstation_gracefully(timeout: float = 20.0) -> bool:
    """Ask visible VMware Workstation windows to close via WM_CLOSE; never force-kill."""
    if not is_workstation_running():
        return True
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        wm_close = 0x0010
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def close_vmware_window(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if not user32.IsWindowVisible(hwnd):
                return True
            try:
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
                if not handle:
                    return True
                buffer = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buffer))
                ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
                ctypes.windll.kernel32.CloseHandle(handle)
                if Path(buffer.value).name.casefold() == "vmware.exe":
                    user32.PostMessageW(hwnd, wm_close, 0, 0)
            except OSError:
                pass
            return True

        callback = callback_type(close_vmware_window)
        user32.EnumWindows(callback, 0)
    except (AttributeError, OSError):
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_workstation_running():
            return True
        time.sleep(0.25)
    return not is_workstation_running()


def _path_key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def backup_inventory(inventory_path: Path) -> Path:
    if not inventory_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy VMware Library: {inventory_path}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup = inventory_path.with_name(f"{inventory_path.name}.backup-{stamp}")
    backup.write_bytes(inventory_path.read_bytes())
    return backup


def remove_from_library(inventory_path: Path, vmx_paths: Iterable[Path]) -> LibraryRemovalResult:
    if not inventory_path.is_file():
        raise FileNotFoundError(f"Không tìm thấy VMware Library: {inventory_path}")
    if is_workstation_running():
        raise RuntimeError("Hãy đóng hoàn toàn VMware Workstation trước khi sửa Library.")

    targets = {_path_key(path) for path in vmx_paths}
    data = inventory_path.read_bytes()
    encoding = "windows-1252"
    text = data.decode(encoding, errors="replace")
    lines = text.splitlines(keepends=True)

    prefixes: set[str] = set()
    for line in lines:
        match = CONFIG_LINE.match(line.rstrip("\r\n"))
        if match and match.group(2) and _path_key(match.group(2)) in targets:
            prefixes.add(match.group(1).casefold())
    if not prefixes:
        return LibraryRemovalResult(removed=0, backup_path=None)

    backup = backup_inventory(inventory_path)

    kept: list[str] = []
    for line in lines:
        match = ENTRY_LINE.match(line)
        if match and match.group(1).casefold() in prefixes:
            continue
        kept.append(line)
    updated = "".join(kept).encode(encoding, errors="replace")

    temp_name: str | None = None
    try:
        with NamedTemporaryFile(
            mode="wb", prefix="inventory-", suffix=".tmp",
            dir=inventory_path.parent, delete=False,
        ) as stream:
            temp_name = stream.name
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, inventory_path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    return LibraryRemovalResult(removed=len(prefixes), backup_path=backup)

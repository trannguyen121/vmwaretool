from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .models import VMwareEnvironment


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _registry_install_paths() -> list[Path]:
    paths: list[Path] = []
    try:
        import winreg

        keys = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\VMware, Inc.\VMware Workstation"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\VMware, Inc.\VMware Workstation"),
        )
        for hive, key_name in keys:
            try:
                with winreg.OpenKey(hive, key_name) as key:
                    for value_name in ("InstallPath", "InstallPath64"):
                        try:
                            value, _ = winreg.QueryValueEx(key, value_name)
                            paths.append(Path(value))
                        except OSError:
                            pass
            except OSError:
                pass
    except ImportError:
        pass
    return paths


def _registry_version() -> str | None:
    try:
        import winreg

        for key_name in (
            r"SOFTWARE\VMware, Inc.\VMware Workstation",
            r"SOFTWARE\WOW6432Node\VMware, Inc.\VMware Workstation",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_name) as key:
                    value, _ = winreg.QueryValueEx(key, "ProductVersion")
                    if value:
                        return str(value)
            except OSError:
                pass
    except ImportError:
        pass
    return None


def _candidate_dirs() -> list[Path]:
    result = _registry_install_paths()
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env_name)
        if root:
            result.append(Path(root) / "VMware" / "VMware Workstation")
    for command in ("vmware.exe", "vmrun.exe", "vmware-vdiskmanager.exe"):
        found = shutil.which(command)
        if found:
            result.append(Path(found).parent)
    unique: dict[str, Path] = {}
    for path in result:
        unique[str(path).casefold()] = path
    return list(unique.values())


def _version_from_cli(executable: Path | None) -> str:
    if not executable:
        return "Unknown"
    try:
        completed = subprocess.run(
            [str(executable)], capture_output=True, text=True, timeout=10,
            creationflags=CREATE_NO_WINDOW,
        )
        output = (completed.stdout + completed.stderr).strip()
        match = re.search(r"(?:version|build)\s+([\w. -]+)", output, re.I)
        return match.group(1).strip() if match else output.splitlines()[0][:100]
    except (OSError, subprocess.SubprocessError, IndexError):
        return "Unknown"


def detect_environment() -> VMwareEnvironment:
    install_dir = next((p for p in _candidate_dirs() if (p / "vmware.exe").is_file()), None)
    if install_dir is None:
        install_dir = next((p for p in _candidate_dirs() if p.is_dir()), None)

    def tool(name: str) -> Path | None:
        local = install_dir / name if install_dir else None
        if local and local.is_file():
            return local
        found = shutil.which(name)
        return Path(found) if found else None

    vmware = tool("vmware.exe")
    vmrun = tool("vmrun.exe")
    vdisk = tool("vmware-vdiskmanager.exe")
    appdata = os.environ.get("APPDATA")
    inventory = Path(appdata) / "VMware" / "inventory.vmls" if appdata else None
    return VMwareEnvironment(
        install_dir=install_dir,
        vmware_exe=vmware,
        vmrun_exe=vmrun,
        vdiskmanager_exe=vdisk,
        version=_registry_version() or _version_from_cli(vmrun),
        inventory_path=inventory if inventory and inventory.is_file() else None,
    )


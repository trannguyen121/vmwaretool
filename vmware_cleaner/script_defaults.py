from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ScriptDefaults:
    template_vmx: str = ""
    output_root: str = ""
    snapshot_name: str = ""
    vm_count: int = 1
    name_prefix: str = "Clone"
    cpu: int = 2
    ram_mb: int = 4096
    start_vms: bool = False
    stop_mode: str = "soft"


ASSIGNMENT = re.compile(r'^\s*\$(\w+)\s*=\s*(.+?)\s*(?:#.*)?$')


def _script_directory() -> Path | None:
    candidates = [Path(__file__).resolve().parent.parent / "script"]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.insert(0, Path(bundle_root) / "script")
    return next((path for path in candidates if path.is_dir()), None)


def _parse_value(raw: str) -> Any:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    if value.casefold() in {"$true", "$false"}:
        return value.casefold() == "$true"
    try:
        return int(value)
    except ValueError:
        return value


def _read_assignments(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    result: dict[str, Any] = {}
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            result[match.group(1)] = _parse_value(match.group(2))
    return result


def load_script_defaults() -> ScriptDefaults:
    directory = _script_directory()
    if directory is None:
        return ScriptDefaults()
    clone = _read_assignments(directory / "Create-VMware-LinkedClones-NAT.ps1")
    power = _read_assignments(directory / "PowerOff-All-VMware.ps1")
    return ScriptDefaults(
        template_vmx=str(clone.get("TemplateVMX", "")),
        output_root=str(clone.get("OutputRoot", "")),
        snapshot_name=str(clone.get("SnapshotName", "")),
        vm_count=int(clone.get("VMCount", 1)),
        name_prefix=str(clone.get("NamePrefix", "Clone")),
        cpu=int(clone.get("CPU", 2)),
        ram_mb=int(clone.get("RAM_MB", 4096)),
        start_vms=bool(clone.get("StartVMs", False)),
        stop_mode=str(power.get("StopMode", "soft")),
    )

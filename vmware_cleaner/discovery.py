from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from .models import VMwareEnvironment, VirtualMachine

LOG = logging.getLogger(__name__)
ASSIGNMENT = re.compile(r'^\s*([^#=]+?)\s*=\s*"(.*)"\s*$')
DISK_KEY = re.compile(r'^((?:ide|sata|scsi|nvme)\d+:\d+)\.fileName$', re.I)
EXTENT_LINE = re.compile(r'^\s*(?:RW|RDONLY|NOACCESS)\s+\d+\s+\w+\s+"([^"]+)"', re.I)


def read_vmware_text(path: Path, limit: int | None = None) -> str:
    if limit is None:
        data = path.read_bytes()
    else:
        with path.open("rb") as stream:
            data = stream.read(limit)
    for encoding in ("utf-8-sig", "windows-1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def parse_assignments(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in read_vmware_text(path).splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            values[match.group(1).strip()] = match.group(2)
    return values


def _resolve_disk(vmx_dir: Path, value: str) -> Path:
    candidate = Path(value.replace("/", os.sep))
    if not candidate.is_absolute():
        candidate = vmx_dir / candidate
    return candidate.resolve(strict=False)


def _descriptor_metadata(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        text = read_vmware_text(path, 1024 * 1024)
    except OSError:
        return {}
    metadata: dict[str, str] = {}
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            metadata[match.group(1).strip()] = match.group(2)
    return metadata


def resolve_vmdk_chain(descriptor: Path) -> frozenset[Path]:
    found: set[Path] = set()
    visited_descriptors: set[Path] = set()
    current = descriptor.resolve(strict=False)
    while current not in visited_descriptors:
        visited_descriptors.add(current)
        found.add(current)
        try:
            descriptor_text = read_vmware_text(current, 1024 * 1024)
            for line in descriptor_text.splitlines():
                extent = EXTENT_LINE.match(line)
                if extent:
                    found.add(_resolve_disk(current.parent, extent.group(1)))
        except OSError:
            pass
        metadata = _descriptor_metadata(current)
        parent = metadata.get("parentFileNameHint")
        if not parent:
            break
        current = _resolve_disk(current.parent, parent)
    return frozenset(found)


def _vm_size(vmx: Path) -> int:
    try:
        return sum(p.stat().st_size for p in vmx.parent.rglob("*") if p.is_file())
    except OSError:
        return 0


def _volume_id(path: Path) -> str:
    return (path.drive or path.anchor or str(path.parent)).casefold()


def vm_from_vmx(vmx: Path, inventory_name: str = "") -> VirtualMachine:
    values = parse_assignments(vmx)
    disks = []
    for key, value in values.items():
        match = DISK_KEY.match(key)
        if (
            match
            and values.get(f"{match.group(1)}.present", "").upper() == "TRUE"
            and value.lower().endswith(".vmdk")
        ):
            disk = _resolve_disk(vmx.parent, value)
            if disk not in disks:
                disks.append(disk)
    chain: set[Path] = set()
    for disk in disks:
        chain.update(resolve_vmdk_chain(disk))
    name = inventory_name or values.get("displayName") or vmx.stem
    volumes = frozenset(_volume_id(p) for p in chain or {vmx})
    return VirtualMachine(
        name=name, vmx_path=vmx.resolve(strict=False), vmdks=tuple(disks),
        chain_files=frozenset(chain), size_bytes=_vm_size(vmx), volume_ids=volumes,
    )


def discover_vms(environment: VMwareEnvironment) -> list[VirtualMachine]:
    if not environment.inventory_path:
        return []
    inventory = parse_assignments(environment.inventory_path)
    configs: list[tuple[str, str]] = []
    for key, value in inventory.items():
        match = re.fullmatch(r"(vmlist\d+)\.config", key, re.I)
        if match and value:
            configs.append((value, inventory.get(f"{match.group(1)}.DisplayName", "")))
    result: list[VirtualMachine] = []
    seen: set[str] = set()
    for raw_path, name in configs:
        vmx = Path(raw_path)
        key = str(vmx.resolve(strict=False)).casefold()
        if key in seen:
            continue
        seen.add(key)
        if not vmx.is_file():
            LOG.warning("VMX in inventory does not exist: %s", vmx)
            continue
        try:
            result.append(vm_from_vmx(vmx, name))
        except OSError as exc:
            LOG.exception("Cannot inspect %s: %s", vmx, exc)
    return sorted(result, key=lambda vm: vm.name.casefold())

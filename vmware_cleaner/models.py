from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CleanupStatus(str, Enum):
    WAITING = "Waiting"
    RUNNING = "Running"
    DONE = "Done"
    FAILED = "Failed"
    SKIPPED = "Skipped"


@dataclass(slots=True)
class VMwareEnvironment:
    install_dir: Path | None = None
    vmware_exe: Path | None = None
    vmrun_exe: Path | None = None
    vdiskmanager_exe: Path | None = None
    version: str = "Unknown"
    inventory_path: Path | None = None

    @property
    def cleanup_available(self) -> bool:
        return bool(self.vdiskmanager_exe and self.vdiskmanager_exe.is_file())


@dataclass(slots=True)
class VirtualMachine:
    name: str
    vmx_path: Path
    vmdks: tuple[Path, ...] = ()
    chain_files: frozenset[Path] = frozenset()
    size_bytes: int = 0
    volume_ids: frozenset[str] = frozenset()
    status: CleanupStatus = CleanupStatus.WAITING
    status_detail: str = ""

    @property
    def key(self) -> str:
        return str(self.vmx_path.resolve()).casefold()


@dataclass(slots=True)
class ValidationResult:
    safe: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CleanupResult:
    vm: VirtualMachine
    status: CleanupStatus
    message: str
    reclaimed_bytes: int = 0


from pathlib import Path
from types import SimpleNamespace

import vmware_cleaner.compact as compact_module
from vmware_cleaner.compact import CompactEngine
from vmware_cleaner.models import CleanupStatus, ValidationResult, VirtualMachine, VMwareEnvironment


def test_headless_cleanup_measures_before_and_after(monkeypatch, tmp_path: Path) -> None:
    cli = tmp_path / "vmware-vdiskmanager.exe"
    cli.write_bytes(b"fake")
    disk = tmp_path / "disk.vmdk"
    disk.write_bytes(b"x" * 1000)
    vmx = tmp_path / "demo.vmx"
    vmx.write_text('displayName="Demo"', encoding="utf-8")
    vm = VirtualMachine(
        name="Demo", vmx_path=vmx, vmdks=(disk,), chain_files=frozenset({disk})
    )
    monkeypatch.setattr(
        compact_module, "validate_vm", lambda *_args: ValidationResult(safe=True)
    )
    monkeypatch.setattr(compact_module, "list_running_vms", lambda *_args: set())

    def fake_run(command, **kwargs):
        assert command == [str(cli), "-k", str(disk)]
        assert kwargs["creationflags"] == compact_module.CREATE_NO_WINDOW
        disk.write_bytes(b"x" * 400)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(compact_module.subprocess, "run", fake_run)
    result = CompactEngine(VMwareEnvironment(vdiskmanager_exe=cli)).compact(vm)

    assert result.status is CleanupStatus.DONE
    assert result.before_bytes == 1000
    assert result.after_bytes == 400
    assert result.reclaimed_bytes == 600

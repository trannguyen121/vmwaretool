from pathlib import Path

from vmware_cleaner.models import VMwareEnvironment
from vmware_cleaner.safety import validate_gui_cleanup, validate_vm
from vmware_cleaner.discovery import vm_from_vmx


def make_vm(tmp_path: Path, disk_text: str = '# Disk DescriptorFile\n'):
    vmx = tmp_path / "safe.vmx"
    vmx.write_text(
        'displayName="Safe"\nscsi0:0.present="TRUE"\nscsi0:0.fileName="safe.vmdk"\n',
        encoding="utf-8",
    )
    (tmp_path / "safe.vmdk").write_text(disk_text, encoding="ascii")
    return vm_from_vmx(vmx)


def test_unknown_power_state_is_rejected(tmp_path: Path) -> None:
    vm = make_vm(tmp_path)
    env = VMwareEnvironment(vdiskmanager_exe=tmp_path / "missing.exe")
    result = validate_vm(vm, env, running_vms=None)
    assert not result.safe
    assert any("Powered Off" in reason for reason in result.reasons)


def test_lock_and_linked_clone_are_rejected(tmp_path: Path) -> None:
    vm = make_vm(tmp_path, 'parentFileNameHint="parent.vmdk"\n')
    (tmp_path / "vmware.log.lck").mkdir()
    cli = tmp_path / "vmware-vdiskmanager.exe"
    cli.write_bytes(b"fake")
    result = validate_vm(vm, VMwareEnvironment(vdiskmanager_exe=cli), running_vms=set())
    assert not result.safe
    assert any("linked clone" in reason for reason in result.reasons)
    assert any(".lck" in reason for reason in result.reasons)


def test_sparse_powered_off_vm_is_accepted(tmp_path: Path) -> None:
    vm = make_vm(tmp_path, 'createType="monolithicSparse"\n')
    cli = tmp_path / "vmware-vdiskmanager.exe"
    cli.write_bytes(b"fake")
    result = validate_vm(vm, VMwareEnvironment(vdiskmanager_exe=cli), running_vms=set())
    assert result.safe


def test_gui_cleanup_allows_linked_clone_and_vmware_lock(tmp_path: Path) -> None:
    vm = make_vm(tmp_path, 'createType="monolithicSparse"\nparentFileNameHint="parent.vmdk"\n')
    (tmp_path / "parent.vmdk").write_text('createType="monolithicSparse"\n', encoding="ascii")
    (tmp_path / "safe.vmx.lck").mkdir()
    vmware = tmp_path / "vmware.exe"
    vmware.write_bytes(b"fake")
    result = validate_gui_cleanup(
        vm, VMwareEnvironment(vmware_exe=vmware), running_vms=set()
    )
    assert result.safe

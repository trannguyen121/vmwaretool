from pathlib import Path

from vmware_cleaner.discovery import discover_vms, parse_assignments, resolve_vmdk_chain, vm_from_vmx
from vmware_cleaner.models import CleanupStatus, VMwareEnvironment


def test_parse_inventory_and_vmx(tmp_path: Path) -> None:
    vmx = tmp_path / "Demo.vmx"
    vmx.write_text(
        '.encoding = "UTF-8"\ndisplayName = "Demo VM"\nscsi0:0.present = "TRUE"\nscsi0:0.fileName = "disk.vmdk"\n',
        encoding="utf-8",
    )
    disk = tmp_path / "disk.vmdk"
    disk.write_text('# Disk DescriptorFile\ncreateType="monolithicSparse"\n', encoding="ascii")
    parsed = parse_assignments(vmx)
    vm = vm_from_vmx(vmx)
    assert parsed["displayName"] == "Demo VM"
    assert vm.name == "Demo VM"
    assert vm.vmdks == (disk.resolve(),)


def test_resolve_parent_chain(tmp_path: Path) -> None:
    parent = tmp_path / "parent.vmdk"
    parent.write_text('# Disk DescriptorFile\n', encoding="ascii")
    child = tmp_path / "child.vmdk"
    child.write_text('parentFileNameHint="parent.vmdk"\n', encoding="ascii")
    assert resolve_vmdk_chain(child) == frozenset({child.resolve(), parent.resolve()})


def test_discovery_keeps_missing_vmx_as_stale_library_entry(tmp_path: Path) -> None:
    missing = tmp_path / "Missing" / "Missing.vmx"
    inventory = tmp_path / "inventory.vmls"
    inventory.write_text(
        f'vmlist1.config = "{missing}"\n'
        'vmlist1.DisplayName = "Missing VM"\n',
        encoding="utf-8",
    )
    found = discover_vms(VMwareEnvironment(inventory_path=inventory))
    assert len(found) == 1
    assert found[0].name == "Missing VM"
    assert found[0].status == CleanupStatus.SKIPPED
    assert "xóa hiển thị" in found[0].status_detail


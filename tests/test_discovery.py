from pathlib import Path

from vmware_cleaner.discovery import parse_assignments, resolve_vmdk_chain, vm_from_vmx


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


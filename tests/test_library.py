from pathlib import Path

import pytest

import vmware_cleaner.library as library
from vmware_cleaner.library import remove_from_library


def test_remove_from_library_keeps_vm_files_and_creates_backup(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(library, "is_workstation_running", lambda: False)
    vm_one = tmp_path / "one" / "one.vmx"
    vm_two = tmp_path / "two" / "two.vmx"
    vm_one.parent.mkdir()
    vm_two.parent.mkdir()
    vm_one.write_text('displayName = "One"\n', encoding="utf-8")
    vm_two.write_text('displayName = "Two"\n', encoding="utf-8")
    inventory = tmp_path / "inventory.vmls"
    inventory.write_text(
        '.encoding = "windows-1252"\n'
        f'vmlist1.config = "{vm_one}"\n'
        'vmlist1.DisplayName = "One"\n'
        f'vmlist2.config = "{vm_two}"\n'
        'vmlist2.DisplayName = "Two"\n',
        encoding="windows-1252",
    )

    result = remove_from_library(inventory, [vm_one])

    assert result.removed == 1
    assert result.backup_path and result.backup_path.is_file()
    updated = inventory.read_text(encoding="windows-1252")
    assert "vmlist1." not in updated
    assert "vmlist2.config" in updated
    assert vm_one.is_file()
    assert vm_two.is_file()


def test_remove_from_library_requires_workstation_closed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(library, "is_workstation_running", lambda: True)
    inventory = tmp_path / "inventory.vmls"
    inventory.write_text('.encoding = "windows-1252"\n', encoding="windows-1252")
    with pytest.raises(RuntimeError, match="đóng hoàn toàn"):
        remove_from_library(inventory, [])


def test_graceful_close_returns_immediately_when_not_running(monkeypatch) -> None:
    monkeypatch.setattr(library, "is_workstation_running", lambda: False)
    assert library.close_workstation_gracefully() is True

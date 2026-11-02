from pathlib import Path

import pytest

import vmware_cleaner.vm_tools as vm_tools
from vmware_cleaner.vm_tools import CloneSettings, create_linked_clones, delete_vms, stop_vms


def test_clone_settings_rejects_invalid_prefix(tmp_path: Path) -> None:
    template = tmp_path / "template.vmx"
    template.write_text('displayName = "Template"\n', encoding="utf-8")
    settings = CloneSettings(template, tmp_path / "out", "Clean", name_prefix="bad/name")
    with pytest.raises(ValueError, match="không hợp lệ"):
        settings.validate()


def test_create_linked_clone_configures_vmx(monkeypatch, tmp_path: Path) -> None:
    template = tmp_path / "template.vmx"
    template.write_text('displayName = "Template"\n', encoding="utf-8")
    vmrun = tmp_path / "vmrun.exe"
    vmrun.write_bytes(b"")
    output = tmp_path / "clones"

    monkeypatch.setattr(vm_tools, "list_snapshots", lambda *_args: ["Clean"])
    monkeypatch.setattr(vm_tools, "list_running_vms", lambda *_args: [])

    def fake_run(_vmrun: Path, arguments: list[str], timeout=None):
        if arguments[0] == "clone":
            destination = Path(arguments[2])
            destination.write_text(
                'displayName = "Old"\n'
                'numvcpus = "1"\n'
                'memsize = "1024"\n'
                'ethernet0.connectionType = "bridged"\n'
                'ethernet0.generatedAddress = "00:0c:29:00:00:01"\n',
                encoding="utf-8",
            )

    monkeypatch.setattr(vm_tools, "_run_vmrun", fake_run)
    settings = CloneSettings(template, output, "Clean", 2, "Opera", 4, 6132)
    created = create_linked_clones(vmrun, settings)

    assert created == [
        output / "Opera-1" / "Opera-1.vmx",
        output / "Opera-2" / "Opera-2.vmx",
    ]
    text = created[0].read_text(encoding="utf-8")
    assert 'displayName = "Opera-1"' in text
    assert 'numvcpus = "4"' in text
    assert 'memsize = "6132"' in text
    assert 'ethernet0.connectionType = "nat"' in text
    assert 'ethernet0.addressType = "static"' in text
    assert 'ethernet0.address = "00:50:56:' in text
    assert "generatedAddress" not in text


def test_create_clone_rejects_running_template(monkeypatch, tmp_path: Path) -> None:
    template = tmp_path / "template.vmx"
    template.write_text('displayName = "Template"\n', encoding="utf-8")
    vmrun = tmp_path / "vmrun.exe"
    vmrun.write_bytes(b"")
    monkeypatch.setattr(vm_tools, "list_snapshots", lambda *_args: ["Clean"])
    monkeypatch.setattr(vm_tools, "list_running_vms", lambda *_args: [template])

    with pytest.raises(RuntimeError, match="đang chạy"):
        create_linked_clones(vmrun, CloneSettings(template, tmp_path / "out", "Clean"))


def test_delete_vms_uses_vmware_command(monkeypatch, tmp_path: Path) -> None:
    vmrun = tmp_path / "vmrun.exe"
    vmrun.write_bytes(b"")
    vmx = tmp_path / "Demo.vmx"
    vmx.write_text('displayName = "Demo"\n', encoding="utf-8")
    calls = []
    monkeypatch.setattr(vm_tools, "list_running_vms", lambda *_args: [])
    monkeypatch.setattr(
        vm_tools, "_run_vmrun",
        lambda executable, arguments, timeout=None: calls.append((executable, arguments, timeout)),
    )

    failures = delete_vms(vmrun, [vmx])

    assert failures == []
    assert calls == [(vmrun, ["deleteVM", str(vmx.resolve())], 300)]


def test_delete_vms_skips_running_vm(monkeypatch, tmp_path: Path) -> None:
    vmrun = tmp_path / "vmrun.exe"
    vmrun.write_bytes(b"")
    vmx = tmp_path / "Running.vmx"
    vmx.write_text('displayName = "Running"\n', encoding="utf-8")
    monkeypatch.setattr(vm_tools, "list_running_vms", lambda *_args: [vmx])
    monkeypatch.setattr(
        vm_tools, "_run_vmrun",
        lambda *_args, **_kwargs: pytest.fail("deleteVM must not run for a powered-on VM"),
    )

    failures = delete_vms(vmrun, [vmx])

    assert failures == [(vmx.resolve(), "VM đang chạy; hãy tắt VM trước khi xóa.")]


def test_delete_vms_treats_already_missing_vmx_as_cleanable(monkeypatch, tmp_path: Path) -> None:
    vmrun = tmp_path / "vmrun.exe"
    vmrun.write_bytes(b"")
    missing = tmp_path / "Missing.vmx"
    monkeypatch.setattr(vm_tools, "list_running_vms", lambda *_args: [])
    failures = delete_vms(vmrun, [missing])
    assert failures == []


def test_stop_vms_does_not_call_stop_for_powered_off_or_missing_vm(monkeypatch, tmp_path: Path) -> None:
    vmrun = tmp_path / "vmrun.exe"
    vmrun.write_bytes(b"")
    powered_off = tmp_path / "Off.vmx"
    powered_off.write_text('displayName = "Off"\n', encoding="utf-8")
    missing = tmp_path / "Missing.vmx"
    messages = []
    monkeypatch.setattr(vm_tools, "list_running_vms", lambda *_args: [])
    monkeypatch.setattr(
        vm_tools, "_run_vmrun",
        lambda *_args, **_kwargs: pytest.fail("stop must only run for powered-on VMs"),
    )

    failures = stop_vms(
        vmrun, [powered_off, missing], "soft",
        lambda _current, _total, message: messages.append(message),
    )

    assert failures == []
    assert messages == ["Đã tắt sẵn: Off.vmx", "Bỏ qua Missing.vmx: entry Library không còn file VMX."]


def test_stop_vms_calls_stop_only_for_running_vm(monkeypatch, tmp_path: Path) -> None:
    vmrun = tmp_path / "vmrun.exe"
    vmrun.write_bytes(b"")
    running = tmp_path / "Running.vmx"
    running.write_text('displayName = "Running"\n', encoding="utf-8")
    calls = []
    monkeypatch.setattr(vm_tools, "list_running_vms", lambda *_args: [running])
    monkeypatch.setattr(
        vm_tools, "_run_vmrun",
        lambda executable, arguments, timeout=None: calls.append((executable, arguments, timeout)),
    )

    failures = stop_vms(vmrun, [running], "hard")

    assert failures == []
    assert calls == [(vmrun, ["stop", str(running.resolve()), "hard"], 120)]

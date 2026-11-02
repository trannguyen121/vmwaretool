from vmware_cleaner.script_defaults import load_script_defaults


def test_load_defaults_from_powershell_scripts() -> None:
    defaults = load_script_defaults()
    assert defaults.template_vmx == r"D:\MayAo\Windows ATVGv3 3D Canvas 3333\Windows ATVGv3 1111.vmx"
    assert defaults.output_root == r"D:\MayAo\clone"
    assert defaults.snapshot_name == "New"
    assert defaults.vm_count == 80
    assert defaults.name_prefix == "Opera"
    assert defaults.cpu == 4
    assert defaults.ram_mb == 6132
    assert defaults.start_vms is False
    assert defaults.stop_mode == "hard"

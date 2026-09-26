from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "install" / "install_zwo_eaf_hid.sh"


def test_eaf_hid_helper_targets_only_zwo_eaf_hid_interface():
    source = HELPER.read_text(encoding="utf-8")

    assert 'ATTRS{idVendor}=="03c3"' in source
    assert 'ATTRS{idProduct}=="1f10"' in source
    assert 'ATTR{bInterfaceClass}=="03"' in source
    assert 'DRIVER==""' in source
    assert "/sys/bus/usb/drivers/usbhid/bind" in source


def test_eaf_hid_helper_preserves_existing_non_usbhid_driver():
    source = HELPER.read_text(encoding="utf-8")

    assert "refusing to steal ZWO EAF interface" in source
    assert 'if [[ "$driver" == "usbhid" ]]' in source


def test_eaf_hid_helper_repairs_connected_device_and_installs_hotplug_rule():
    source = HELPER.read_text(encoding="utf-8")

    assert "udevadm control --reload-rules" in source
    assert "for iface in /sys/bus/usb/devices/*:*" in source
    assert 'echo "$name" > "$USBHID_BIND"' in source
    assert 'ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_interface"' in source


def test_fresh_installer_invokes_eaf_hid_helper():
    installer = (ROOT / "install" / "install_solareclipse.sh").read_text(
        encoding="utf-8"
    )

    assert 'bash "$SCRIPT_DIR/install_zwo_eaf_hid.sh"' in installer


def test_dev_deploy_ships_but_does_not_execute_eaf_hid_helper():
    deploy = (ROOT / "tools" / "deploy-prod.sh").read_text(encoding="utf-8")

    assert '"$SRC/install/install_zwo_eaf_hid.sh"' in deploy
    assert '"$DST_HOST:$DST/install/install_zwo_eaf_hid.sh"' in deploy
    assert "sudo bash" not in deploy


def test_fresh_package_requires_eaf_hid_helper():
    source = (ROOT / "tools" / "build_fresh_install_package.py").read_text(
        encoding="utf-8"
    )

    assert '"install/install_zwo_eaf_hid.sh"' in source


def test_release_package_requires_eaf_hid_helper():
    source = (ROOT / "scripts" / "build_release_package.py").read_text(
        encoding="utf-8"
    )

    assert '"install/install_zwo_eaf_hid.sh"' in source


def test_dev_deploy_does_not_present_runtime_migration_as_normal_step():
    deploy = (ROOT / "tools" / "deploy-prod.sh").read_text(encoding="utf-8")

    assert "ONLY for installations that" in deploy
    assert "do not rerun it as a normal deploy step" in deploy
    assert "$ACTIVE_DST/install/install_zwo_eaf_hid.sh" in deploy

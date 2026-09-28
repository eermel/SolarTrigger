from tests.frontend_source import frontend_source
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = frontend_source()
APP = (ROOT / "flask_app/app.py").read_text(encoding="utf-8")
HELPER = (
    ROOT / "install" / "solartrigger-release-update"
).read_text(encoding="utf-8")


def test_devices_panel_has_vertical_spacing():
    assert ".devices-section {" in INDEX
    assert "flex-direction: column;" in INDEX
    assert "gap: 12px;" in INDEX


def test_devices_refresh_and_reset_use_shared_button_height():
    assert "--btn-h:     30px;" in INDEX
    assert ".btn {" in INDEX
    assert "height: var(--btn-h); min-height: var(--btn-h);" in INDEX

    assert "#devices-rescan," in INDEX
    assert "#erase-persistent-data-reboot {" in INDEX
    assert "padding: 0 10px;" in INDEX
    assert "font-size: 11px;" in INDEX


def test_devices_has_destructive_persistent_reset_button():
    assert 'id="erase-persistent-data-reboot"' in INDEX
    assert "⚠ ERASE ALL PERSISTENT DATA &amp; REBOOT ⚠" in INDEX
    assert 'onclick="erasePersistentDataAndReboot()"' in INDEX


def test_reset_requires_confirmation():
    assert "function erasePersistentDataAndReboot()" in INDEX
    assert "confirm(" in INDEX
    assert "PERSISTENT DATA" in INDEX
    assert "REBOOT" in INDEX


def test_backend_has_reset_and_reboot_endpoint():
    assert '@app.route("/api/system/erase-persistent-data-and-reboot"' in APP
    assert '"erase-reboot"' in APP
    assert "RELEASE_HELPER" in APP
    assert '["sudo", "-n", "/usr/bin/systemctl", "reboot"]' not in APP


def test_reset_targets_only_shared_application_var_in_root_helper():
    assert 'reset_persistent_data_and_reboot()' in HELPER
    assert '[[ "$SHARED_VAR" == "$BASE/var" ]]' in HELPER
    assert 'preserved_top = {"tls"}' in HELPER
    assert '"camera_profiles"' in HELPER
    assert '"camera_timing"' in HELPER
    assert '"camera_characterization"' in HELPER
    assert 'TRIGGER_DIR / "configs" / "rig"' not in APP
    assert 'path.name == "dryrun_short.json"' not in APP

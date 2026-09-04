from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app/templates/index.html").read_text(encoding="utf-8")
APP = (ROOT / "flask_app/app.py").read_text(encoding="utf-8")


def test_devices_panel_has_vertical_spacing():
    assert ".devices-section {" in INDEX
    assert "flex-direction: column;" in INDEX
    assert "gap: 12px;" in INDEX


def test_devices_refresh_matches_compact_gps_buttons():
    assert "#devices-rescan," in INDEX
    assert "padding: 7px 10px;" in INDEX
    assert "font-size: 11px;" in INDEX
    assert "min-height: 0;" in INDEX


def test_devices_has_destructive_persistent_reset_button():
    assert 'id="erase-persistent-data-reboot"' in INDEX
    assert "⚠ ERASE ALL PERSISTANT DATA &amp; REBOOT ⚠" in INDEX
    assert 'onclick="erasePersistentDataAndReboot()"' in INDEX


def test_reset_requires_confirmation():
    assert "function erasePersistentDataAndReboot()" in INDEX
    assert "confirm(" in INDEX
    assert "ERASE ALL PERSISTANT DATA & REBOOT" in INDEX


def test_backend_has_reset_and_reboot_endpoint():
    assert '@app.route("/api/system/erase-persistent-data-and-reboot"' in APP
    assert "def _erase_all_persistent_data():" in APP
    assert '["sudo", "-n", "/usr/bin/systemctl", "reboot"]' in APP

def test_reset_targets_only_application_var():
    assert "reset_application_var(VAR_DIR)" in APP
    assert 'TRIGGER_DIR / "configs" / "rig"' not in APP
    assert 'path.name == "dryrun_short.json"' not in APP

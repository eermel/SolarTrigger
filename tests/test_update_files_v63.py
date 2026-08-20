from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "install" / "update_files.sh").read_text(encoding="utf-8")


def test_fast_update_deploys_all_v6_layers():
    assert 'sync_app_dir "backend"' in SCRIPT
    assert 'sync_app_dir "services"' in SCRIPT
    assert 'sync_app_dir "plugins"' in SCRIPT


def test_fast_update_restarts_flask_and_checks_active():
    assert "systemctl restart solareclipse.service" in SCRIPT
    assert "systemctl is-active --quiet solareclipse.service" in SCRIPT


def test_fast_update_writes_deployed_version_markers():
    assert '"$TRIGGER_DIR/VERSION"' in SCRIPT
    assert '"$FLASK_DIR/VERSION"' in SCRIPT


def test_fast_update_validates_critical_python_files():
    assert '"$TRIGGER_DIR/backend/trigger_service.py"' in SCRIPT
    assert '"$TRIGGER_DIR/services/gps_service.py"' in SCRIPT
    assert '"$TRIGGER_DIR/eclipse_trigger.py"' in SCRIPT

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "install" / "update_files.sh").read_text(encoding="utf-8")


def test_fast_update_deploys_application_layers():
    assert 'sync_app_dir "backend"' in SCRIPT
    assert 'sync_app_dir "services"' in SCRIPT
    assert 'sync_app_dir "plugins"' in SCRIPT


def test_fast_update_restarts_flask_and_checks_active():
    assert "systemctl restart solareclipse.service" in SCRIPT
    assert "systemctl is-active --quiet solareclipse.service" in SCRIPT


def test_fast_update_writes_build_commit_marker():
    assert '"$APP_DIR/BUILD_COMMIT"' in SCRIPT
    assert "PACKAGE_VERSION" not in SCRIPT


def test_fast_update_validates_critical_python_files():
    assert '"$APP_DIR/backend/trigger_service.py"' in SCRIPT
    assert '"$APP_DIR/services/gps_service.py"' in SCRIPT
    assert '"$APP_DIR/scripts/eclipse_trigger.py"' in SCRIPT
    assert '"$APP_DIR/scripts/camera_ipc_client.py"' in SCRIPT
    assert '"$APP_DIR/scripts/fanout_camera_adapter.py"' in SCRIPT

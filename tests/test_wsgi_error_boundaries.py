from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "install" / "install_solareclipse.sh").read_text(encoding="utf-8")

def test_background_threads_are_idempotent():
    assert "_background_threads_lock = threading.Lock()" in APP
    assert "_background_threads_started = False" in APP
    assert "if _background_threads_started:" in APP
    assert "_background_threads_started = True" in APP

def test_wsgi_starts_background_threads():
    assert "from app import app, socketio, start_background_threads" in INSTALLER
    assert "start_background_threads()" in INSTALLER

def test_trigger_error_boundaries_exist():
    assert '"TRIGGER_SIMULATION_FAILED"' in APP
    assert '"TRIGGER_DRYRUN_FAILED"' in APP
    assert '"DEBUG_START_FAILED"' in APP
    assert '"error": "Trigger simulation failed."' in APP
    assert '"error": "Trigger dry-run failed."' in APP
    assert '"error": "DEBUG start failed."' in APP

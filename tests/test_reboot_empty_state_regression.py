from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_rig_migration_handles_missing_active_circumstances_file():
    source = (ROOT / "backend/rig_config.py").read_text(encoding="utf-8")

    assert 'active_file = circumstances_state.get("active_file")' in source
    assert "if active_file:" in source
    assert 'configs_path / "circumstances" / active_file' in source


def test_socketio_connect_accepts_auth_argument():
    source = (ROOT / "flask_app/app.py").read_text(encoding="utf-8")

    assert '@socketio.on("connect")' in source
    assert "def on_connect(auth=None):" in source

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_flask_uses_runtime_gps_config_path():
    text = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")
    assert 'GPS_CONFIG_FILE = TRIGGER_DIR / "configs" / "gps_default.json"' in text
    assert 'Path.home() / "configs" / "gps_default.json"' not in text

def test_update_deploys_configs_into_trigger_runtime():
    text = (ROOT / "install" / "update_files.sh").read_text(encoding="utf-8")
    assert 'CONFIGS_DIR="$TRIGGER_DIR/configs"' in text
    assert 'cp -a "$PACKAGE_DIR/configs" "$TRIGGER_DIR/"' in text
    assert 'CONFIGS_DIR="$USER_HOME/configs"' not in text

def test_full_install_deploys_configs_into_trigger_runtime():
    text = (ROOT / "install" / "install_solareclipse.sh").read_text(encoding="utf-8")
    assert 'CONFIGS_DIR="$TRIGGER_DIR/configs"' in text
    assert 'cp -a "$PACKAGE_DIR/configs" "$TRIGGER_DIR/"' in text
    assert 'CONFIGS_DIR="$USER_HOME/configs"' not in text

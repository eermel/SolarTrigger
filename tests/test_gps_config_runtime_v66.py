from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_flask_uses_runtime_gps_config_path():
    text = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")

    assert 'GPS_CONFIG_FILE = TRIGGER_DIR / "configs" / "gps_default.json"' in text
    assert 'Path.home() / "configs" / "gps_default.json"' not in text


def test_update_preserves_runtime_configs():
    text = (ROOT / "install" / "update_files.sh").read_text(encoding="utf-8")

    assert 'CONFIGS_DIR="$APP_DIR/configs"' in text
    assert 'mkdir -p "$CONFIGS_DIR"' in text
    assert 'mkdir -p "$CONFIGS_DIR/circumstances"' in text
    assert 'mkdir -p "$CONFIGS_DIR/camera_cfg"' in text
    assert 'mkdir -p "$CONFIGS_DIR/camera_timing"' in text

    # Une mise à jour ne doit pas écraser en masse les configs runtime.
    assert 'cp -a "$PACKAGE_DIR/configs/." "$CONFIGS_DIR/"' not in text
    assert 'rm -rf "$CONFIGS_DIR"' not in text

    assert (
        'cp -a "$PACKAGE_DIR/configs/camera_timing/." '
        '"$CONFIGS_DIR/camera_timing/"'
        in text
    )


def test_full_install_deploys_initial_configs_into_application_root():
    text = (
        ROOT / "install" / "install_solareclipse.sh"
    ).read_text(encoding="utf-8")

    assert 'CONFIGS_DIR="$APP_DIR/configs"' in text
    assert 'cp -a "$PACKAGE_DIR/configs/." "$CONFIGS_DIR/"' in text
    assert 'CONFIGS_DIR="$USER_HOME/configs"' not in text

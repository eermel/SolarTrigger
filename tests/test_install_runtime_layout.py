from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (
    ROOT / "install" / "install_solareclipse.sh"
).read_text(encoding="utf-8")


RUNTIME_SCRIPTS = (
    "__init__.py",
    "camera_ipc_client.py",
    "eclipse_calculator_py.py",
    "eclipse_trigger.py",
    "fanout_camera_adapter.py",
    "gps_sync.py",
)


def test_installer_does_not_install_unused_socketio_client_dependencies():
    assert "requests \\" not in INSTALLER
    assert "python-socketio[client]" not in INSTALLER

    # Le serveur Socket.IO et son transport WebSocket restent requis.
    assert "flask-socketio" in INSTALLER
    assert "simple-websocket" in INSTALLER


def test_installer_uses_build_commit_not_manual_version():
    assert "BUILD_COMMIT" in INSTALLER
    assert "v6.0" not in INSTALLER

    # VERSION n'est plus une source de métadonnée.
    assert '"$PACKAGE_DIR/VERSION"' not in INSTALLER
    assert "PACKAGE_VERSION" not in INSTALLER

    # Une ancienne installation doit être nettoyée.
    assert 'rm -f "$APP_DIR/VERSION"' in INSTALLER


def test_installer_uses_one_application_root():
    assert 'APP_DIR="$USER_HOME/solar-eclipse-trigger-prod"' in INSTALLER
    assert 'VENV_DIR="$APP_DIR/venv"' in INSTALLER
    assert "python_solareclipsetrigger" not in INSTALLER
    assert "flaskapp_solareclipsetrigger" not in INSTALLER


def test_installer_preserves_runtime_scripts_directory():
    assert 'SCRIPTS_DIR="$APP_DIR/scripts"' in INSTALLER

    # The source scripts directory must not be flattened into APP_DIR.
    assert 'cp "$PACKAGE_DIR/scripts/"*.py "$APP_DIR/"' not in INSTALLER
    assert 'cp "$PACKAGE_DIR/scripts/"*.py "$TRIGGER_DIR/"' not in INSTALLER

    for filename in RUNTIME_SCRIPTS:
        assert filename in INSTALLER


def test_installer_recreates_runtime_scripts_directory_after_preflight():
    cleanup = 'rm -rf "$SCRIPTS_DIR"'
    missing = 'error "Script runtime manquant : $src"'
    copy = 'cp "$src" "$SCRIPTS_DIR/$script"'

    assert cleanup in INSTALLER
    assert missing in INSTALLER
    assert copy in INSTALLER

    # Ne jamais détruire le runtime existant avant validation du package.
    assert INSTALLER.index(missing) < INSTALLER.index(cleanup)
    assert INSTALLER.index(cleanup) < INSTALLER.index(copy)


def test_installer_does_not_install_dev_only_assets():
    assert '"$PACKAGE_DIR/jubier_files"' not in INSTALLER
    assert 'cp -r "$PACKAGE_DIR/tests"' not in INSTALLER

    for filename in (
        "eclipse_calculator_jubier.py",
        "eclipse_dataset_builder.py",
        "eclipse_dataset_diff.py",
        "generate_debug_partial.py",
        "generate_debug_realistic.py",
        "generate_debug_total.py",
        "measure_camera_wakeup.py",
        "measure_sony_bracket_timing.py",
        "totality_only.py",
    ):
        assert filename not in INSTALLER


def test_installer_places_flask_runtime_in_application_root():
    assert 'cp "$PACKAGE_DIR/flask_app/app.py" "$APP_DIR/app.py"' in INSTALLER
    assert (
        'cp "$PACKAGE_DIR/flask_app/templates/index.html" '
        '"$APP_DIR/templates/index.html"'
    ) in INSTALLER

    assert 'mkdir -p "$APP_DIR/templates"' in INSTALLER
    assert 'mkdir -p "$APP_DIR/static/sounds"' in INSTALLER


def test_installer_systemd_service_uses_application_root():
    assert "WorkingDirectory=$APP_DIR" in INSTALLER
    assert 'Environment="PYTHONPATH=$APP_DIR"' in INSTALLER
    assert "ExecStart=$VENV_DIR/bin/gunicorn" in INSTALLER
    assert "wsgi:app" in INSTALLER


def test_installer_does_not_create_legacy_trigger_service():
    assert (
        "cat > /etc/systemd/system/solareclipse-trigger.service"
        not in INSTALLER
    )


def test_installer_deploys_runtime_data_and_sounds_under_application_root():
    assert 'CONFIGS_DIR="$APP_DIR/configs"' in INSTALLER
    assert 'SOUNDS_DIR="$APP_DIR/Sounds"' in INSTALLER

    assert (
        'sync_eclipse_datasets "$PACKAGE_DIR" "$APP_DIR"'
        in INSTALLER
    )

    assert '"$APP_DIR/static/sounds"' in INSTALLER


def test_installer_has_no_legacy_shell_directory_aliases():
    assert 'TRIGGER_DIR="$APP_DIR"' not in INSTALLER
    assert 'FLASK_DIR="$APP_DIR"' not in INSTALLER

    assert "$TRIGGER_DIR" not in INSTALLER
    assert "$FLASK_DIR" not in INSTALLER


def test_installer_uses_application_root_for_wsgi_and_static_files():
    assert 'cat > "$APP_DIR/wsgi.py" <<EOL' in INSTALLER
    assert "alias $APP_DIR/static/;" in INSTALLER


def test_installer_does_not_create_unused_uwsgi_configuration():
    assert "uwsgi.ini" not in INSTALLER
    assert "uWSGI" not in INSTALLER
    assert "eventlet" not in INSTALLER
    assert "simple-websocket" in INSTALLER

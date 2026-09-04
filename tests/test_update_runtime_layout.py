from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPDATE = (
    ROOT / "install" / "update_files.sh"
).read_text(encoding="utf-8")


RUNTIME_SCRIPTS = (
    "__init__.py",
    "camera_ipc_client.py",
    "eclipse_calculator_py.py",
    "eclipse_trigger.py",
    "fanout_camera_adapter.py",
    "gps_sync.py",
)


def test_update_uses_single_application_root():
    assert 'APP_DIR="$USER_HOME/solar-eclipse-trigger-prod"' in UPDATE
    assert 'CONFIGS_DIR="$APP_DIR/configs"' in UPDATE

    assert "python_solareclipsetrigger" not in UPDATE
    assert "flaskapp_solareclipsetrigger" not in UPDATE


def test_update_supports_isolated_application_root_in_test_mode():
    assert "SOLARECLIPSE_TEST_APP_DIR" in UPDATE


def test_update_deploys_only_runtime_scripts():
    assert 'SCRIPTS_DIR="$APP_DIR/scripts"' in UPDATE

    for filename in RUNTIME_SCRIPTS:
        assert filename in UPDATE

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
        assert filename not in UPDATE


def test_update_does_not_deploy_tests_or_jubier():
    assert '"$PACKAGE_DIR/tests"' not in UPDATE
    assert '"$PACKAGE_DIR/jubier_files"' not in UPDATE


def test_update_deploys_application_layers_to_same_root():
    assert 'sync_app_dir "backend"' in UPDATE
    assert 'sync_app_dir "services"' in UPDATE
    assert 'sync_app_dir "plugins"' in UPDATE

    assert (
        'sync_eclipse_datasets "$PACKAGE_DIR" "$APP_DIR"'
        in UPDATE
    )


def test_update_preserves_runtime_configuration():
    assert 'mkdir -p "$CONFIGS_DIR"' in UPDATE
    assert 'mkdir -p "$CONFIGS_DIR/circumstances"' in UPDATE
    assert 'mkdir -p "$CONFIGS_DIR/camera_cfg"' in UPDATE
    assert 'mkdir -p "$CONFIGS_DIR/camera_timing"' in UPDATE

    # Runtime/user configuration must never be overwritten wholesale.
    assert 'cp -a "$PACKAGE_DIR/configs/." "$CONFIGS_DIR/"' not in UPDATE
    assert 'rm -rf "$CONFIGS_DIR"' not in UPDATE

    # Only immutable camera timing profiles are refreshed.
    assert (
        'cp -a "$PACKAGE_DIR/configs/camera_timing/." '
        '"$CONFIGS_DIR/camera_timing/"'
        in UPDATE
    )


def test_update_places_flask_and_sounds_in_application_root():
    assert (
        'cp -a "$PACKAGE_DIR/flask_app/app.py" "$APP_DIR/app.py"'
        in UPDATE
    )
    assert (
        'cp -a "$PACKAGE_DIR/flask_app/templates/index.html" '
        '"$APP_DIR/templates/index.html"'
        in UPDATE
    )

    assert '"$APP_DIR/Sounds"' in UPDATE
    assert '"$APP_DIR/static/sounds"' in UPDATE


def test_update_validates_current_runtime_files():
    assert '"$APP_DIR/backend/trigger_service.py"' in UPDATE
    assert '"$APP_DIR/services/gps_service.py"' in UPDATE
    assert '"$APP_DIR/scripts/eclipse_trigger.py"' in UPDATE
    assert '"$APP_DIR/scripts/camera_ipc_client.py"' in UPDATE
    assert '"$APP_DIR/scripts/fanout_camera_adapter.py"' in UPDATE


def test_update_restarts_and_checks_main_service():
    assert "systemctl restart solareclipse.service" in UPDATE
    assert "systemctl is-active --quiet solareclipse.service" in UPDATE

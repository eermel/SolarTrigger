from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (
    ROOT / "install" / "install_solareclipse.sh"
).read_text(encoding="utf-8")
DEPLOY = (
    ROOT / "tools" / "deploy-prod.sh"
).read_text(encoding="utf-8")


def test_installer_defines_application_var():
    assert 'VAR_DIR="$APP_DIR/var"' in INSTALLER


def test_installer_creates_complete_var_layout():
    expected = (
        '$VAR_DIR/state',
        '$VAR_DIR/logs',
        '$VAR_DIR/generated/rig',
        '$VAR_DIR/generated/camera_cfg',
        '$VAR_DIR/generated/circumstances',
        '$VAR_DIR/generated/photo_cfg',
        '$VAR_DIR/generated/exposure_opt',
        '$VAR_DIR/generated/sequence',
        '$VAR_DIR/generated/execution_plan',
    )

    for path in expected:
        assert f'"{path}"' in INSTALLER


def test_installer_does_not_delete_application_var():
    assert 'rm -rf "$VAR_DIR"' not in INSTALLER


def test_installer_replaces_product_configs():
    assert 'rm -rf "$CONFIGS_DIR"' in INSTALLER
    assert 'cp -a "$PACKAGE_DIR/configs/." "$CONFIGS_DIR/"' in INSTALLER


def test_deploy_synchronizes_complete_product_configs():
    assert '"$SRC/configs/"' in DEPLOY
    assert '"$DST_HOST:$DST/configs/"' in DEPLOY
    assert 'rsync "${RSYNC_OPTS[@]}" --delete \\' in DEPLOY


def test_deploy_never_synchronizes_var():
    assert '"$SRC/var' not in DEPLOY
    assert '"$DST_HOST:$DST/var' not in DEPLOY


def test_deploy_documents_var_as_persistent():
    assert (
        'var/   (all persistent/generated/runtime application data)'
        in DEPLOY
    )
    assert 'var/ is never synchronized or deleted.' in DEPLOY

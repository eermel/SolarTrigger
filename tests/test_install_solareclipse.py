from pathlib import Path


INSTALLER_PATH = (
    Path(__file__).resolve().parents[1] / "install" / "install_solareclipse.sh"
)


def test_installer_does_not_reference_browser_dependencies():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8").lower()

    assert "chromium" not in installer_source
    assert "chromium-driver" not in installer_source
    assert "playwright" not in installer_source


def test_installer_generates_runtime_path_wrappers():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")

    gps_start = 'cat > "$BIN_DIR/sync_gps.sh" <<EOL'
    gps_source = installer_source.split(gps_start, maxsplit=1)[1].split(
        "\nEOL", maxsplit=1
    )[0]

    calculator_start = 'cat > "$BIN_DIR/calcul_eclipse.sh" <<EOL'
    calculator_source = installer_source.split(
        calculator_start,
        maxsplit=1,
    )[1].split("\nEOL", maxsplit=1)[0]

    assert (
        'sudo "$VENV_DIR/bin/python3" '
        '"$SCRIPTS_DIR/gps_sync.py" "\\$@"'
        in gps_source
    )
    assert (
        '"$VENV_DIR/bin/python3" '
        '"$SCRIPTS_DIR/eclipse_calculator_py.py" "\\$@"'
        in calculator_source
    )


def test_installer_does_not_generate_direct_trigger_wrapper():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")

    assert 'cat > "$BIN_DIR/trigger_eclipse.sh"' not in installer_source



def test_installer_uses_versioned_release_symlink_layout():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")

    assert 'INSTALL_BASE="$USER_HOME/solartrigger"' in installer_source
    assert 'RELEASES_DIR="$INSTALL_BASE/releases"' in installer_source
    assert 'ACTIVE_LINK="$USER_HOME/solar-eclipse-trigger-prod"' in installer_source
    assert 'ln -s "$RELEASE_DIR" "$ACTIVE_LINK"' in installer_source
    assert 'ln -s "$VAR_DIR" "$RELEASE_DIR/var"' in installer_source
    assert 'ln -s "$VENV_DIR" "$RELEASE_DIR/venv"' in installer_source


def test_installer_installs_maintenance_helpers_and_sudoers():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")

    assert 'for HELPER in solartrigger-system-update solartrigger-release-update' in installer_source
    assert '"/usr/local/sbin/$HELPER"' in installer_source
    assert (
        '$CURRENT_USER ALL=(root) NOPASSWD: '
        '/usr/local/sbin/solartrigger-system-update'
        in installer_source
    )
    assert (
        '$CURRENT_USER ALL=(root) NOPASSWD: '
        '/usr/local/sbin/solartrigger-release-update *'
        in installer_source
    )

def test_installer_uses_central_indi_service():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")

    assert "solartrigger-indi.service" in installer_source
    assert "backend.indi_server_daemon" in installer_source
    assert "configs/indi_default.json" in installer_source
    assert "After=network.target local-fs.target solartrigger-indi.service" in installer_source
    assert "indiserver-eqmod.service" in installer_source  # cleanup only
    assert "Description=INDI server (EQMod)" not in installer_source


def test_installer_keeps_zwo_indi_package_optional():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")

    assert "apt-cache show indi-asi" in installer_source
    assert "apt install -y indi-asi" in installer_source



def test_installer_checks_a7v_without_python_binding_before_venv():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")
    step3b = installer_source.split(
        'step "STEP 3b — Build libgphoto2 from git (Sony A7V)"', 1
    )[1].split(
        'step "STEP 3c — ZWO EAF SDK (focuser, optional)"', 1
    )[0]

    assert "import gphoto2" not in step3b
    assert "print-camera-list human-readable" in step3b
    assert "gphoto2 --list-cameras" in step3b


def test_installer_makes_install_base_writable_before_user_venv_creation():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")
    chown_pos = installer_source.index(
        'chown "$CURRENT_USER:$CURRENT_USER" "$INSTALL_BASE"'
    )
    venv_pos = installer_source.index(
        'sudo -u "$CURRENT_USER" HOME="$USER_HOME" python3 -m venv "$VENV_DIR"'
    )

    assert chown_pos < venv_pos

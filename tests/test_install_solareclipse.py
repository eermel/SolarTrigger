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

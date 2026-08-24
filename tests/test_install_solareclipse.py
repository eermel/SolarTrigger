from pathlib import Path


INSTALLER_PATH = (
    Path(__file__).resolve().parents[1] / "install" / "install_solareclipse.sh"
)


def test_installer_does_not_reference_browser_dependencies():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8").lower()

    assert "chromium" not in installer_source
    assert "chromium-driver" not in installer_source
    assert "playwright" not in installer_source


def test_installer_generates_python_eclipse_calculator_wrapper():
    installer_source = INSTALLER_PATH.read_text(encoding="utf-8")

    wrapper_start = 'cat > "$BIN_DIR/calcul_eclipse.sh" <<EOL'
    wrapper_source = installer_source.split(wrapper_start, maxsplit=1)[1].split(
        "\nEOL", maxsplit=1
    )[0]

    assert 'python3 eclipse_calculator_py.py "\\$@"' in wrapper_source

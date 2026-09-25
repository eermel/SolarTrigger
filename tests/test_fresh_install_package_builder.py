from pathlib import Path
import zipfile

from tools import build_fresh_install_package


ROOT = Path(__file__).resolve().parents[1]


def test_fresh_install_zip_embeds_validation_persistence_layout(tmp_path):
    output = tmp_path / "solartrigger-install-1.0.0.zip"

    build_fresh_install_package.build_fresh_install(
        ROOT,
        output,
        "1.0.0",
        build_commit="f" * 40,
    )

    prefix = "SolarTrigger-1.0.0/"
    with zipfile.ZipFile(output) as archive:
        installer = archive.read(
            prefix + "install/install_solareclipse.sh"
        ).decode("utf-8")
        helper = archive.read(
            prefix + "install/solartrigger-release-update"
        ).decode("utf-8")
        runtime_paths = archive.read(
            prefix + "backend/runtime_paths.py"
        ).decode("utf-8")

    assert (
        '"$VAR_DIR/generated/camera_characterization/validation"'
        in installer
    )
    assert (
        'validation_dir="$SHARED_CAMERA_CHARACTERIZATION/validation"'
        in helper
    )
    assert (
        'CAMERA_VALIDATION_DIR = CAMERA_CHARACTERIZATION_DIR / "validation"'
        in runtime_paths
    )

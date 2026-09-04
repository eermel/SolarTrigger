"""CI-level functional coverage for eclipse dataset deployment."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DATASETS_SYNC = REPOSITORY_ROOT / "install" / "datasets_sync.sh"
UPDATE_FILES = REPOSITORY_ROOT / "install" / "update_files.sh"
KNOWN_DATASET = "2025-03-29.json"


def run_dataset_sync(package_dir: Path, trigger_dir: Path) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        SOLARECLIPSE_TEST_PACKAGE_DIR=str(package_dir),
        SOLARECLIPSE_TEST_TRIGGER_DIR=str(trigger_dir),
    )
    return subprocess.run(
        ["bash", str(DATASETS_SYNC)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def write_package_registry(package_dir: Path, registry: object) -> Path:
    datasets_dir = package_dir / "data" / "eclipses"
    datasets_dir.mkdir(parents=True)
    (datasets_dir / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    return datasets_dir


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}".lower()


def test_dataset_sync_creates_destination_and_copies_registry_and_dataset(tmp_path: Path) -> None:
    trigger_dir = tmp_path / "absent-trigger"

    result = run_dataset_sync(REPOSITORY_ROOT, trigger_dir)

    assert result.returncode == 0, combined_output(result)
    destination = trigger_dir / "data" / "eclipses"
    assert destination.is_dir()
    assert (destination / "registry.json").read_bytes() == (
        REPOSITORY_ROOT / "data" / "eclipses" / "registry.json"
    ).read_bytes()
    assert (destination / KNOWN_DATASET).read_bytes() == (
        REPOSITORY_ROOT / "data" / "eclipses" / KNOWN_DATASET
    ).read_bytes()


def test_loader_operates_from_generated_runtime(tmp_path: Path) -> None:
    trigger_dir = tmp_path / "trigger"
    result = run_dataset_sync(REPOSITORY_ROOT, trigger_dir)
    assert result.returncode == 0, combined_output(result)
    shutil.copytree(REPOSITORY_ROOT / "backend", trigger_dir / "backend")

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
import pathlib
import sys

runtime = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(runtime))
from backend.eclipse_engine import loader

assert pathlib.Path(loader.__file__).resolve().is_relative_to(runtime)
assert "2025-03-29" in loader.list_supported_eclipses()
assert isinstance(loader.load_eclipse("2025-03-29"), dict)
print(json.dumps({"loader": loader.__file__}))
""",
            str(trigger_dir),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert probe.returncode == 0, combined_output(probe)


def test_dataset_sync_rejects_missing_registry(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    (package_dir / "data" / "eclipses").mkdir(parents=True)

    result = run_dataset_sync(package_dir, tmp_path / "trigger")

    assert result.returncode != 0
    assert "missing registry" in combined_output(result)


def test_dataset_sync_rejects_invalid_registry_json(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    datasets_dir = package_dir / "data" / "eclipses"
    datasets_dir.mkdir(parents=True)
    (datasets_dir / "registry.json").write_text("{not valid json", encoding="utf-8")

    result = run_dataset_sync(package_dir, tmp_path / "trigger")

    assert result.returncode != 0
    assert "invalid json" in combined_output(result)


def test_dataset_sync_rejects_missing_referenced_dataset(tmp_path: Path) -> None:
    package_dir = tmp_path / "package"
    write_package_registry(
        package_dir,
        {"eclipses": [{"date": "2099-01-01", "file": "missing.json"}]},
    )

    result = run_dataset_sync(package_dir, tmp_path / "trigger")

    assert result.returncode != 0
    assert "missing dataset" in combined_output(result)


def test_update_files_test_mode_never_invokes_system_services(
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    sentinel_dir = tmp_path / "service-sentinels"
    bin_dir = tmp_path / "bin"

    app_dir.mkdir()
    bin_dir.mkdir()
    sentinel_dir.mkdir()

    for command in ("systemctl", "nginx"):
        executable = bin_dir / command
        executable.write_text(
            "#!/bin/sh\n"
            f"touch '{sentinel_dir / command}'\n"
            "exit 97\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

    environment = os.environ.copy()
    environment.update(
        PATH=f"{bin_dir}{os.pathsep}{environment['PATH']}",
        SOLARECLIPSE_TEST_MODE="1",
        SOLARECLIPSE_SKIP_SERVICE_RESTART="1",
        SOLARECLIPSE_TEST_PACKAGE_DIR=str(REPOSITORY_ROOT),
        SOLARECLIPSE_TEST_APP_DIR=str(app_dir),
    )

    result = subprocess.run(
        ["bash", str(UPDATE_FILES)],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, combined_output(result)
    assert not any(sentinel_dir.iterdir())
    assert "ignor" in combined_output(result)

    assert (
        app_dir / "data" / "eclipses" / KNOWN_DATASET
    ).is_file()


def test_installer_sources_and_calls_shared_dataset_helper() -> None:
    installer = (REPOSITORY_ROOT / "install" / "install_solareclipse.sh").read_text(
        encoding="utf-8"
    )

    assert 'source "$SCRIPT_DIR/datasets_sync.sh"' in installer
    assert 'sync_eclipse_datasets "$PACKAGE_DIR" "$APP_DIR"' in installer

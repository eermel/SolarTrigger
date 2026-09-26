import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile


def _build_package(path: Path, version: str = "test-fixed") -> None:
    payload = {
        "app.py": b"x = 1\n",
        "wsgi.py": b"x = 1\n",
        "backend/runtime_daemon.py": b"x = 1\n",
        "scripts/eclipse_trigger.py": b"x = 1\n",
        "templates/index.html": b"<html></html>\n",
        "static/js/solartrigger.js": b"console.log('ok');\n",
        "Sounds/contact.wav": b"RIFF",
        "configs/photo_cfg/photo_default.json": b"{}\n",
        "configs/camera_profiles/reference.json": b'{"source":"package"}\n',
        "configs/camera_timing/reference.json": b'{"source":"package"}\n',
        "install/solartrigger-release-update": b"#!/bin/bash\nexit 0\n",
    }
    files = {}
    for relative, data in payload.items():
        files[relative] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": "0755" if relative.startswith("install/") else "0644",
        }
    manifest = {
        "package_type": "solartrigger-release",
        "schema_version": 2,
        "version": version,
        "build_commit": "deadbeef",
        "files": files,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for relative, data in payload.items():
            archive.writestr(f"payload/{relative}", data)


def _helper_functions(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    helper = (root / "install" / "solartrigger-release-update").read_text(
        encoding="utf-8"
    )
    prefix = helper.split('case "${1:-}" in', 1)[0]
    functions = tmp_path / "helper-functions.sh"
    functions.write_text(prefix, encoding="utf-8")
    return functions


def test_release_update_does_not_leak_function_destination_and_cleans_stale(tmp_path):
    functions = _helper_functions(tmp_path)

    home = tmp_path / "home"
    base = home / "solartrigger"
    releases = base / "releases"
    active = home / "solar-eclipse-trigger-prod"
    (active / "var").mkdir(parents=True)
    (active / "venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, active / "venv" / "bin" / "python")
    (active / "app.py").write_text("x = 1\n", encoding="utf-8")
    (active / "BUILD_COMMIT").write_text("legacy\n", encoding="utf-8")

    # Data created before versioned persistent camera storage must survive the
    # migration and remain visible through every release.
    legacy_profile = active / "configs/camera_profiles/user_camera.json"
    legacy_profile.parent.mkdir(parents=True)
    legacy_profile.write_text('{"source":"legacy-characterization"}\n', encoding="utf-8")
    legacy_timing = active / "configs/camera_timing/user_camera.json"
    legacy_timing.parent.mkdir(parents=True)
    legacy_timing.write_text('{"source":"legacy-timing"}\n', encoding="utf-8")
    history = active / "configs/camera_characterization/history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text('{"status":"SUCCESS"}\n', encoding="utf-8")
    validation_report = (
        active
        / "configs/camera_characterization/validation/legacy-pass/report.json"
    )
    validation_report.parent.mkdir(parents=True)
    validation_report.write_text(
        '{"config_type":"camera_validation_report","analysis":{"verdict":"PASS"}}\n',
        encoding="utf-8",
    )

    releases.mkdir(parents=True)
    stale = releases / ".install-previous-failure-4938"
    stale.mkdir()
    (stale / "marker").write_text("stale", encoding="utf-8")

    package = tmp_path / "release.zip"
    _build_package(package)

    script = tmp_path / "run.sh"
    script.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "export SOLARTRIGGER_USER=$(id -un)\n"
        f"source {functions!s}\n"
        f"USER_HOME={home!s}\n"
        f"BASE={base!s}\n"
        f"RELEASES={releases!s}\n"
        f"SHARED_VAR={base / 'var'!s}\n"
        f"SHARED_VENV={base / 'venv'!s}\n"
        f"SHARED_CAMERA_BASE={base / 'var/generated'!s}\n"
        f"SHARED_CAMERA_PROFILES={base / 'var/generated/camera_profiles'!s}\n"
        f"SHARED_CAMERA_TIMING={base / 'var/generated/camera_timing'!s}\n"
        f"SHARED_CAMERA_CHARACTERIZATION={base / 'var/generated/camera_characterization'!s}\n"
        f"ACTIVE={active!s}\n"
        "APP_USER=$(id -un)\n"
        "chown(){ :; }\n"
        "seal_release(){ :; }\n"
        "request_reboot(){ log 'TEST reboot suppressed'; }\n"
        f"install_release {package!s}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    final = releases / "test-fixed"
    assert active.is_symlink()
    assert active.resolve() == final.resolve()
    assert final.is_dir()
    assert (final / "var").is_symlink()
    assert (final / "venv").is_symlink()
    assert not stale.exists()
    assert not any(releases.glob(".install-*"))
    assert not package.exists()

    shared = base / "var/generated"
    assert (shared / "camera_profiles/user_camera.json").read_text() == legacy_profile.read_text()
    assert (shared / "camera_timing/user_camera.json").read_text() == legacy_timing.read_text()
    assert (shared / "camera_characterization/history.jsonl").read_text() == history.read_text()
    assert (
        shared
        / "camera_characterization/validation/legacy-pass/report.json"
    ).read_text() == validation_report.read_text()
    assert (shared / "camera_characterization/validation").is_dir()
    assert (shared / "camera_profiles/reference.json").read_text() == '{"source":"package"}\n'
    assert (shared / "camera_timing/reference.json").read_text() == '{"source":"package"}\n'

    for name in ("camera_profiles", "camera_timing", "camera_characterization"):
        assert (final / "configs" / name).is_symlink()
        assert (final / "configs" / name).resolve() == (shared / name).resolve()

    legacy = [p for p in releases.iterdir() if p.name.startswith("legacy-")]
    assert len(legacy) == 1
    assert legacy[0].joinpath("var").is_symlink()
    assert legacy[0].joinpath("venv").is_symlink()
    for name in ("camera_profiles", "camera_timing", "camera_characterization"):
        assert legacy[0].joinpath("configs", name).is_symlink()
        assert legacy[0].joinpath("configs", name).resolve() == (shared / name).resolve()
    legacy_manifest = json.loads(
        legacy[0].joinpath("RELEASE_MANIFEST.json").read_text(encoding="utf-8")
    )
    assert legacy_manifest["files"]
    assert not any(
        path.startswith("configs/camera_")
        for path in legacy_manifest["files"]
    )


def test_integrity_verification_ignores_shared_camera_data_but_rejects_code_tamper(tmp_path):
    functions = _helper_functions(tmp_path)
    release = tmp_path / "release"
    package = tmp_path / "release.zip"
    _build_package(package)

    extract = subprocess.run(
        [
            "bash",
            "-c",
            f"export SOLARTRIGGER_USER=$(id -un); source {functions}; validate_and_extract {package} {release}",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert extract.returncode == 0, extract.stdout + extract.stderr

    # Replacing mutable camera files is expected and must not invalidate code.
    (release / "configs/camera_profiles/reference.json").write_text(
        '{"source":"user"}\n', encoding="utf-8"
    )
    ok = subprocess.run(
        ["bash", "-c", f"export SOLARTRIGGER_USER=$(id -un); source {functions}; verify_release_integrity {release}"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr

    (release / "app.py").write_text("tampered = True\n", encoding="utf-8")
    bad = subprocess.run(
        ["bash", "-c", f"export SOLARTRIGGER_USER=$(id -un); source {functions}; verify_release_integrity {release}"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad.returncode != 0
    assert "release integrity" in (bad.stdout + bad.stderr).lower()



def test_integrity_verification_ignores_python_bytecode_but_rejects_other_extras(tmp_path):
    functions = _helper_functions(tmp_path)
    release = tmp_path / "release"
    package = tmp_path / "release.zip"
    _build_package(package)

    extract = subprocess.run(
        [
            "bash",
            "-c",
            f"export SOLARTRIGGER_USER=$(id -un); source {functions}; validate_and_extract {package} {release}",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert extract.returncode == 0, extract.stdout + extract.stderr

    cache = release / "plugins/__pycache__"
    cache.mkdir(parents=True)
    (cache / "__init__.cpython-313.pyc").write_bytes(b"generated bytecode")
    (release / "backend/runtime_daemon.pyc").write_bytes(b"generated bytecode")

    ok = subprocess.run(
        ["bash", "-c", f"export SOLARTRIGGER_USER=$(id -un); source {functions}; verify_release_integrity {release}"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr

    (release / "plugins/undeclared.py").write_text("x = 1\n", encoding="utf-8")
    bad = subprocess.run(
        ["bash", "-c", f"export SOLARTRIGGER_USER=$(id -un); source {functions}; verify_release_integrity {release}"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bad.returncode != 0
    assert "undeclared release file" in (bad.stdout + bad.stderr).lower()

def test_user_generated_data_and_characterization_survive_version_switch_and_rollback(tmp_path):
    functions = _helper_functions(tmp_path)
    home = tmp_path / "home"
    base = home / "solartrigger"
    releases = base / "releases"
    active = home / "solar-eclipse-trigger-prod"
    releases.mkdir(parents=True)

    legacy_package = tmp_path / "legacy.zip"
    _build_package(legacy_package, version="legacy-source")

    bootstrap = subprocess.run(
        [
            "bash",
            "-c",
            (
                "set -euo pipefail; "
                "export SOLARTRIGGER_USER=$(id -un); "
                f"source {functions}; "
                f"validate_and_extract {legacy_package} {active}"
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert bootstrap.returncode == 0, bootstrap.stdout + bootstrap.stderr
    (active / "var" / "generated" / "photo_cfg").mkdir(parents=True)
    (active / "var" / "generated" / "photo_cfg" / "user.json").write_text(
        '{"user":true}\n', encoding="utf-8"
    )
    (active / "venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, active / "venv" / "bin" / "python")

    release_one = tmp_path / "release-one.zip"
    release_two = tmp_path / "release-two.zip"
    _build_package(release_one, version="release-one")
    _build_package(release_two, version="release-two")

    script = tmp_path / "switch.sh"
    script.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "export SOLARTRIGGER_USER=$(id -un)\n"
        f"source {functions!s}\n"
        f"USER_HOME={home!s}\n"
        f"BASE={base!s}\n"
        f"RELEASES={releases!s}\n"
        f"SHARED_VAR={base / 'var'!s}\n"
        f"SHARED_VENV={base / 'venv'!s}\n"
        f"SHARED_CAMERA_BASE={base / 'var/generated'!s}\n"
        f"SHARED_CAMERA_PROFILES={base / 'var/generated/camera_profiles'!s}\n"
        f"SHARED_CAMERA_TIMING={base / 'var/generated/camera_timing'!s}\n"
        f"SHARED_CAMERA_CHARACTERIZATION={base / 'var/generated/camera_characterization'!s}\n"
        f"ACTIVE={active!s}\n"
        "APP_USER=$(id -un)\n"
        "chown(){ :; }\n"
        "seal_release(){ :; }\n"
        "request_reboot(){ log 'TEST reboot suppressed'; }\n"
        f"install_release {release_one!s}\n"
        # Simulate a characterization and an ordinary user-generated config
        # after release-one is active.
        "printf '%s\\n' '{\"characterized\":\"release-one\"}' > "
        '"$ACTIVE/configs/camera_profiles/field_camera.json"\n'
        "printf '%s\\n' '{\"timing\":\"measured\"}' > "
        '"$ACTIVE/configs/camera_timing/field_camera.json"\n'
        "printf '%s\\n' '{\"status\":\"SUCCESS\"}' >> "
        '"$ACTIVE/configs/camera_characterization/history.jsonl"\n'
        "mkdir -p \"$ACTIVE/configs/camera_characterization/validation/release-one-pass\"\n"
        "printf '%s\\n' '{\"analysis\":{\"verdict\":\"PASS\"}}' > "
        '"$ACTIVE/configs/camera_characterization/validation/release-one-pass/report.json"\n'
        f"install_release {release_two!s}\n"
        "test \"$(readlink -f \"$ACTIVE\")\" = \"$(readlink -f \"$RELEASES/release-two\")\"\n"
        "grep -q 'release-one' \"$ACTIVE/configs/camera_profiles/field_camera.json\"\n"
        "grep -q 'measured' \"$ACTIVE/configs/camera_timing/field_camera.json\"\n"
        "rollback_release release-one\n"
        "test \"$(readlink -f \"$ACTIVE\")\" = \"$(readlink -f \"$RELEASES/release-one\")\"\n"
        "grep -q 'release-one' \"$ACTIVE/configs/camera_profiles/field_camera.json\"\n"
        "grep -q 'measured' \"$ACTIVE/configs/camera_timing/field_camera.json\"\n"
        "grep -q 'SUCCESS' \"$ACTIVE/configs/camera_characterization/history.jsonl\"\n"
        "grep -q 'PASS' \"$ACTIVE/configs/camera_characterization/validation/release-one-pass/report.json\"\n"
        "grep -q '\"user\":true' \"$ACTIVE/var/generated/photo_cfg/user.json\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(script)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    shared = base / "var" / "generated"
    assert (shared / "camera_profiles" / "field_camera.json").is_file()
    assert (shared / "camera_timing" / "field_camera.json").is_file()
    assert (shared / "camera_characterization" / "history.jsonl").is_file()
    assert (
        shared
        / "camera_characterization"
        / "validation"
        / "release-one-pass"
        / "report.json"
    ).is_file()
    assert (shared / "camera_characterization" / "validation").is_dir()
    assert (shared / "photo_cfg" / "user.json").is_file()


def test_release_helper_hardens_runtime_identity_and_self_refresh():
    root = Path(__file__).resolve().parents[1]
    helper = (root / "install" / "solartrigger-release-update").read_text(
        encoding="utf-8"
    )

    assert "User=root" in helper
    assert "Group=$app_group" in helper
    assert "RuntimeDirectory=solartrigger" in helper
    assert "RuntimeDirectoryMode=0770" in helper
    assert 'Environment="SOLARTRIGGER_RUNTIME_SOCKET_GROUP=$app_group"' in helper
    assert "refresh_installed_release_helper()" in helper
    assert (
        'refresh_installed_release_helper '
        '"$final_destination/install/solartrigger-release-update"'
        in helper
    )

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


def test_release_update_does_not_leak_function_destination_and_cleans_stale(tmp_path):
    root = Path(__file__).resolve().parents[1]
    helper = (root / "install" / "solartrigger-release-update").read_text(encoding="utf-8")
    prefix = helper.split('case "${1:-}" in', 1)[0]
    functions = tmp_path / "helper-functions.sh"
    functions.write_text(prefix, encoding="utf-8")

    home = tmp_path / "home"
    base = home / "solartrigger"
    releases = base / "releases"
    active = home / "solar-eclipse-trigger-prod"
    (active / "var").mkdir(parents=True)
    (active / "venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, active / "venv" / "bin" / "python")
    (active / "app.py").write_text("x = 1\n", encoding="utf-8")
    (active / "BUILD_COMMIT").write_text("legacy\n", encoding="utf-8")

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
        f"BASE={base!s}\n"
        f"RELEASES={releases!s}\n"
        f"SHARED_VAR={base / 'var'!s}\n"
        f"SHARED_VENV={base / 'venv'!s}\n"
        f"ACTIVE={active!s}\n"
        "APP_USER=$(id -un)\n"
        "chown(){ :; }\n"
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
    legacy = [p for p in releases.iterdir() if p.name.startswith("legacy-")]
    assert len(legacy) == 1
    assert legacy[0].joinpath("var").is_symlink()
    assert legacy[0].joinpath("venv").is_symlink()

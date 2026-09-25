import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def _helper_functions(tmp_path: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    helper = (root / "install" / "solartrigger-release-update").read_text(
        encoding="utf-8"
    )
    prefix = helper.split('case "${1:-}" in', 1)[0]
    functions = tmp_path / "helper-functions.sh"
    functions.write_text(prefix, encoding="utf-8")
    return functions


def _minimal_release(root: Path) -> None:
    payload = {
        "app.py": b"x = 1\n",
        "wsgi.py": b"x = 1\n",
        "backend/runtime_daemon.py": b"x = 1\n",
        "scripts/eclipse_trigger.py": b"x = 1\n",
        "templates/index.html": b"<html></html>\n",
        "static/js/solartrigger.js": b"console.log('ok');\n",
    }
    files = {}
    for relative, data in payload.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files[relative] = {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": "0644",
        }

    (root / "RELEASE_MANIFEST.json").write_text(
        json.dumps(
            {
                "package_type": "solartrigger-release",
                "schema_version": 2,
                "version": "1.0.0",
                "files": files,
            }
        ),
        encoding="utf-8",
    )


def test_dev_prepare_clones_verified_release_once_and_preserves_shared_links(tmp_path):
    functions = _helper_functions(tmp_path)

    home = tmp_path / "home"
    base = home / "solartrigger"
    releases = base / "releases"
    release = releases / "1.0.0"
    active = home / "solar-eclipse-trigger-prod"
    shared_var = base / "var"
    shared_venv = base / "venv"

    releases.mkdir(parents=True)
    shared_var.mkdir()
    (shared_venv / "bin").mkdir(parents=True)
    os.symlink(sys.executable, shared_venv / "bin" / "python")
    _minimal_release(release)
    os.symlink(release, active)

    script = tmp_path / "run.sh"
    script.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "export SOLARTRIGGER_USER=$(id -un)\n"
        f"source {functions!s}\n"
        f"USER_HOME={home!s}\n"
        f"BASE={base!s}\n"
        f"RELEASES={releases!s}\n"
        f"SHARED_VAR={shared_var!s}\n"
        f"SHARED_VENV={shared_venv!s}\n"
        f"SHARED_CAMERA_BASE={shared_var / 'generated'!s}\n"
        f"SHARED_CAMERA_PROFILES={shared_var / 'generated/camera_profiles'!s}\n"
        f"SHARED_CAMERA_TIMING={shared_var / 'generated/camera_timing'!s}\n"
        f"SHARED_CAMERA_CHARACTERIZATION={shared_var / 'generated/camera_characterization'!s}\n"
        f"ACTIVE={active!s}\n"
        f"DEV_ACTIVE={base / 'dev-active'!s}\n"
        "APP_USER=$(id -un)\n"
        "chown(){ :; }\n"
        "prepare_dev_workspace\n"
        "test \"$(readlink -f \"$ACTIVE\")\" = \"$(readlink -m \"$DEV_ACTIVE\")\"\n"
        "test -f \"$DEV_ACTIVE/.solartrigger-dev-workspace\"\n"
        "test ! -e \"$DEV_ACTIVE/RELEASE_MANIFEST.json\"\n"
        "test \"$(readlink -f \"$DEV_ACTIVE/var\")\" = \"$(readlink -f \"$SHARED_VAR\")\"\n"
        "test \"$(readlink -f \"$DEV_ACTIVE/venv\")\" = \"$(readlink -f \"$SHARED_VENV\")\"\n"
        "test \"$(readlink -f \"$DEV_ACTIVE/configs/camera_characterization\")\" = "
        "\"$(readlink -f \"$SHARED_CAMERA_CHARACTERIZATION\")\"\n"
        "test -d \"$SHARED_CAMERA_CHARACTERIZATION/validation\"\n"
        "printf '%s\\n' keep > \"$DEV_ACTIVE/local-marker\"\n"
        "prepare_dev_workspace\n"
        "test -f \"$DEV_ACTIVE/local-marker\"\n",
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
    assert "DEV workspace prepared" in result.stdout
    assert "DEV workspace already active" in result.stdout

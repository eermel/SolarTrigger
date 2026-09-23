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
                "version": "hardening-test",
                "files": files,
            }
        ),
        encoding="utf-8",
    )


def _run_helper(functions: Path, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "bash",
            "-c",
            (
                "set -euo pipefail; "
                "export SOLARTRIGGER_USER=$(id -un); "
                f"source {functions}; "
                f"{command}"
            ),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def test_validate_release_runtime_does_not_write_bytecode(tmp_path):
    functions = _helper_functions(tmp_path)
    release = tmp_path / "release"
    _minimal_release(release)

    shared_venv = tmp_path / "venv"
    (shared_venv / "bin").mkdir(parents=True)
    os.symlink(sys.executable, shared_venv / "bin" / "python")

    result = _run_helper(
        functions,
        f"SHARED_VENV={shared_venv}; validate_release_runtime {release}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(release.rglob("__pycache__"))
    assert not list(release.rglob("*.pyc"))
    assert not list(release.rglob("*.pyo"))


def test_cleanup_release_runtime_artifacts_removes_old_bytecode(tmp_path):
    functions = _helper_functions(tmp_path)
    release = tmp_path / "release"
    cache = release / "backend" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "runtime_daemon.cpython-313.pyc").write_bytes(b"old")
    (release / "orphan.pyo").write_bytes(b"old")

    result = _run_helper(
        functions,
        f"cleanup_release_runtime_artifacts {release}",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(release.rglob("__pycache__"))
    assert not list(release.rglob("*.pyc"))
    assert not list(release.rglob("*.pyo"))


def test_integrity_rejects_undeclared_file(tmp_path):
    functions = _helper_functions(tmp_path)
    release = tmp_path / "release"
    _minimal_release(release)
    (release / "backend" / "rogue.py").write_text(
        "rogue = True\n",
        encoding="utf-8",
    )

    result = _run_helper(
        functions,
        f"verify_release_integrity {release}",
    )
    assert result.returncode != 0
    assert "undeclared release file: backend/rogue.py" in (
        result.stdout + result.stderr
    )


def test_integrity_rejects_undeclared_symlink(tmp_path):
    functions = _helper_functions(tmp_path)
    release = tmp_path / "release"
    _minimal_release(release)
    os.symlink("/tmp", release / "rogue-link")

    result = _run_helper(
        functions,
        f"verify_release_integrity {release}",
    )
    assert result.returncode != 0
    assert "undeclared release symlink: rogue-link" in (
        result.stdout + result.stderr
    )

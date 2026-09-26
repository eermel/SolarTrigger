#!/usr/bin/env python3
"""Build a complete SolarTrigger archive for a freshly installed Raspberry Pi.

The resulting ZIP contains the full offline application payload expected by
install/install_solareclipse.sh plus a human-readable RELEASE_VERSION file.
The installer copy embedded in the ZIP is adapted so the initial installed
release is named with that version (for example 1.0.0), while BUILD_COMMIT is
kept only as technical traceability metadata.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

PACKAGE_TYPE = "solartrigger-fresh-install"
SCHEMA_VERSION = 1
SEMVER_RE = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

COPY_TREES = (
    "backend",
    "services",
    "plugins",
    "configs",
    "data",
    "Sounds",
    "vendor",
    "install",
)
RUNTIME_SCRIPTS = (
    "__init__.py",
    "camera_ipc_client.py",
    "eclipse_calculator_py.py",
    "eclipse_trigger.py",
    "fanout_camera_adapter.py",
    "gps_sync.py",
)
SKIP_PARTS = {"__pycache__", ".pytest_cache", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}

INSTALLER_RELATIVE = PurePosixPath("install/install_solareclipse.sh")

OLD_VERSION_BLOCK = '''if [ -n "$BUILD_COMMIT" ]; then
    INITIAL_RELEASE_VERSION="bootstrap-${BUILD_COMMIT:0:12}"
else
    INITIAL_RELEASE_VERSION="bootstrap-$(date -u +%Y%m%d-%H%M%S)"
fi
'''

NEW_VERSION_BLOCK = '''RELEASE_VERSION_FILE="$PACKAGE_DIR/RELEASE_VERSION"
if [ ! -f "$RELEASE_VERSION_FILE" ]; then
    error "Missing RELEASE_VERSION in installation package."
fi

INITIAL_RELEASE_VERSION=$(tr -d '[:space:]' < "$RELEASE_VERSION_FILE")
if [[ ! "$INITIAL_RELEASE_VERSION" =~ ^[0-9]+\\.[0-9]+\\.[0-9]+(-[0-9A-Za-z.-]+)?(\\+[0-9A-Za-z.-]+)?$ ]]; then
    error "Invalid SolarTrigger release version: $INITIAL_RELEASE_VERSION"
fi
'''

OLD_METADATA_BLOCK = '''# Métadonnée logicielle unique : commit source du build.
# Migration d'une éventuelle ancienne installation.
rm -f "$APP_DIR/VERSION"

if [ -n "$BUILD_COMMIT" ]; then
'''

NEW_METADATA_BLOCK = '''# Métadonnées de release : version lisible par l'opérateur + commit source
# conservé uniquement pour la traçabilité technique.
rm -f "$APP_DIR/VERSION"
printf '%s\\n' "$INITIAL_RELEASE_VERSION" > "$APP_DIR/RELEASE_VERSION"

if [ -n "$BUILD_COMMIT" ]; then
'''


def validate_version(value: str) -> str:
    version = str(value or "").strip()
    if not SEMVER_RE.fullmatch(version):
        raise ValueError(
            "version must be semantic and human-readable, e.g. 1.0.0 or 1.0.0-rc1"
        )
    return version


def _git_commit(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value or None


def _git_tracked_files(repo_root: Path) -> set[str] | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z"],
            check=True,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return {
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    }


def _iter_tree(root: Path, prefix: PurePosixPath, include):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or not include(path):
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path, prefix / PurePosixPath(*relative.parts)


def _source_files(repo_root: Path):
    tracked = _git_tracked_files(repo_root)

    def include(path: Path) -> bool:
        if tracked is None:
            return True
        return path.relative_to(repo_root).as_posix() in tracked

    for tree_name in COPY_TREES:
        source = repo_root / tree_name
        if not source.is_dir():
            raise FileNotFoundError(f"Missing installation tree: {source}")
        yield from _iter_tree(source, PurePosixPath(tree_name), include)

    scripts_root = repo_root / "scripts"
    for script_name in RUNTIME_SCRIPTS:
        source = scripts_root / script_name
        if not source.is_file() or not include(source):
            raise FileNotFoundError(f"Missing tracked runtime script: {source}")
        yield source, PurePosixPath("scripts") / script_name

    flask_root = repo_root / "flask_app"
    app_py = flask_root / "app.py"
    if not app_py.is_file() or not include(app_py):
        raise FileNotFoundError(f"Missing tracked Flask application: {app_py}")
    yield app_py, PurePosixPath("flask_app/app.py")

    for source_name in ("templates", "static"):
        source = flask_root / source_name
        if not source.is_dir():
            raise FileNotFoundError(f"Missing Flask tree: {source}")
        yield from _iter_tree(
            source,
            PurePosixPath("flask_app") / source_name,
            include,
        )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode) or 0o644


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _patch_installer(data: bytes, version: str) -> bytes:
    text = data.decode("utf-8")

    if OLD_VERSION_BLOCK not in text:
        raise RuntimeError(
            "install/install_solareclipse.sh no longer contains the expected "
            "bootstrap version block; review the fresh-package builder"
        )
    text = text.replace(OLD_VERSION_BLOCK, NEW_VERSION_BLOCK, 1)

    if OLD_METADATA_BLOCK not in text:
        raise RuntimeError(
            "install/install_solareclipse.sh no longer contains the expected "
            "release metadata block; review the fresh-package builder"
        )
    text = text.replace(OLD_METADATA_BLOCK, NEW_METADATA_BLOCK, 1)

    text = re.sub(
        r"(?m)^#\s+Version\s*:\s*.*$",
        f"#   Release : {version}",
        text,
        count=1,
    )
    return text.encode("utf-8")


def _install_wrapper() -> bytes:
    return b'''#!/bin/bash\nset -euo pipefail\nROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\nexec "$ROOT/install/install_solareclipse.sh" "$@"\n'''


def build_fresh_install(
    repo_root: Path,
    output: Path,
    version: str,
    *,
    build_commit: str | None = None,
) -> Path:
    repo_root = repo_root.resolve()
    output = output.resolve()
    version = validate_version(version)
    commit = build_commit or _git_commit(repo_root)

    prefix = PurePosixPath(f"SolarTrigger-{version}")
    payloads: list[tuple[str, bytes, int]] = []
    files: dict[str, dict[str, object]] = {}

    for source, relative in _source_files(repo_root):
        data = source.read_bytes()
        if relative == INSTALLER_RELATIVE:
            data = _patch_installer(data, version)
        mode = _mode(source)
        name = relative.as_posix()
        if name in files:
            raise ValueError(f"Duplicate installation path: {name}")
        files[name] = {
            "sha256": _sha256(data),
            "size": len(data),
            "mode": f"{mode:04o}",
        }
        payloads.append((name, data, mode))

    generated: dict[str, tuple[bytes, int]] = {
        "RELEASE_VERSION": ((version + "\n").encode("utf-8"), 0o644),
        "install.sh": (_install_wrapper(), 0o755),
    }
    if commit:
        generated["BUILD_COMMIT"] = ((commit + "\n").encode("utf-8"), 0o644)

    for name, (data, mode) in generated.items():
        files[name] = {
            "sha256": _sha256(data),
            "size": len(data),
            "mode": f"{mode:04o}",
        }
        payloads.append((name, data, mode))

    required = {
        "RELEASE_VERSION",
        "install.sh",
        "install/install_solareclipse.sh",
        "install/solartrigger-release-update",
        "install/solartrigger-system-update",
        "install/install_zwo_eaf_hid.sh",
        "backend/runtime_daemon.py",
        "scripts/eclipse_trigger.py",
        "flask_app/app.py",
        "flask_app/templates/index.html",
        "flask_app/static/js/solartrigger.js",
        "Sounds/contact.wav",
        "configs/photo_cfg/photo_default.json",
    }
    missing = sorted(required - set(files))
    if missing:
        raise FileNotFoundError("Fresh installation package is incomplete: " + ", ".join(missing))

    manifest = {
        "package_type": PACKAGE_TYPE,
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "build_commit": commit,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(
            temp,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            manifest_info = zipfile.ZipInfo((prefix / "fresh-install-manifest.json").as_posix())
            manifest_info.create_system = 3
            manifest_info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(manifest_info, manifest_data)

            for name, data, mode in payloads:
                info = zipfile.ZipInfo((prefix / name).as_posix())
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, data)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)

    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a complete SolarTrigger ZIP for a fresh Raspberry Pi."
    )
    parser.add_argument("version", help="Human release label, e.g. 1.0.0")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="ZIP path (default: dist/solartrigger-install-<version>.zip)",
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    version = validate_version(args.version)
    output = args.output or (
        args.repo_root / "dist" / f"solartrigger-install-{version}.zip"
    )
    result = build_fresh_install(args.repo_root, output, version)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

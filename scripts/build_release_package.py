#!/usr/bin/env python3
"""Build an offline SolarTrigger release ZIP from a development checkout."""

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


PACKAGE_TYPE = "solartrigger-release"
SCHEMA_VERSION = 2
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

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
WSGI_SOURCE = """from app import app, socketio, start_background_threads\n\nstart_background_threads()\n\nif __name__ == \"__main__\":\n    socketio.run(app)\n"""


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
    """Return tracked repository paths, or None outside a Git checkout."""
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


def _iter_tree(root: Path, prefix: PurePosixPath, include=lambda _path: True):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or not include(path):
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        yield path, prefix / PurePosixPath(*relative.parts)


def _runtime_files(repo_root: Path):
    tracked = _git_tracked_files(repo_root)

    def include(path: Path) -> bool:
        if tracked is None:
            return True
        return path.relative_to(repo_root).as_posix() in tracked

    for tree_name in COPY_TREES:
        source = repo_root / tree_name
        if not source.is_dir():
            if tree_name == "jubier_files":
                continue
            raise FileNotFoundError(f"Missing runtime tree: {source}")
        yield from _iter_tree(
            source,
            PurePosixPath(tree_name),
            include=include,
        )

    scripts_root = repo_root / "scripts"
    for script_name in RUNTIME_SCRIPTS:
        source = scripts_root / script_name
        if not source.is_file() or not include(source):
            raise FileNotFoundError(
                f"Missing tracked runtime script: {source}"
            )
        yield source, PurePosixPath("scripts") / script_name

    flask_root = repo_root / "flask_app"
    app_py = flask_root / "app.py"
    if not app_py.is_file() or not include(app_py):
        raise FileNotFoundError(f"Missing tracked runtime file: {app_py}")
    yield app_py, PurePosixPath("app.py")

    for source_name, target_name in (
        ("templates", "templates"),
        ("static", "static"),
    ):
        source = flask_root / source_name
        if not source.is_dir():
            raise FileNotFoundError(f"Missing runtime tree: {source}")
        yield from _iter_tree(
            source,
            PurePosixPath(target_name),
            include=include,
        )

    # The web UI serves the same tracked WAV assets that the Pi runtime uses.
    sounds = repo_root / "Sounds"
    for path, relative in _iter_tree(
        sounds,
        PurePosixPath("static/sounds"),
        include=include,
    ):
        yield path, relative


def _zip_mode(path: Path) -> int:
    mode = stat.S_IMODE(path.stat().st_mode)
    return mode or 0o644


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_release(
    repo_root: Path,
    output: Path,
    version: str,
    *,
    build_commit: str | None = None,
) -> Path:
    repo_root = repo_root.resolve()
    output = output.resolve()

    if not VERSION_RE.fullmatch(version):
        raise ValueError(
            "version must use only letters, digits, dot, underscore, plus or dash"
        )

    files: dict[str, dict[str, object]] = {}
    payloads: list[tuple[str, bytes, int]] = []

    for source, relative in _runtime_files(repo_root):
        name = relative.as_posix()
        if name in files:
            raise ValueError(f"duplicate runtime path: {name}")
        data = source.read_bytes()
        mode = _zip_mode(source)
        files[name] = {
            "sha256": _sha256_bytes(data),
            "size": len(data),
            "mode": f"{mode:04o}",
        }
        payloads.append((name, data, mode))

    generated = {
        "wsgi.py": WSGI_SOURCE.encode("utf-8"),
    }
    commit = build_commit or _git_commit(repo_root)
    if commit:
        generated["BUILD_COMMIT"] = (commit + "\n").encode("utf-8")

    for name, data in generated.items():
        mode = 0o644
        files[name] = {
            "sha256": _sha256_bytes(data),
            "size": len(data),
            "mode": f"{mode:04o}",
        }
        payloads.append((name, data, mode))

    required = (
        "app.py",
        "wsgi.py",
        "backend/runtime_daemon.py",
        "scripts/eclipse_trigger.py",
        "templates/index.html",
        "static/js/solartrigger.js",
        "Sounds/contact.wav",
        "configs/photo_cfg/photo_default.json",
        "install/solartrigger-release-update",
        "install/install_zwo_eaf_hid.sh",
    )
    missing = [name for name in required if name not in files]
    if missing:
        raise FileNotFoundError(
            "Release is incomplete: " + ", ".join(missing)
        )

    manifest = {
        "package_type": PACKAGE_TYPE,
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "build_commit": commit,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with zipfile.ZipFile(
            temp,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            )
            for name, data, mode in payloads:
                info = zipfile.ZipInfo(f"payload/{name}")
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | mode) << 16
                archive.writestr(info, data)
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)

    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an offline SolarTrigger release ZIP."
    )
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="ZIP path (default: dist/solartrigger-<version>.zip)",
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    output = args.output or (
        args.repo_root / "dist" / f"solartrigger-{args.version}.zip"
    )
    result = build_release(args.repo_root, output, args.version)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""System and application maintenance helpers for the web portal."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import stat
import subprocess
import threading
import zipfile


SYS = Path("/sys/class/net")
SYSTEM_HELPER = "/usr/local/sbin/solartrigger-system-update"
RELEASE_HELPER = "/usr/local/sbin/solartrigger-release-update"
MAX = 256 * 1024 * 1024
MAX_UNCOMPRESSED = 768 * 1024 * 1024
PACKAGE_TYPE = "solartrigger-release"
PACKAGE_SCHEMA_VERSION = 2
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")

INSTALL_BASE = Path(
    os.environ.get(
        "SOLARTRIGGER_INSTALL_BASE",
        str(Path.home() / "solartrigger"),
    )
)
RELEASES_DIR = Path(
    os.environ.get(
        "SOLARTRIGGER_RELEASES_DIR",
        str(INSTALL_BASE / "releases"),
    )
)
ACTIVE_RELEASE = Path(
    os.environ.get(
        "SOLARTRIGGER_ACTIVE_RELEASE",
        str(Path.home() / "solar-eclipse-trigger-prod"),
    )
)

_REQUIRED_RELEASE_PATHS = {
    "app.py",
    "wsgi.py",
    "backend/runtime_daemon.py",
    "scripts/eclipse_trigger.py",
    "templates/index.html",
    "static/js/solartrigger.js",
    "Sounds/contact.wav",
    "configs/photo_cfg/photo_default.json",
    "install/solartrigger-release-update",
}


def ethernet_status(root=SYS):
    out = []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        entries = []
    for path in entries:
        if path.name == "lo" or (path / "wireless").exists():
            continue
        try:
            carrier = (path / "carrier").read_text().strip() == "1"
        except OSError:
            carrier = False
        out.append({"name": path.name, "carrier": carrier})
    return {
        "connected": any(item["carrier"] for item in out),
        "interfaces": out,
    }


def _route_interface(address):
    try:
        result = subprocess.run(
            ["ip", "route", "get", address],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    parts = result.stdout.split()
    try:
        return parts[parts.index("dev") + 1]
    except (ValueError, IndexError):
        return None


def internet_available(timeout=2.0, root=SYS):
    ethernet = {
        item["name"]
        for item in ethernet_status(root)["interfaces"]
        if item["carrier"]
    }
    if not ethernet:
        return False

    endpoints = (
        ("deb.debian.org", 80),
        ("security.debian.org", 80),
        ("deb.debian.org", 443),
    )

    for host, port in endpoints:
        try:
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError:
            continue

        for _family, _socktype, _proto, _canonname, sockaddr in infos:
            if _route_interface(sockaddr[0]) not in ethernet:
                continue
            try:
                with socket.create_connection(sockaddr, timeout=timeout):
                    return True
            except OSError:
                continue

    return False


def validate_release_version(value) -> str:
    version = str(value or "").strip()
    if not VERSION_RE.fullmatch(version):
        raise ValueError("Invalid release version")
    return version


def _safe_member_name(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Unsafe ZIP path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe ZIP path")
    return path


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_IFMT(mode) == stat.S_IFLNK


def _hash_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info, "r") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def validate_release_zip(path):
    """Validate structure, completeness and SHA-256 integrity of one update ZIP."""

    package = Path(path)
    if not package.is_file() or package.stat().st_size > MAX:
        raise ValueError("Invalid or oversized update package")

    try:
        archive = zipfile.ZipFile(package)
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid ZIP package") from exc

    with archive:
        infos = archive.infolist()
        if len(infos) > 10000:
            raise ValueError("Update package contains too many entries")
        if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED:
            raise ValueError("Update package is too large")

        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate ZIP entries are not allowed")

        for info in infos:
            _safe_member_name(info.filename)
            if _zip_member_is_symlink(info):
                raise ValueError("ZIP symlinks are not allowed")

        try:
            manifest = json.loads(archive.read("manifest.json"))
        except Exception as exc:
            raise ValueError(
                "manifest.json is required and must be valid"
            ) from exc

        if not isinstance(manifest, dict):
            raise ValueError("Invalid release manifest")
        if manifest.get("package_type") != PACKAGE_TYPE:
            raise ValueError("Invalid release manifest")
        if manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
            raise ValueError("Unsupported release package schema")

        version = validate_release_version(manifest.get("version"))
        files = manifest.get("files")
        if not isinstance(files, dict) or not files:
            raise ValueError("Release manifest does not contain file hashes")

        expected_members = set()
        normalized_files = {}
        for relative_name, metadata in files.items():
            relative = _safe_member_name(relative_name)
            if str(relative).startswith("payload/"):
                raise ValueError("Manifest file paths must be payload-relative")
            if not isinstance(metadata, dict):
                raise ValueError("Invalid release file metadata")

            sha256 = str(metadata.get("sha256") or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise ValueError("Invalid release file hash")
            try:
                size = int(metadata.get("size"))
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid release file size") from exc
            if size < 0:
                raise ValueError("Invalid release file size")

            key = relative.as_posix()
            normalized_files[key] = {
                "sha256": sha256,
                "size": size,
            }
            expected_members.add(f"payload/{key}")

        missing_required = sorted(_REQUIRED_RELEASE_PATHS - set(normalized_files))
        if missing_required:
            raise ValueError(
                "Release package is incomplete: " + ", ".join(missing_required)
            )

        actual_members = {
            info.filename
            for info in infos
            if info.filename.startswith("payload/") and not info.is_dir()
        }
        if actual_members != expected_members:
            missing = sorted(expected_members - actual_members)
            unexpected = sorted(actual_members - expected_members)
            detail = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if unexpected:
                detail.append("unexpected=" + ",".join(unexpected))
            raise ValueError(
                "Release payload does not match manifest"
                + (": " + " ".join(detail) if detail else "")
            )

        by_name = {info.filename: info for info in infos}
        for relative_name, metadata in normalized_files.items():
            member = by_name[f"payload/{relative_name}"]
            if member.file_size != metadata["size"]:
                raise ValueError(
                    f"Release file size mismatch: {relative_name}"
                )
            if _hash_member(archive, member) != metadata["sha256"]:
                raise ValueError(
                    f"Release file hash mismatch: {relative_name}"
                )

        manifest["version"] = version
        return manifest


def installed_releases(
    releases_dir: Path | None = None,
    active_release: Path | None = None,
):
    """Return installed release versions and which directory is active."""

    releases_dir = Path(releases_dir or RELEASES_DIR)
    active_release = Path(active_release or ACTIVE_RELEASE)
    active_target = None
    try:
        if active_release.is_symlink():
            active_target = active_release.resolve(strict=False)
    except OSError:
        active_target = None

    entries = []
    try:
        candidates = sorted(
            (
                path
                for path in releases_dir.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ),
            key=lambda path: path.name.casefold(),
        )
    except OSError:
        candidates = []

    for path in candidates:
        version = path.name
        metadata = {}
        manifest_path = path / "RELEASE_MANIFEST.json"
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                metadata = raw
                version = str(raw.get("version") or version)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass

        try:
            is_active = (
                active_target is not None
                and path.resolve(strict=False) == active_target
            )
        except OSError:
            is_active = False

        files = metadata.get("files") if isinstance(metadata, dict) else None
        rollback_eligible = (
            isinstance(files, dict)
            and bool(files)
            and _REQUIRED_RELEASE_PATHS.issubset(files)
        )

        entries.append({
            "version": version,
            "directory": path.name,
            "active": is_active,
            "build_commit": metadata.get("build_commit"),
            "rollback_eligible": rollback_eligible,
        })

    active = next(
        (item["version"] for item in entries if item["active"]),
        None,
    )
    return {
        "active": active,
        "releases": entries,
    }


class Job:
    def __init__(self):
        self.lock = threading.RLock()
        self.running = False
        self.kind = None
        self.status = "idle"
        self.error = None
        self.logs = deque(maxlen=1000)

    def snapshot(self):
        with self.lock:
            return deepcopy({
                "running": self.running,
                "kind": self.kind,
                "status": self.status,
                "error": self.error,
                "logs": list(self.logs),
            })

    def _claim(self, kind):
        with self.lock:
            if self.running:
                raise RuntimeError("Maintenance operation already running")
            self.running = True
            self.kind = kind
            self.status = "running"
            self.error = None
            self.logs.clear()

    def start(self, kind, cmd):
        self._claim(kind)
        threading.Thread(
            target=self._run,
            args=(cmd,),
            daemon=True,
        ).start()

    def start_callable(self, kind, fn):
        """Run in-process maintenance while keeping busy state authoritative."""
        self._claim(kind)
        threading.Thread(
            target=self._run_callable,
            args=(fn,),
            daemon=True,
        ).start()

    def _run(self, cmd):
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in process.stdout or ():
                self.logs.append(line.rstrip())
            if process.wait():
                raise RuntimeError("Maintenance helper failed")
            self.status = "success"
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            self.logs.append("FAILED: " + str(exc))
        finally:
            self.running = False

    def _run_callable(self, fn):
        try:
            fn()
            self.status = "success"
        except Exception as exc:
            self.status = "failed"
            self.error = str(exc)
            self.logs.append("FAILED: " + str(exc))
        finally:
            self.running = False


JOB = Job()

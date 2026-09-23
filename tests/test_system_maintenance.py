import hashlib
import json
import zipfile

import pytest

from backend import system_maintenance
from backend.system_maintenance import (
    ethernet_status,
    installed_releases,
    internet_available,
    validate_release_zip,
)


def test_eth_carrier(tmp_path):
    eth = tmp_path / "eth0"
    eth.mkdir()
    (eth / "carrier").write_text("1")
    assert ethernet_status(tmp_path)["connected"]


def test_wifi_not_eth(tmp_path):
    wifi = tmp_path / "wlan0"
    wifi.mkdir()
    (wifi / "wireless").mkdir()
    (wifi / "carrier").write_text("1")
    assert not ethernet_status(tmp_path)["connected"]


def _eth_root(tmp_path):
    eth = tmp_path / "eth0"
    eth.mkdir()
    (eth / "carrier").write_text("1")
    return tmp_path


def test_internet_available_uses_debian_endpoint_over_physical_ethernet(
    tmp_path,
    monkeypatch,
):
    root = _eth_root(tmp_path)
    monkeypatch.setattr(
        system_maintenance.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("199.232.170.132", 80))
        ],
    )
    monkeypatch.setattr(
        system_maintenance,
        "_route_interface",
        lambda address: "eth0",
    )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        system_maintenance.socket,
        "create_connection",
        lambda *args, **kwargs: Connection(),
    )
    assert internet_available(root=root)


def test_internet_available_rejects_route_over_wifi(
    tmp_path,
    monkeypatch,
):
    root = _eth_root(tmp_path)
    monkeypatch.setattr(
        system_maintenance.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("199.232.170.132", 80))
        ],
    )
    monkeypatch.setattr(
        system_maintenance,
        "_route_interface",
        lambda address: "wlan0",
    )
    monkeypatch.setattr(
        system_maintenance.socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail(
            "must not connect through Wi-Fi"
        ),
    )
    assert not internet_available(root=root)


def test_internet_available_fails_closed_when_debian_endpoints_unreachable(
    tmp_path,
    monkeypatch,
):
    root = _eth_root(tmp_path)
    monkeypatch.setattr(
        system_maintenance.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("199.232.170.132", 80))
        ],
    )
    monkeypatch.setattr(
        system_maintenance,
        "_route_interface",
        lambda address: "eth0",
    )

    def fail(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(
        system_maintenance.socket,
        "create_connection",
        fail,
    )
    assert not internet_available(root=root)


def _release_payload():
    return {
        "app.py": b"app\n",
        "wsgi.py": b"wsgi\n",
        "backend/runtime_daemon.py": b"runtime\n",
        "scripts/eclipse_trigger.py": b"trigger\n",
        "templates/index.html": b"<html></html>\n",
        "static/js/solartrigger.js": b"console.log('x');\n",
        "Sounds/contact.wav": b"RIFF-test",
        "configs/photo_cfg/photo_default.json": b"{}\n",
        "install/solartrigger-release-update": b"#!/bin/sh\n",
    }


def _write_release(path, *, version="7.3.0", tamper=None):
    payload = _release_payload()
    files = {
        name: {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mode": "0644",
        }
        for name, data in payload.items()
    }
    manifest = {
        "package_type": "solartrigger-release",
        "schema_version": 2,
        "version": version,
        "build_commit": "a" * 40,
        "files": files,
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest),
        )
        for name, data in payload.items():
            if tamper == name:
                data += b"-tampered"
            archive.writestr(f"payload/{name}", data)
    return manifest


def test_zip_validates_manifest_hashes(tmp_path):
    package = tmp_path / "x.zip"
    _write_release(package)

    result = validate_release_zip(package)

    assert result["version"] == "7.3.0"
    assert result["schema_version"] == 2


def test_zip_rejects_payload_hash_mismatch(tmp_path):
    package = tmp_path / "x.zip"
    _write_release(package, tamper="app.py")

    with pytest.raises(ValueError, match="size mismatch|hash mismatch"):
        validate_release_zip(package)


def test_zip_traversal(tmp_path):
    package = tmp_path / "x.zip"
    _write_release(package)
    with zipfile.ZipFile(package, "a") as archive:
        archive.writestr("payload/../../etc/passwd", "x")

    with pytest.raises(ValueError, match="Unsafe ZIP path"):
        validate_release_zip(package)


def test_zip_rejects_legacy_unhashed_manifest(tmp_path):
    package = tmp_path / "x.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps({
                "package_type": "solartrigger-release",
                "version": "7.3.0",
            }),
        )
        archive.writestr("payload/app.py", "x")

    with pytest.raises(ValueError, match="schema"):
        validate_release_zip(package)


def test_installed_releases_marks_active_symlink(tmp_path):
    releases = tmp_path / "releases"
    releases.mkdir()
    v10 = releases / "1.0"
    v11 = releases / "1.1"
    v10.mkdir()
    v11.mkdir()
    (v10 / "RELEASE_MANIFEST.json").write_text(
        json.dumps({
            "version": "1.0",
            "build_commit": "a" * 40,
        }),
        encoding="utf-8",
    )
    (v11 / "RELEASE_MANIFEST.json").write_text(
        json.dumps({
            "version": "1.1",
            "build_commit": "b" * 40,
        }),
        encoding="utf-8",
    )
    active = tmp_path / "solar-eclipse-trigger-prod"
    active.symlink_to(v11)

    result = installed_releases(releases, active)

    assert result["active"] == "1.1"
    assert result["releases"] == [
        {
            "version": "1.0",
            "directory": "1.0",
            "active": False,
            "build_commit": "a" * 40,
        },
        {
            "version": "1.1",
            "directory": "1.1",
            "active": True,
            "build_commit": "b" * 40,
        },
    ]

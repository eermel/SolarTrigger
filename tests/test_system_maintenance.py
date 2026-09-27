import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import zipfile

import pytest

from backend import system_maintenance
from backend.system_maintenance import (
    _REQUIRED_RELEASE_PATHS,
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
            "files": {name: {"size": 1, "sha256": "0" * 64} for name in _REQUIRED_RELEASE_PATHS},
        }),
        encoding="utf-8",
    )
    (v11 / "RELEASE_MANIFEST.json").write_text(
        json.dumps({
            "version": "1.1",
            "build_commit": "b" * 40,
            "files": {name: {"size": 1, "sha256": "0" * 64} for name in _REQUIRED_RELEASE_PATHS},
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
            "rollback_eligible": True,
        },
        {
            "version": "1.1",
            "directory": "1.1",
            "active": True,
            "build_commit": "b" * 40,
            "rollback_eligible": True,
        },
    ]


def test_installed_releases_rejects_legacy_manifest_without_hashes(tmp_path):
    releases = tmp_path / "releases"
    releases.mkdir()
    legacy = releases / "legacy-20260923-203041"
    legacy.mkdir()
    (legacy / "RELEASE_MANIFEST.json").write_text(
        json.dumps({
            "package_type": "solartrigger-release",
            "schema_version": 2,
            "version": legacy.name,
            "build_commit": "a" * 40,
            "migrated_legacy": True,
            "files": {},
        }),
        encoding="utf-8",
    )
    active = tmp_path / "solar-eclipse-trigger-prod"

    result = installed_releases(releases, active)

    assert result["releases"] == [{
        "version": legacy.name,
        "directory": legacy.name,
        "active": False,
        "build_commit": "a" * 40,
        "rollback_eligible": False,
    }]


def test_successful_system_upgrade_records_completion_in_log(monkeypatch):
    class Process:
        stdout = ["apt output\\n"]
        def wait(self):
            return 0
    monkeypatch.setattr(system_maintenance.subprocess, "Popen", lambda *a, **k: Process())
    job = system_maintenance.Job()
    job._claim("apt-upgrade")
    job._run(["helper", "upgrade"])
    snapshot = job.snapshot()
    assert snapshot["status"] == "success"
    assert snapshot["logs"][-1] == "SUCCESS: System update completed successfully"


def test_maintenance_thread_start_failure_rolls_job_back(monkeypatch):
    class FailingThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("thread start failed")

    monkeypatch.setattr(system_maintenance.threading, "Thread", FailingThread)

    for starter in (
        lambda job: job.start("apt-check", ["helper", "check"]),
        lambda job: job.start_callable("callable", lambda: None),
    ):
        job = system_maintenance.Job()
        with pytest.raises(RuntimeError, match="thread start failed"):
            starter(job)

        snapshot = job.snapshot()
        assert snapshot["running"] is False
        assert snapshot["status"] == "failed"
        assert "thread start failed" in snapshot["error"]


def test_privileged_maintenance_helpers_share_one_nonblocking_lock(tmp_path):
    root = Path(__file__).resolve().parents[1]
    system_helper = root / "install" / "solartrigger-system-update"
    release_helper = root / "install" / "solartrigger-release-update"
    system_text = system_helper.read_text(encoding="utf-8")
    release_text = release_helper.read_text(encoding="utf-8")

    default_lock = "/run/lock/solartrigger-maintenance.lock"
    assert default_lock in system_text
    assert default_lock in release_text
    assert "/usr/bin/flock -n 9" in system_text
    assert "/usr/bin/flock -n 9" in release_text
    assert "acquire_maintenance_lock" in release_text

    # Behavioural check without running apt or release mutations: an externally
    # held test lock must make the system helper fail before it reaches even an
    # invalid action.
    lock_path = tmp_path / "maintenance.lock"
    with lock_path.open("w") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        env = os.environ.copy()
        env["SOLARTRIGGER_MAINTENANCE_LOCK"] = str(lock_path)
        result = subprocess.run(
            [str(system_helper), "invalid-action"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    assert result.returncode == 75
    assert "already running" in result.stderr.lower()


def test_maintenance_helper_running_detects_external_flock(tmp_path):
    lock_path = tmp_path / "maintenance.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        assert system_maintenance.maintenance_helper_running(lock_path) is False

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert system_maintenance.maintenance_helper_running(lock_path) is True

        fcntl.flock(fd, fcntl.LOCK_UN)
        assert system_maintenance.maintenance_helper_running(lock_path) is False
    finally:
        os.close(fd)


def test_maintenance_helper_running_detects_preflock_helper_process(tmp_path):
    proc_root = tmp_path / "proc"
    helper_proc = proc_root / "123"
    helper_proc.mkdir(parents=True)
    (helper_proc / "cmdline").write_bytes(
        b"sudo\0-n\0"
        b"/usr/local/sbin/solartrigger-system-update\0"
        b"upgrade\0"
    )

    missing_lock = tmp_path / "maintenance.lock"
    assert system_maintenance.maintenance_helper_running(
        missing_lock,
        proc_root=proc_root,
    ) is True

    (helper_proc / "cmdline").write_bytes(
        b"cat\0"
        b"/tmp/copy-of-solartrigger-system-update\0"
    )
    assert system_maintenance.maintenance_helper_running(
        missing_lock,
        proc_root=proc_root,
    ) is False

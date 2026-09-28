import sys
from pathlib import Path
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from backend.state_store import StateStore
from backend.system_maintenance import Job


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


class _NeverGpsController:
    def __init__(self):
        self.calls = 0

    def start(self, **_kwargs):
        self.calls += 1
        return True


class _NeverMaintenanceJob:
    def __init__(self):
        self.calls = 0

    def start_callable(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("destructive maintenance must not start")

    def start(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("destructive maintenance must not start")


@pytest.fixture
def starting_trigger_client(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices",
        {"gps": {"plugin": "test", "active": True}},
    )

    # Deliberately keep the published state idle.  The regression target is
    # TriggerService's private START->Popen window, before running=True appears
    # in StateStore.
    service = SimpleNamespace(
        any_active_or_starting=lambda: True,
        is_active_or_starting=lambda _rig_id: True,
    )
    gps = _NeverGpsController()
    maintenance_job = _NeverMaintenanceJob()

    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_trigger_service", service)
    monkeypatch.setattr(flask_module, "_gps_controller", gps)

    from backend import system_maintenance

    monkeypatch.setattr(system_maintenance, "JOB", maintenance_job)

    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), gps, maintenance_job


def test_gps_sync_rejects_trigger_starting_before_state_publication(
    starting_trigger_client,
):
    client, gps, _maintenance_job = starting_trigger_client

    response = client.post("/api/gps/sync_time")

    assert response.status_code == 409
    assert response.get_json()["code"] == "TRIGGER_RUNNING"
    assert gps.calls == 0


def test_erase_reboot_rejects_trigger_starting_before_state_publication(
    starting_trigger_client,
):
    client, _gps, maintenance_job = starting_trigger_client

    response = client.post(
        "/api/system/erase-persistent-data-and-reboot",
        json={"confirmation": "ERASE ALL PERSISTANT DATA & REBOOT"},
    )

    assert response.status_code == 409
    assert response.get_json()["code"] == "TRIGGER_RUNNING"
    assert maintenance_job.calls == 0


def test_callable_maintenance_publishes_busy_state_for_entire_action():
    job = Job()
    entered = threading.Event()
    release = threading.Event()

    def action():
        entered.set()
        if not release.wait(1.0):
            raise RuntimeError("test action was not released")

    job.start_callable("erase-reboot", action)
    try:
        assert entered.wait(0.5)
        snapshot = job.snapshot()
        assert snapshot["running"] is True
        assert snapshot["kind"] == "erase-reboot"
        assert snapshot["status"] == "running"

        with pytest.raises(RuntimeError, match="already running"):
            job.start_callable("second-action", lambda: None)
    finally:
        release.set()

    deadline = time.monotonic() + 1.0
    while job.snapshot()["running"] and time.monotonic() < deadline:
        time.sleep(0.01)

    snapshot = job.snapshot()
    assert snapshot["running"] is False
    assert snapshot["status"] == "success"


def test_trigger_start_guard_rejects_external_maintenance_helper(monkeypatch):
    from backend import camera_characterization
    from backend import camera_validation
    from backend import system_maintenance
    from backend.trigger_service import TriggerValidationError

    monkeypatch.setattr(
        system_maintenance,
        "maintenance_helper_running",
        lambda: True,
    )
    monkeypatch.setattr(
        system_maintenance.JOB,
        "snapshot",
        lambda: {"running": False},
    )
    monkeypatch.setattr(camera_characterization.JOB, "running", False)
    monkeypatch.setattr(camera_validation.JOB, "running", False)

    called = []
    with pytest.raises(TriggerValidationError) as caught:
        flask_module._trigger_start_guarded(
            lambda: called.append(True)
        )

    assert caught.value.code == "SYSTEM_MAINTENANCE_RUNNING"
    assert called == []


def test_erase_reboot_is_delegated_to_locked_root_helper(monkeypatch):
    from backend import system_maintenance

    class CapturingJob:
        def __init__(self):
            self.calls = []

        def start(self, kind, command):
            self.calls.append((kind, list(command)))

    job = CapturingJob()
    monkeypatch.setattr(
        flask_module,
        "_trigger_active_or_starting",
        lambda _rig_id=None: False,
    )
    monkeypatch.setattr(system_maintenance, "JOB", job)

    flask_module.app.config.update(TESTING=True)
    response = flask_module.app.test_client().post(
        "/api/system/erase-persistent-data-and-reboot",
        json={"confirmation": "ERASE ALL PERSISTANT DATA & REBOOT"},
    )

    assert response.status_code == 200
    assert job.calls == [
        (
            "erase-reboot",
            [
                "sudo",
                "-n",
                system_maintenance.RELEASE_HELPER,
                "erase-reboot",
            ],
        )
    ]


def test_release_helper_erase_reboot_uses_detached_locked_worker():
    root = Path(__file__).resolve().parents[1]
    helper = (
        root / "install" / "solartrigger-release-update"
    ).read_text(encoding="utf-8")

    admission_index = helper.index("    erase-reboot)")
    admission_lock = helper.index(
        "acquire_maintenance_lock",
        admission_index,
    )
    detach_index = helper.index("/usr/bin/systemd-run", admission_lock)
    worker_index = helper.index("    erase-reboot-worker)", detach_index)
    worker_lock = helper.index(
        "acquire_maintenance_lock_wait",
        worker_index,
    )
    reset_index = helper.index(
        "reset_persistent_data_and_reboot",
        worker_lock,
    )

    assert admission_lock < detach_index < worker_index
    assert worker_lock < reset_index
    assert '--unit="solartrigger-erase-reboot"' in helper
    assert "--property=Type=exec" in helper
    assert '--setenv=SOLARTRIGGER_USER="$APP_USER"' in helper
    assert "--no-block" not in helper[admission_index:worker_index]
    assert "exec >/dev/null 2>&1" in helper
    assert "Persistent SolarTrigger data erased. Rebooting Raspberry Pi." in helper

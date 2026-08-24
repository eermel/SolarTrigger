import sys
from types import ModuleType

import pytest

from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


@pytest.fixture
def guarded_routes(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), state_store


def _set_device_active(state_store, category, active):
    state_store.update_section(
        "devices",
        {category: {"plugin": "test" if active else "none", "active": active}},
    )


@pytest.mark.parametrize(
    "route",
    [
        "/api/gps/sync",
        "/api/gps/sync_time_location",
        "/api/gps/sync_time",
        "/api/gps/get_location",
    ],
)
def test_gps_sync_inactive_does_not_start_controller(
    guarded_routes, monkeypatch, route
):
    client, state_store = guarded_routes
    _set_device_active(state_store, "gps", False)

    class UnexpectedController:
        def start(self, **kwargs):
            pytest.fail("GpsController must not be started")

    monkeypatch.setattr(flask_module, "_gps_controller", UnexpectedController())

    response = client.post(route)

    assert response.status_code == 409
    assert response.get_json()["code"] == "DEVICE_INACTIVE"
    assert response.get_json()["category"] == "gps"


@pytest.mark.parametrize(
    ("route", "mode"),
    [
        ("/api/gps/sync", "time_location"),
        ("/api/gps/sync_time_location", "time_location"),
        ("/api/gps/sync_time", "time_only"),
        ("/api/gps/get_location", "location_only"),
    ],
)
def test_gps_sync_active_starts_controller(
    guarded_routes, monkeypatch, route, mode
):
    client, state_store = guarded_routes
    _set_device_active(state_store, "gps", True)
    starts = []

    class RecordingController:
        def start(self, **kwargs):
            starts.append(kwargs)
            return True

    monkeypatch.setattr(flask_module, "_gps_controller", RecordingController())

    response = client.post(route)

    assert response.status_code == 200
    assert response.get_json() == {"status": "started"}
    assert starts == [{"timeout_s": 60.0, "mode": mode}]


def test_camera_usb_inactive_does_not_run_subprocess(
    guarded_routes, monkeypatch
):
    client, state_store = guarded_routes
    _set_device_active(state_store, "camera", False)
    monkeypatch.setattr(
        flask_module.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("subprocess must not be invoked"),
    )

    response = client.post("/api/camera/usb", json={"action": "release"})

    assert response.status_code == 409
    assert response.get_json()["code"] == "DEVICE_INACTIVE"
    assert response.get_json()["category"] == "camera"


def test_camera_usb_active_continues_existing_release_flow(
    guarded_routes, monkeypatch
):
    client, state_store = guarded_routes
    _set_device_active(state_store, "camera", True)
    calls = []
    monkeypatch.setattr(
        flask_module.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    monkeypatch.setattr("glob.glob", lambda _pattern: [])
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    response = client.post("/api/camera/usb", json={"action": "release"})

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert [call[0][-1] for call in calls] == [
        "gvfsd-gphoto2",
        "gphoto2",
        "gvfs-gphoto2-volume-monitor",
    ]

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


def test_gps_sync_alias_active_starts_time_location_mode(
    guarded_routes, monkeypatch
):
    client, state_store = guarded_routes
    _set_device_active(state_store, "gps", True)
    starts = []

    class RecordingController:
        def start(self, **kwargs):
            starts.append(kwargs)
            return True

    monkeypatch.setattr(flask_module, "_gps_controller", RecordingController())

    response = client.post("/api/gps/sync")

    assert response.status_code == 200
    assert response.get_json() == {"status": "started"}
    assert starts == [{"timeout_s": 60.0, "mode": "time_location"}]


@pytest.mark.parametrize(
    "route",
    [
        "/api/debug/generate",
        "/api/debug/generate_realistic",
        "/api/camera/usb",
        "/api/configs/clear_debug",
    ],
)
def test_removed_ui_routes_are_not_registered(route):
    if hasattr(flask_module.app, "routes"):
        assert (route, "POST") not in flask_module.app.routes
    else:
        registered_routes = {
            rule.rule
            for rule in flask_module.app.url_map.iter_rules()
            if "POST" in rule.methods
        }
        assert route not in registered_routes

import sys
from types import ModuleType

import pytest

from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


TIME_FIELDS = ("synced", "sync_time")
LOCATION_FIELDS = (
    "lat",
    "lon",
    "alt",
    "satellites",
    "hdop",
    "date",
    "timezone",
    "timezone_name",
    "utc_offset_minutes",
)

INITIAL_GPS = {
    "synced": False,
    "sync_time": "2025-01-02T03:04:05+00:00",
    "lat": 12.345678,
    "lon": 23.456789,
    "alt": 123.4,
    "satellites": 4,
    "hdop": 2.5,
    "date": "2025-01-02",
    "timezone": "UTC+1",
    "timezone_name": "Europe/Rome",
    "utc_offset_minutes": 60,
}

UPDATED_TIME = {
    "synced": True,
    "sync_time": "2026-08-24T10:11:12+00:00",
}

UPDATED_LOCATION = {
    "lat": 44.123456,
    "lon": 5.654321,
    "alt": 456.7,
    "satellites": 9,
    "hdop": 0.8,
    "date": "2026-08-24",
    "timezone": "UTC+2",
    "timezone_name": "Europe/Paris",
    "utc_offset_minutes": 120,
}


class FinalStateGpsController:
    """Synchronous stand-in for the final state transition of each GPS mode."""

    def __init__(self, state_store):
        self.state_store = state_store
        self.starts = []

    def start(self, *, timeout_s, mode):
        self.starts.append({"timeout_s": timeout_s, "mode": mode})
        values = {"gps_sync_running": False}
        if mode in {"time_only", "time_location"}:
            values.update(UPDATED_TIME)
        if mode in {"location_only", "time_location"}:
            values.update(UPDATED_LOCATION)
        snapshot = self.state_store.update_section("gps", values)
        flask_module.socketio.emit("gps_update", snapshot, namespace="/")
        flask_module.socketio.emit("gps_sync_done", {"synced": True}, namespace="/")
        return True


@pytest.fixture
def gps_actions(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section("gps", INITIAL_GPS)
    state_store.update_section(
        "devices", {"gps": {"plugin": "test", "active": True}}
    )
    state_store.update_section("trigger", {"running": False})

    controller = FinalStateGpsController(state_store)
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_gps_controller", controller)
    monkeypatch.setattr(flask_module.socketio, "emit", lambda *args, **kwargs: None)
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), state_store, controller


def _select(snapshot, fields):
    return {field: snapshot[field] for field in fields}


def test_sync_time_changes_only_time_fields(gps_actions):
    client, state_store, controller = gps_actions
    before = state_store.snapshot("gps")

    response = client.post("/api/gps/sync_time")

    after = state_store.snapshot("gps")
    assert response.status_code == 200
    assert controller.starts == [{"timeout_s": 60.0, "mode": "time_only"}]
    assert _select(after, TIME_FIELDS) == UPDATED_TIME
    assert _select(after, LOCATION_FIELDS) == _select(before, LOCATION_FIELDS)


def test_get_location_changes_only_location_fields(gps_actions):
    client, state_store, controller = gps_actions
    before = state_store.snapshot("gps")

    response = client.post("/api/gps/get_location")

    after = state_store.snapshot("gps")
    assert response.status_code == 200
    assert controller.starts == [{"timeout_s": 60.0, "mode": "location_only"}]
    assert _select(after, LOCATION_FIELDS) == UPDATED_LOCATION
    assert _select(after, TIME_FIELDS) == _select(before, TIME_FIELDS)


def test_sync_time_and_location_changes_both_field_groups(gps_actions):
    client, state_store, controller = gps_actions

    response = client.post("/api/gps/sync_time_location")

    after = state_store.snapshot("gps")
    assert response.status_code == 200
    assert controller.starts == [{"timeout_s": 60.0, "mode": "time_location"}]
    assert _select(after, TIME_FIELDS) == UPDATED_TIME
    assert _select(after, LOCATION_FIELDS) == UPDATED_LOCATION


@pytest.mark.parametrize(
    ("payload", "expects_clock_reset"),
    [
        ({**UPDATED_LOCATION, "synced": False}, False),
        ({**UPDATED_LOCATION, "synced": True}, True),
    ],
)
def test_clock_reset_is_emitted_only_for_synced_gps_updates(
    monkeypatch, payload, expects_clock_reset
):
    emitted = []
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, data, **kwargs: emitted.append((event, data, kwargs)),
    )

    flask_module._emit_backend("gps_update", payload)

    events = [event for event, _data, _kwargs in emitted]
    assert ("clock_reset" in events) is expects_clock_reset

import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType

import pytest

from backend.state_store import StateStore
from plugins.focuser.base import DIR_IN, DIR_OUT
from services.focuser_service import FocuserService


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


class RecordingPlugin:
    def __init__(self):
        self.connected = False
        self.relative_moves = []
        self.jog_calls = []

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def status(self):
        return {"moving": False, "holding": False}

    def get_position(self):
        return 100

    def move_relative(self, delta, wait=False):
        self.relative_moves.append(delta)

    def start_continuous(self, direction, mode):
        self.jog_calls.append((direction, mode))


def settings(mode="slow", updated_at=None):
    return {
        "mode": mode,
        "slow_step": 37,
        "fast_step": 240,
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture
def focuser_api(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.json")
    store.update_section(
        "devices",
        {"focuser": {"plugin": "recording", "active": True}},
    )
    store.update_section("focuser_settings", settings(), persist=True)
    plugin = RecordingPlugin()
    service = FocuserService(
        store,
        log_fn=lambda *_: None,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )
    monkeypatch.setattr(flask_module, "_state_store", store)
    monkeypatch.setattr(flask_module, "_focuser_service", service)
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), service, plugin


@pytest.mark.parametrize(
    ("direction", "expected_delta"),
    [("increase", 37), ("decrease", -37)],
)
def test_step_direction_uses_signed_active_step(
    focuser_api, direction, expected_delta
):
    client, _, plugin = focuser_api

    response = client.post("/api/focuser/step", json={"direction": direction})

    assert response.status_code == 200
    assert plugin.relative_moves == [expected_delta]


@pytest.mark.parametrize(
    ("legacy_delta", "expected_delta"),
    [(9999, 37), (-9999, -37)],
)
def test_legacy_delta_supplies_direction_but_not_magnitude(
    focuser_api, legacy_delta, expected_delta
):
    client, _, plugin = focuser_api

    response = client.post("/api/focuser/step", json={"delta": legacy_delta})

    assert response.status_code == 200
    assert plugin.relative_moves == [expected_delta]


@pytest.mark.parametrize(
    ("direction", "legacy_delta"),
    [("increase", -1), ("decrease", 1)],
)
def test_contradictory_direction_and_delta_are_rejected(
    focuser_api, direction, legacy_delta
):
    client, _, plugin = focuser_api

    response = client.post(
        "/api/focuser/step",
        json={"direction": direction, "delta": legacy_delta},
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_DIRECTION"
    assert plugin.relative_moves == []


def test_legacy_mode_is_ignored_by_step_and_jog(focuser_api):
    client, service, plugin = focuser_api
    service.set_mode("fast")

    step = client.post(
        "/api/focuser/step",
        json={"direction": "increase", "mode": "fine"},
    )
    jog = client.post(
        "/api/focuser/jog/start",
        json={"direction": "decrease", "mode": "fine"},
    )

    assert step.status_code == 200
    assert jog.status_code == 200
    assert plugin.relative_moves == [240]
    assert plugin.jog_calls == [(DIR_IN, "coarse")]


def test_mode_endpoint_is_authoritative_for_step_and_jog(focuser_api):
    client, _, plugin = focuser_api

    changed = client.post("/api/focuser/mode", json={"mode": "fast"})
    step = client.post(
        "/api/focuser/step",
        json={"direction": "decrease", "mode": "slow"},
    )
    jog = client.post(
        "/api/focuser/jog/start",
        json={"direction": "increase", "mode": "slow"},
    )

    assert changed.status_code == 200
    assert changed.get_json()["mode"] == "fast"
    assert step.status_code == 200
    assert jog.status_code == 200
    assert plugin.relative_moves == [-240]
    assert plugin.jog_calls == [(DIR_OUT, "coarse")]


@pytest.mark.parametrize(
    ("age", "expected_mode", "expected_slow", "expected_fast", "expected_active"),
    [
        (timedelta(hours=71), "fast", 37, 240, 240),
        (timedelta(hours=73), "slow", 20, 150, 20),
    ],
)
def test_settings_ttl_restores_recent_values_or_persists_defaults(
    tmp_path, age, expected_mode, expected_slow, expected_fast, expected_active
):
    path = tmp_path / "state.json"
    store = StateStore(path)
    updated_at = (datetime.now(timezone.utc) - age).isoformat()
    store.update_section(
        "focuser_settings",
        settings(mode="fast", updated_at=updated_at),
        persist=True,
    )

    service = FocuserService(store, log_fn=lambda *_: None)

    assert service.active_step() == expected_active
    persisted = StateStore(path).snapshot("focuser_settings")
    assert persisted["mode"] == expected_mode
    assert persisted["slow_step"] == expected_slow
    assert persisted["fast_step"] == expected_fast
    if age > timedelta(hours=72):
        assert persisted["updated_at"] != updated_at
    else:
        assert persisted["updated_at"] == updated_at

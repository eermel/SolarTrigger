import sys
from types import ModuleType

import pytest

from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


class RecordingFocuserService:
    def __init__(self):
        self.mode = "slow"
        self.moves = []

    def active_step(self):
        return 37

    def move_relative(self, delta):
        self.moves.append(delta)
        return {"status": "ok", "delta": delta, "mode": self.mode}


@pytest.fixture
def focuser_step_api(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices",
        {"focuser": {"plugin": "test", "active": True}},
    )
    service = RecordingFocuserService()
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_focuser_service", service)
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), service


@pytest.mark.parametrize(
    ("direction", "mode", "expected_delta"),
    [
        ("increase", "coarse", 37),
        ("decrease", "invalid", -37),
    ],
)
def test_step_accepts_canonical_direction_and_ignores_legacy_mode(
    focuser_step_api, direction, mode, expected_delta
):
    client, service = focuser_step_api

    response = client.post(
        "/api/focuser/step", json={"direction": direction, "mode": mode}
    )

    assert response.status_code == 200
    assert response.get_json()["delta"] == expected_delta
    assert service.moves == [expected_delta]
    assert service.mode == "slow"


@pytest.mark.parametrize(
    ("legacy_delta", "mode", "expected_delta"),
    [
        (5, "fine", 37),
        (-3, "invalid", -37),
    ],
)
def test_step_accepts_legacy_delta_but_uses_active_step_magnitude(
    focuser_step_api, legacy_delta, mode, expected_delta
):
    client, service = focuser_step_api

    response = client.post(
        "/api/focuser/step", json={"delta": legacy_delta, "mode": mode}
    )

    assert response.status_code == 200
    assert response.get_json()["delta"] == expected_delta
    assert service.moves == [expected_delta]
    assert service.mode == "slow"


@pytest.mark.parametrize(
    ("direction", "legacy_delta", "mode"),
    [
        ("increase", -1, "coarse"),
        ("decrease", 1, "fine"),
    ],
)
def test_step_rejects_direction_delta_conflict(
    focuser_step_api, direction, legacy_delta, mode
):
    client, service = focuser_step_api

    response = client.post(
        "/api/focuser/step",
        json={"direction": direction, "delta": legacy_delta, "mode": mode},
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_DIRECTION"
    assert service.moves == []
    assert service.mode == "slow"

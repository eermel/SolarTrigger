import sys
from types import ModuleType

import pytest

from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


class BusyFocuserService:
    def status(self):
        return {"moving": True, "motion_command": "go"}

    def stop(self):
        return self.status()

    def set_mode(self, mode):
        return {**self.status(), "mode": mode}

    def set_step(self, coarse=None, fine=None):
        return {**self.status(), "slow_step": fine, "fast_step": coarse}


@pytest.fixture
def busy_focuser_api(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices",
        {"focuser": {"plugin": "test", "active": True}},
    )
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_focuser_service", BusyFocuserService())
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client()


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("/api/focuser/home", None),
        ("/api/focuser/move_to", {"position": 100}),
        ("/api/focuser/step", {"direction": "increase"}),
        ("/api/focuser/jog/start", {"direction": "increase"}),
    ],
)
def test_motion_endpoints_reject_commands_while_focuser_is_busy(
    busy_focuser_api, endpoint, payload
):
    response = busy_focuser_api.post(endpoint, json=payload)

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "Focuser motion already in progress.",
        "code": "FOCUSER_BUSY",
    }


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        ("/api/focuser/stop", None),
        ("/api/focuser/mode", {"mode": "fast"}),
        ("/api/focuser/set_step", {"coarse": 200}),
    ],
)
def test_non_motion_endpoints_remain_available_while_focuser_is_busy(
    busy_focuser_api, endpoint, payload
):
    response = busy_focuser_api.post(endpoint, json=payload)

    assert response.status_code == 200

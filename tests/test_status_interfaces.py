import sys
from types import ModuleType

import pytest

from backend.rig_manager import Rig, RigManager
from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


@pytest.fixture
def status_state(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    circumstances = {
        "loaded": True,
        "active_file": "eclipse_test.json",
        "meta": {"site": "test site"},
    }
    capture = {
        "loaded": True,
        "active_file": "camera_test.json",
        "meta": {"camera": "test camera"},
    }
    state_store.update_section("circumstances", circumstances)
    state_store.update_section("capture", capture)

    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_state", state_store.data)
    monkeypatch.setattr(flask_module, "_state_lock", state_store.lock)

    return state_store, circumstances, capture


def test_api_status_exposes_current_circumstances_and_capture(
    status_state, monkeypatch
):
    state_store, circumstances, capture = status_state
    monkeypatch.setattr(flask_module, "_get_camera_status", lambda: {})
    monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: None)

    response = flask_module.app.test_client().get("/api/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload["time"]["backend_utc_epoch_ms"], int)
    assert isinstance(payload["time"]["backend_local_epoch_ms"], int)
    assert payload["circumstances"] == circumstances
    assert payload["capture"] == capture
    assert payload["circumstances"] == state_store.snapshot("circumstances")
    assert payload["capture"] == state_store.snapshot("capture")


def test_api_status_exposes_four_normalized_rigs(status_state, monkeypatch):
    rig_manager = RigManager(
        {
            1: Rig(1, True, "Primary", {"camera": {"backend": "simulated"}}),
            3: Rig(3, False, "Spare", {"camera": {"backend": "none"}}),
        }
    )
    monkeypatch.setattr(flask_module, "get_rig_manager", lambda: rig_manager)
    monkeypatch.setattr(flask_module, "_get_camera_status", lambda: {})
    monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: None)

    response = flask_module.app.test_client().get("/api/status")

    assert response.status_code == 200
    rigs = response.get_json()["rigs"]
    assert len(rigs) == 4
    assert rigs == [
        {"rig_id": 1, "name": "Primary", "enabled": True},
        {"rig_id": 2, "name": "RIG 2", "enabled": False},
        {"rig_id": 3, "name": "Spare", "enabled": False},
        {"rig_id": 4, "name": "RIG 4", "enabled": False},
    ]


def test_on_connect_status_update_exposes_current_circumstances_and_capture(
    status_state, monkeypatch
):
    state_store, circumstances, capture = status_state
    rig_manager = RigManager(
        {
            1: Rig(1, True, "Primary", {"camera": {"backend": "simulated"}}),
            3: Rig(3, False, "Spare", {"camera": {"backend": "none"}}),
        }
    )
    emitted = []
    monkeypatch.setattr(flask_module, "get_rig_manager", lambda: rig_manager)
    monkeypatch.setattr(
        flask_module,
        "emit",
        lambda event, payload: emitted.append((event, payload)),
    )
    monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: None)
    monkeypatch.setattr(flask_module, "_log_buffer", [])

    flask_module.on_connect()

    assert [event for event, _payload in emitted] == ["status_update"]
    payload = emitted[0][1]
    assert isinstance(payload["time"]["backend_utc_epoch_ms"], int)
    assert isinstance(payload["time"]["backend_local_epoch_ms"], int)
    assert payload["circumstances"] == circumstances
    assert payload["capture"] == capture
    assert payload["circumstances"] == state_store.snapshot("circumstances")
    assert payload["capture"] == state_store.snapshot("capture")
    assert payload["rigs"] == [
        {"rig_id": 1, "name": "Primary", "enabled": True},
        {"rig_id": 2, "name": "RIG 2", "enabled": False},
        {"rig_id": 3, "name": "Spare", "enabled": False},
        {"rig_id": 4, "name": "RIG 4", "enabled": False},
    ]


def test_synced_gps_update_emits_clock_reset_epochs(monkeypatch):
    rig_manager = RigManager(
        {
            2: Rig(2, True, "Secondary", {"camera": {"backend": "simulated"}}),
        }
    )
    emitted = []
    monkeypatch.setattr(flask_module, "get_rig_manager", lambda: rig_manager)
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, payload, **kwargs: emitted.append((event, payload, kwargs)),
    )

    flask_module._emit_backend("gps_update", {"synced": True})

    assert [event for event, _payload, _kwargs in emitted] == [
        "gps_update",
        "status_update",
        "clock_reset",
    ]
    status_time = emitted[1][1]["time"]
    assert emitted[1][1]["rigs"] == [
        {"rig_id": 1, "name": "RIG 1", "enabled": False},
        {"rig_id": 2, "name": "Secondary", "enabled": True},
        {"rig_id": 3, "name": "RIG 3", "enabled": False},
        {"rig_id": 4, "name": "RIG 4", "enabled": False},
    ]
    clock_reset = emitted[2][1]
    assert isinstance(clock_reset["new_utc_epoch_ms"], int)
    assert isinstance(clock_reset["new_local_epoch_ms"], int)
    assert clock_reset["new_utc_epoch_ms"] == status_time["backend_utc_epoch_ms"]
    assert clock_reset["new_local_epoch_ms"] == status_time["backend_local_epoch_ms"]

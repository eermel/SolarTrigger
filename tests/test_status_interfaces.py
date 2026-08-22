import sys
from types import ModuleType

import pytest

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
    assert payload["circumstances"] == circumstances
    assert payload["capture"] == capture
    assert payload["circumstances"] == state_store.snapshot("circumstances")
    assert payload["capture"] == state_store.snapshot("capture")


def test_on_connect_status_update_exposes_current_circumstances_and_capture(
    status_state, monkeypatch
):
    state_store, circumstances, capture = status_state
    emitted = []
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
    assert payload["circumstances"] == circumstances
    assert payload["capture"] == capture
    assert payload["circumstances"] == state_store.snapshot("circumstances")
    assert payload["capture"] == state_store.snapshot("capture")

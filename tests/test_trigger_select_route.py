import json
import sys
from types import ModuleType

import pytest

from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


def test_trigger_select_updates_persists_and_emits_circumstances(tmp_path, monkeypatch):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    filename = "eclipse_2027.json"
    eclipse_data = {
        "_date": "2027-08-02",
        "title": "Éclipse totale 2027",
        "C1_local": "10:10:00",
        "C2_local": "11:20:00",
        "photo_plan": {"exposure": "1/1000"},
    }
    (configs_dir / filename).write_text(
        json.dumps(eclipse_data), encoding="utf-8"
    )

    state_file = tmp_path / "state.json"
    state_store = StateStore(state_file)
    emitted = []

    monkeypatch.setattr(flask_module, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(flask_module, "JSON_FILE", tmp_path / "todayeclipse.json")
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_state", state_store.data)
    monkeypatch.setattr(flask_module, "_state_lock", state_store.lock)
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, payload: emitted.append((event, payload)),
    )
    monkeypatch.setattr(flask_module, "_append_log", lambda *args, **kwargs: None)

    response = flask_module.app.test_client().post(
        "/api/trigger/select", json={"filename": filename, "dir": "configs"}
    )

    assert response.status_code == 200
    circumstances = response.get_json()["circumstances"]
    assert circumstances == {
        "loaded": True,
        "active_file": filename,
        "meta": {
            "_date": "2027-08-02",
            "title": "Éclipse totale 2027",
            "phases_local": {"C1": "10:10:00", "C2": "11:20:00"},
        },
    }
    assert state_store.snapshot("circumstances") == circumstances
    persisted = json.loads(state_file.read_text(encoding="utf-8"))
    assert persisted["circumstances"] == circumstances

    assert [event for event, _payload in emitted].count("status_update") == 1
    assert [event for event, _payload in emitted].count("eclipse_calculated") == 1
    events = {event: payload for event, payload in emitted}
    assert events["eclipse_calculated"] == {
        "status": "success",
        "data": eclipse_data,
    }
    assert events["status_update"]["circumstances"] == circumstances
    assert set(events["status_update"]["time"]) == {"epoch_ms", "local", "utc"}


def test_trigger_select_camera_updates_persists_and_emits_capture(tmp_path, monkeypatch):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    filename = "camera_test.json"
    camera_data = {
        "_type": "capture",
        "_comment": "Configuration de test",
        "interval_ms": 250,
    }
    (configs_dir / filename).write_text(
        json.dumps(camera_data), encoding="utf-8"
    )

    state_file = tmp_path / "state.json"
    state_store = StateStore(state_file)
    emitted = []

    monkeypatch.setattr(flask_module, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_state", state_store.data)
    monkeypatch.setattr(flask_module, "_state_lock", state_store.lock)
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, payload: emitted.append((event, payload)),
    )
    monkeypatch.setattr(flask_module, "_append_log", lambda *args, **kwargs: None)

    response = flask_module.app.test_client().post(
        "/api/trigger/select_camera", json={"filename": filename}
    )

    assert response.status_code == 200
    payload = response.get_json()
    capture = payload["capture"]
    assert payload["status"] == "ok"
    assert capture == {
        "loaded": True,
        "active_file": filename,
        "meta": {
            "_type": "capture",
            "_comment": "Configuration de test",
        },
    }

    restored = StateStore(state_file)
    assert restored.snapshot("capture")["active_file"] == filename
    assert restored.get("camera_config_file") == filename

    assert len(emitted) == 1
    event, event_payload = emitted[0]
    assert event == "status_update"
    assert event_payload["capture"] == capture
    assert set(event_payload["time"]) == {"epoch_ms", "local", "utc"}

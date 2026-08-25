import json
import sys
from datetime import datetime, timedelta
from types import ModuleType

import pytest

from backend.state_store import StateStore
from backend.trigger_service import TriggerService


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


def _configure_trigger_route(
    tmp_path,
    monkeypatch,
    *,
    circumstances=True,
    capture=True,
    eclipse_date=None,
    camera_subdir="camera_cfg",
):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    camera_filename = "camera.json"
    camera_dir = configs_dir / camera_subdir
    camera_dir.mkdir(parents=True)
    (camera_dir / camera_filename).write_text("{}", encoding="utf-8")

    eclipse_file = tmp_path / "todayeclipse.json"
    eclipse_file.write_text(
        json.dumps(
            {
                "_date": (eclipse_date or datetime.now().astimezone().date()).isoformat(),
                "TSTART": "10:00:00",
                "C1": "10:10:00",
                "C2": "10:20:00",
                "C3": "10:21:00",
                "C4": "10:30:00",
                "TEND": "10:40:00",
            }
        ),
        encoding="utf-8",
    )

    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "gps", {"synced": True, "sync_time": datetime.now().astimezone().isoformat()}
    )
    if circumstances:
        state_store.update_section(
            "circumstances", {"loaded": True, "active_file": eclipse_file.name}
        )
    if capture:
        state_store.update_section(
            "capture", {"loaded": True, "active_file": camera_filename}
        )
        state_store.set("camera_config_file", camera_filename)

    service = TriggerService(
        state_store,
        tmp_path / "eclipse_trigger.py",
        eclipse_file,
        tmp_path / "events.log",
        configs_dir,
        lambda *args: None,
        lambda *args: None,
    )
    monkeypatch.setattr(flask_module, "_trigger_service", service)

    class DummyThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr("backend.trigger_service.threading.Thread", DummyThread)
    return flask_module.app.test_client()


def test_trigger_start_rejects_missing_circumstances(tmp_path, monkeypatch):
    client = _configure_trigger_route(tmp_path, monkeypatch, circumstances=False)

    response = client.post("/api/trigger/start")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "CIRCUMSTANCES_NOT_LOADED",
        "message": "Aucune circonstance d’éclipse sélectionnée",
    }


def test_trigger_start_rejects_missing_capture(tmp_path, monkeypatch):
    client = _configure_trigger_route(tmp_path, monkeypatch, capture=False)

    response = client.post("/api/trigger/start")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "CAPTURE_NOT_LOADED",
        "message": "Aucune configuration de capture sélectionnée",
    }


def test_trigger_start_rejects_invalid_local_date(tmp_path, monkeypatch):
    yesterday = datetime.now().astimezone().date() - timedelta(days=1)
    client = _configure_trigger_route(tmp_path, monkeypatch, eclipse_date=yesterday)

    response = client.post("/api/trigger/start")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "CIRCUMSTANCES_DATE_INVALID",
        "message": "Les circonstances d’éclipse ne correspondent pas à la date locale",
    }


def test_trigger_start_succeeds_when_preconditions_are_met(tmp_path, monkeypatch):
    client = _configure_trigger_route(tmp_path, monkeypatch)

    response = client.post("/api/trigger/start")

    assert response.status_code == 200
    assert response.get_json() == {"status": "started", "mode": "real"}


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
    assert set(events["status_update"]["time"]) == {
        "epoch_ms",
        "backend_utc_epoch_ms",
        "backend_local_epoch_ms",
        "local",
        "utc",
    }


def test_trigger_select_remains_coherent_with_eclipse_list_after_reload(
    tmp_path, monkeypatch
):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    filenames = ("eclipse_2026.json", "eclipse_2027.json")
    for filename in filenames:
        year = filename.removeprefix("eclipse_").removesuffix(".json")
        (configs_dir / filename).write_text(
            json.dumps({"_date": f"{year}-08-02"}),
            encoding="utf-8",
        )

    state_store = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(flask_module, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(flask_module, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(flask_module, "JSON_FILE", tmp_path / "todayeclipse.json")
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_state", state_store.data)
    monkeypatch.setattr(flask_module, "_state_lock", state_store.lock)
    monkeypatch.setattr(flask_module.socketio, "emit", lambda *args: None)
    monkeypatch.setattr(flask_module, "_append_log", lambda *args, **kwargs: None)

    client = flask_module.app.test_client()
    response = client.post(
        "/api/trigger/select",
        json={"filename": filenames[1], "dir": "configs"},
    )

    assert response.status_code == 200
    expected_configs = [
        {"name": filenames[0], "dir": "configs", "active": False},
        {"name": filenames[1], "dir": "configs", "active": False},
    ]
    listed = client.get("/api/configs/list_trigger")
    assert listed.status_code == 200
    assert [
        item for item in listed.get_json()["files"] if item["dir"] == "configs"
    ] == expected_configs

    reloaded = flask_module.app.test_client().get("/api/configs/list_trigger")
    assert reloaded.status_code == 200
    assert [
        item for item in reloaded.get_json()["files"] if item["dir"] == "configs"
    ] == expected_configs


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
    assert set(event_payload["time"]) == {
        "epoch_ms",
        "backend_utc_epoch_ms",
        "backend_local_epoch_ms",
        "local",
        "utc",
    }


def test_trigger_start_accepts_legacy_capture_directory(tmp_path, monkeypatch):
    client = _configure_trigger_route(
        tmp_path,
        monkeypatch,
        camera_subdir="capture",
    )

    response = client.post("/api/trigger/start")

    assert response.status_code == 200
    assert response.get_json() == {"status": "started", "mode": "real"}


def test_boot_restores_valid_persisted_trigger_selections(tmp_path, monkeypatch):
    configs_dir = tmp_path / "configs"
    camera_cfg_dir = configs_dir / "camera_cfg"
    camera_cfg_dir.mkdir(parents=True)

    camera_filename = "camera_boot.json"
    (camera_cfg_dir / camera_filename).write_text("{}", encoding="utf-8")

    today = tmp_path / "todayeclipse.json"
    today.write_text(
        json.dumps(
            {
                "_date": datetime.now().astimezone().date().isoformat(),
                "TSTART": "10:00:00",
                "C1": "10:10:00",
                "C2": "10:20:00",
                "C3": "10:21:00",
                "C4": "10:30:00",
                "TEND": "10:40:00",
            }
        ),
        encoding="utf-8",
    )

    state_file = tmp_path / "state.json"
    persisted = StateStore(state_file)
    persisted.update_section(
        "circumstances",
        {"loaded": True, "active_file": "todayeclipse.json", "meta": {}},
        persist=True,
    )
    persisted.update_section(
        "capture",
        {"loaded": True, "active_file": camera_filename, "meta": {}},
        persist=True,
    )
    persisted.set("camera_config_file", camera_filename, persist=True)

    restored = StateStore(state_file)

    monkeypatch.setattr(flask_module, "_state_store", restored)
    monkeypatch.setattr(flask_module, "_state", restored.data)
    monkeypatch.setattr(flask_module, "_state_lock", restored.lock)
    monkeypatch.setattr(flask_module, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(flask_module, "JSON_FILE", today)

    flask_module._restore_persisted_trigger_selections()

    assert restored.snapshot("circumstances")["loaded"] is True
    assert restored.snapshot("capture")["loaded"] is True


def test_boot_does_not_restore_missing_capture_file(tmp_path, monkeypatch):
    configs_dir = tmp_path / "configs"
    (configs_dir / "camera_cfg").mkdir(parents=True)

    today = tmp_path / "todayeclipse.json"
    today.write_text("{}", encoding="utf-8")

    state_file = tmp_path / "state.json"
    persisted = StateStore(state_file)
    persisted.update_section(
        "circumstances",
        {"loaded": True, "active_file": "todayeclipse.json", "meta": {}},
        persist=True,
    )
    persisted.update_section(
        "capture",
        {"loaded": True, "active_file": "missing.json", "meta": {}},
        persist=True,
    )
    persisted.set("camera_config_file", "missing.json", persist=True)

    restored = StateStore(state_file)

    monkeypatch.setattr(flask_module, "_state_store", restored)
    monkeypatch.setattr(flask_module, "_state", restored.data)
    monkeypatch.setattr(flask_module, "_state_lock", restored.lock)
    monkeypatch.setattr(flask_module, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(flask_module, "JSON_FILE", today)

    flask_module._restore_persisted_trigger_selections()

    assert restored.snapshot("circumstances")["loaded"] is True
    assert restored.snapshot("capture")["loaded"] is False

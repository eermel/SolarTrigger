import json
import sys
from types import ModuleType

import pytest

from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


def camera_data(**overrides):
    data = {
        "_type": "capture",
        "phases": {
            phase: {"shutter_min": "1/2", "shutter_max": "1/1000"}
            for phase in ("partial", "diamond_ring", "totality")
        },
    }
    data.update(overrides)
    return data


@pytest.fixture
def save_routes(tmp_path, monkeypatch):
    configs_dir = tmp_path / "configs"
    state_store = StateStore(tmp_path / "state.json")
    emitted = []

    monkeypatch.setattr(flask_module, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_state", state_store.data)
    monkeypatch.setattr(flask_module, "_state_lock", state_store.lock)
    monkeypatch.setattr(flask_module, "_append_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, payload: emitted.append((event, payload)),
    )

    return flask_module.app.test_client(), configs_dir, state_store, emitted


@pytest.mark.parametrize(
    ("endpoint", "requested_filename", "saved_filename", "data"),
    [
        (
            "/api/configs/save",
            "eclipse_2027",
            "eclipse_2027.json",
            {"_date": "2027-08-02", "title": "Éclipse totale 2027"},
        ),
        (
            "/api/configs/save_camera",
            "test",
            "camera_test.json",
            camera_data(iso=100),
        ),
    ],
)
def test_config_save_new_file_returns_summary_without_state_update(
    save_routes,
    monkeypatch,
    endpoint,
    requested_filename,
    saved_filename,
    data,
):
    client, configs_dir, state_store, emitted = save_routes
    initial_state = state_store.snapshot()
    if endpoint == "/api/configs/save":
        monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: data)
        body = {"filename": requested_filename}
    else:
        body = {"filename": requested_filename, "data": data}

    response = client.post(endpoint, json=body)

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "filename": saved_filename,
        "saved": {"filename": saved_filename, "data": data},
    }
    assert json.loads((configs_dir / saved_filename).read_text(encoding="utf-8")) == data
    assert state_store.snapshot() == initial_state
    assert emitted == []


@pytest.mark.parametrize(
    "filename",
    [
        "/abs.json",
        "../escape.json",
        "dir/name.json",
        "..",
        "foo/..",
        "../foo",
        "/foo",
    ],
)
def test_config_save_rejects_invalid_filename_without_writing(
    save_routes, monkeypatch, filename
):
    client, configs_dir, state_store, emitted = save_routes
    data = {"_date": "2027-08-02", "title": "Éclipse totale 2027"}
    monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: data)
    initial_state = state_store.snapshot()

    response = client.post("/api/configs/save", json={"filename": filename})

    assert response.status_code == 400
    assert response.get_json() == {"error": "Nom de fichier invalide"}
    assert not configs_dir.exists()
    assert state_store.snapshot() == initial_state
    assert emitted == []


def test_config_save_appends_json_extension(save_routes, monkeypatch):
    client, configs_dir, state_store, emitted = save_routes
    data = {"_date": "2027-08-02", "title": "Éclipse totale 2027"}
    monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: data)
    initial_state = state_store.snapshot()

    response = client.post("/api/configs/save", json={"filename": "my_eclipse"})

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "filename": "my_eclipse.json",
        "saved": {"filename": "my_eclipse.json", "data": data},
    }
    assert json.loads(
        (configs_dir / "my_eclipse.json").read_text(encoding="utf-8")
    ) == data
    assert state_store.snapshot() == initial_state
    assert emitted == []


def test_config_save_todayeclipse_default_flow_without_state_update(
    save_routes, monkeypatch
):
    client, configs_dir, state_store, emitted = save_routes
    filename = "todayeclipse.json"
    data = {
        "_date": "2027-08-02",
        "_date_utc": "2027-08-02",
        "title": "Éclipse totale 2027",
        "_type": "total",
    }
    monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: data)
    initial_state = state_store.snapshot()

    response = client.post("/api/configs/save", json={"filename": filename})

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "filename": filename,
        "saved": {"filename": filename, "data": data},
    }
    assert json.loads((configs_dir / filename).read_text(encoding="utf-8")) == data
    assert state_store.snapshot() == initial_state
    assert emitted == []


@pytest.mark.parametrize(
    ("endpoint", "filename", "body"),
    [
        ("/api/configs/save", "existing.json", {"filename": "existing.json"}),
        (
            "/api/configs/save_camera",
            "camera_existing.json",
            {"filename": "existing.json", "data": camera_data(iso=200)},
        ),
    ],
)
def test_config_save_collision_without_overwrite_returns_409(
    save_routes, monkeypatch, endpoint, filename, body
):
    client, configs_dir, state_store, emitted = save_routes
    configs_dir.mkdir()
    original = {"original": True}
    (configs_dir / filename).write_text(json.dumps(original), encoding="utf-8")
    if endpoint == "/api/configs/save":
        monkeypatch.setattr(
            flask_module, "_load_eclipse_json", lambda: {"replacement": True}
        )
    initial_state = state_store.snapshot()

    response = client.post(endpoint, json=body)

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "Le fichier existe déjà",
        "filename": filename,
    }
    assert json.loads((configs_dir / filename).read_text(encoding="utf-8")) == original
    assert state_store.snapshot() == initial_state
    assert emitted == []


def test_config_save_overwrites_active_circumstances_and_emits_status(
    save_routes, monkeypatch
):
    client, configs_dir, state_store, emitted = save_routes
    filename = "todayeclipse.json"
    data = {
        "_date": "2027-08-02",
        "_date_utc": "2027-08-02",
        "title": "Éclipse totale 2027",
        "_type": "total",
        "C1_local": "10:10:00",
        "TMAX_local": "11:30:00",
    }
    configs_dir.mkdir()
    (configs_dir / filename).write_text("{}", encoding="utf-8")
    state_store.update_section(
        "circumstances",
        {"loaded": True, "active_file": filename, "meta": {"title": "Ancien"}},
    )
    monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: data)

    response = client.post(
        "/api/configs/save", json={"filename": filename, "overwrite": True}
    )

    assert response.status_code == 200
    payload = response.get_json()
    circumstances = state_store.snapshot("circumstances")
    assert payload == {
        "status": "ok",
        "filename": filename,
        "circumstances": circumstances,
    }
    assert circumstances == {
        "loaded": True,
        "active_file": filename,
        "meta": {
            "_date": "2027-08-02",
            "_date_utc": "2027-08-02",
            "title": "Éclipse totale 2027",
            "_type": "total",
            "phases_local": {"C1": "10:10:00", "TMAX": "11:30:00"},
        },
    }
    assert emitted == [
        (
            "status_update",
            {"circumstances": circumstances, "time": emitted[0][1]["time"]},
        )
    ]
    assert set(emitted[0][1]["time"]) == {
        "epoch_ms",
        "backend_utc_epoch_ms",
        "backend_local_epoch_ms",
        "local",
        "utc",
    }
    assert json.loads((configs_dir / filename).read_text(encoding="utf-8")) == data


def test_config_save_camera_overwrites_active_capture_and_emits_status(save_routes):
    client, configs_dir, state_store, emitted = save_routes
    filename = "camera_active.json"
    data = camera_data(_comment="Réglages totalité", iso=400)
    configs_dir.mkdir()
    (configs_dir / filename).write_text("{}", encoding="utf-8")
    state_store.update_section(
        "capture",
        {"loaded": True, "active_file": filename, "meta": {"_comment": "Ancien"}},
    )

    response = client.post(
        "/api/configs/save_camera",
        json={"filename": filename, "data": data, "overwrite": True},
    )

    assert response.status_code == 200
    payload = response.get_json()
    capture = state_store.snapshot("capture")
    assert payload == {"status": "ok", "filename": filename, "capture": capture}
    assert capture == {
        "loaded": True,
        "active_file": filename,
        "meta": {"_type": "capture", "_comment": "Réglages totalité"},
    }
    assert emitted == [
        ("status_update", {"capture": capture, "time": emitted[0][1]["time"]})
    ]
    assert set(emitted[0][1]["time"]) == {
        "epoch_ms",
        "backend_utc_epoch_ms",
        "backend_local_epoch_ms",
        "local",
        "utc",
    }
    assert json.loads((configs_dir / filename).read_text(encoding="utf-8")) == data


@pytest.mark.parametrize(
    ("phase_name", "field", "value"),
    [
        ("partial", "shutter_min", "1/3"),
        ("diamond_ring", "shutter_max", "1/6400"),
        ("totality", "shutter_min", None),
        ("partial", "shutter_min", "1/2000"),
        ("diamond_ring", "step_ev", 0.5),
        ("totality", "step_ev", "1.0"),
    ],
)
def test_config_save_camera_rejects_invalid_phase_values_without_writing(
    save_routes, phase_name, field, value
):
    client, configs_dir, _state_store, _emitted = save_routes
    data = camera_data()
    if value is None:
        del data["phases"][phase_name][field]
    else:
        data["phases"][phase_name][field] = value

    response = client.post(
        "/api/configs/save_camera",
        json={"filename": "invalid", "data": data},
    )

    assert response.status_code == 400
    assert "error" in response.get_json()
    assert not (configs_dir / "camera_invalid.json").exists()


def test_config_save_camera_persists_default_step_ev_for_every_phase(save_routes):
    client, configs_dir, _state_store, _emitted = save_routes
    data = camera_data()

    response = client.post(
        "/api/configs/save_camera",
        json={"filename": "defaults", "data": data},
    )

    assert response.status_code == 200
    saved = json.loads(
        (configs_dir / "camera_defaults.json").read_text(encoding="utf-8")
    )
    assert all(phase["step_ev"] == 1.0 for phase in saved["phases"].values())

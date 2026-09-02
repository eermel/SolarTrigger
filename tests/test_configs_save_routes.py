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


def canonical_camera_data(data):
    canonical = json.loads(json.dumps(data))
    for phase_name in ("partial", "diamond_ring", "totality"):
        canonical["phases"][phase_name].setdefault("step_ev", 1.0)
    return canonical


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


def test_config_list_camera_returns_only_sorted_camera_cfg_files(save_routes):
    client, configs_dir, _state_store, _emitted = save_routes
    camera_configs_dir = configs_dir / "camera_cfg"
    capture_dir = configs_dir / "capture"
    camera_configs_dir.mkdir(parents=True)
    capture_dir.mkdir()
    (camera_configs_dir / "camera_zulu.json").write_text("{}", encoding="utf-8")
    (camera_configs_dir / "camera_alpha.json").write_text("{}", encoding="utf-8")
    (capture_dir / "camera_capture.json").write_text("{}", encoding="utf-8")
    (configs_dir / "camera_root.json").write_text("{}", encoding="utf-8")

    response = client.get("/api/configs/list_camera")

    assert response.status_code == 200
    assert response.get_json() == {
        "files": ["camera_alpha.json", "camera_zulu.json"]
    }


def test_camera_load_and_select_prefer_camera_cfg_then_fall_back_to_capture(
    save_routes,
):
    client, configs_dir, _state_store, _emitted = save_routes
    camera_configs_dir = configs_dir / "camera_cfg"
    capture_dir = configs_dir / "capture"
    camera_configs_dir.mkdir(parents=True)
    capture_dir.mkdir()

    preferred_filename = "camera_shared.json"
    legacy_filename = "camera_legacy.json"
    preferred_data = camera_data(_comment="camera_cfg")
    shadowed_legacy_data = camera_data(_comment="capture shadow")
    legacy_data = camera_data(_comment="capture")
    (camera_configs_dir / preferred_filename).write_text(
        json.dumps(preferred_data), encoding="utf-8"
    )
    (capture_dir / preferred_filename).write_text(
        json.dumps(shadowed_legacy_data), encoding="utf-8"
    )
    (capture_dir / legacy_filename).write_text(
        json.dumps(legacy_data), encoding="utf-8"
    )

    app_context = getattr(flask_module.app, "app_context", None)
    if app_context is None:
        preferred_load = flask_module.api_configs_load_camera(preferred_filename)
        legacy_load = flask_module.api_configs_load_camera(legacy_filename)
    else:
        with app_context():
            preferred_load = flask_module.api_configs_load_camera(preferred_filename)
            legacy_load = flask_module.api_configs_load_camera(legacy_filename)

    preferred_select = client.post(
        "/api/trigger/select_camera", json={"filename": preferred_filename}
    )
    legacy_select = client.post(
        "/api/trigger/select_camera", json={"filename": legacy_filename}
    )
    listed = client.get("/api/configs/list_camera")

    preferred_load_data = (
        preferred_load.get_json()
        if hasattr(preferred_load, "get_json")
        else preferred_load
    )
    legacy_load_data = (
        legacy_load.get_json() if hasattr(legacy_load, "get_json") else legacy_load
    )
    assert preferred_load_data == preferred_data
    assert preferred_select.status_code == 200
    assert preferred_select.get_json()["capture"]["meta"]["_comment"] == "camera_cfg"
    assert legacy_load_data == legacy_data
    assert legacy_select.status_code == 200
    assert legacy_select.get_json()["capture"]["meta"]["_comment"] == "capture"
    assert listed.status_code == 200
    assert listed.get_json() == {"files": [preferred_filename]}


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

    expected_data = (
        canonical_camera_data(data)
        if endpoint == "/api/configs/save_camera"
        else data
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "filename": saved_filename,
        "saved": {"filename": saved_filename, "data": expected_data},
    }
    saved_path = (
        configs_dir / "circumstances" / saved_filename
        if endpoint == "/api/configs/save"
        else configs_dir / "camera_cfg" / saved_filename
    )
    assert json.loads(saved_path.read_text(encoding="utf-8")) == expected_data
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
        (configs_dir / "circumstances" / "my_eclipse.json").read_text(
            encoding="utf-8"
        )
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
    destination_dir = (
        configs_dir / "circumstances"
        if endpoint == "/api/configs/save"
        else configs_dir / "camera_cfg"
    )
    destination_dir.mkdir(parents=True)
    original = {"original": True}
    destination = destination_dir / filename
    destination.write_text(json.dumps(original), encoding="utf-8")
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
    assert json.loads(destination.read_text(encoding="utf-8")) == original
    assert state_store.snapshot() == initial_state
    assert emitted == []


@pytest.mark.parametrize("requested_filename", ["todayeclipse.json", "todayeclipse"])
def test_config_save_overwrites_active_circumstances_and_emits_status(
    save_routes, monkeypatch, requested_filename
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
        "/api/configs/save",
        json={"filename": requested_filename, "overwrite": True},
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
    assert len(emitted) == 1
    event, status_payload = emitted[0]
    assert event == "status_update"
    assert status_payload["circumstances"] == circumstances
    assert len(status_payload["rigs"]) == 4
    assert set(status_payload["time"]) == {
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
    camera_configs_dir = configs_dir / "camera_cfg"
    camera_configs_dir.mkdir(parents=True)
    (camera_configs_dir / filename).write_text("{}", encoding="utf-8")
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
    assert len(emitted) == 1
    event, status_payload = emitted[0]
    assert event == "status_update"
    assert status_payload["capture"] == capture
    assert len(status_payload["rigs"]) == 4
    assert set(status_payload["time"]) == {
        "epoch_ms",
        "backend_utc_epoch_ms",
        "backend_local_epoch_ms",
        "local",
        "utc",
    }
    assert json.loads(
        (camera_configs_dir / filename).read_text(encoding="utf-8")
    ) == canonical_camera_data(data)


def test_config_save_camera_writes_only_to_camera_cfg(save_routes):
    client, configs_dir, _state_store, _emitted = save_routes
    filename = "camera_scoped.json"
    data = camera_data(iso=200)

    response = client.post(
        "/api/configs/save_camera",
        json={"filename": filename, "data": data},
    )

    assert response.status_code == 200
    assert json.loads(
        (configs_dir / "camera_cfg" / filename).read_text(encoding="utf-8")
    ) == canonical_camera_data(data)
    assert not (configs_dir / filename).exists()


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
    assert not (configs_dir / "camera_cfg" / "camera_invalid.json").exists()


def test_config_save_camera_persists_default_step_ev_for_every_phase(save_routes):
    client, configs_dir, _state_store, _emitted = save_routes
    data = camera_data()

    response = client.post(
        "/api/configs/save_camera",
        json={"filename": "defaults", "data": data},
    )

    assert response.status_code == 200
    saved = json.loads(
        (configs_dir / "camera_cfg" / "camera_defaults.json").read_text(
            encoding="utf-8"
        )
    )
    assert all(phase["step_ev"] == 1.0 for phase in saved["phases"].values())

def test_exposure_opt_save_strips_legacy_optics(save_routes):
    client, configs_dir, state_store, emitted = save_routes
    initial_state = state_store.snapshot()

    data = {
        "schema_version": 1,
        "config_type": "exposure_optimization",
        "atmospheric_attenuation_enabled": True,
        "rigs": [
            {
                "rig_id": rig_id,
                "optics": {
                    "focal_length_mm": 400 + rig_id,
                },
                "photo": {
                    "anti_trailing_enabled": True,
                    "motion_tolerance_px": 1.0,
                    "iso_compensation_enabled": True,
                    "iso_max": 6400,
                },
            }
            for rig_id in range(1, 5)
        ],
    }

    response = client.post(
        "/api/configs/save_exposure_opt",
        json={
            "filename": "legacy_optics",
            "data": data,
            "overwrite": False,
        },
    )

    assert response.status_code == 200

    result = response.get_json()
    filename = result["filename"]

    saved_path = configs_dir / "exposure_opt" / filename
    saved = json.loads(saved_path.read_text(encoding="utf-8"))

    assert saved["config_type"] == "exposure_optimization"
    assert saved["schema_version"] == 1

    for rig in saved["rigs"]:
        assert "optics" not in rig
        assert "photo" in rig

    assert state_store.snapshot() == initial_state
    assert emitted == []

import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


class _FakeWidget:
    def __init__(self, value):
        self._value = value

    def get_value(self):
        return self._value


class _FakeConfig:
    def __init__(self, model):
        self._values = {
            "batterylevel": "87%",
            "model": model,
        }

    def get_child_by_name(self, name):
        value = self._values.get(name)
        if value is None:
            raise KeyError(name)
        return _FakeWidget(value)


class _FakeCamera:
    def __init__(self, abilities_model=None, config_model=None):
        self._abilities_model = abilities_model
        self._config = _FakeConfig(config_model)

    def init(self):
        return None

    def exit(self):
        return None

    def get_abilities(self):
        if self._abilities_model is None:
            raise RuntimeError("abilities model unavailable")
        return SimpleNamespace(model=self._abilities_model)

    def get_config(self):
        return self._config


@pytest.fixture
def camera_api(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_state", state_store.data)
    monkeypatch.setattr(flask_module, "_state_lock", state_store.lock)
    monkeypatch.setattr(flask_module, "_append_log", lambda *args, **kwargs: None)
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), monkeypatch


def _install_camera(monkeypatch, model, source="abilities"):
    if source == "abilities":
        camera = _FakeCamera(abilities_model=model)
    else:
        camera = _FakeCamera(config_model=model)
    monkeypatch.setattr(flask_module.gp, "Camera", lambda: camera)


def _assert_separated(camera, brand, model, *, connected=None):
    if connected is not None:
        assert camera["connected"] is connected
    assert camera["brand"] == brand
    assert camera["model"] == model
    assert camera["brand"] != camera["model"]
    assert model in camera["model"]


@pytest.mark.parametrize(
    ("model", "source"),
    [
        ("ILCE-7M5 (PC Control)", "abilities"),
        ("Sony ILCE-7M5 (PC Control)", "config"),
    ],
)
def test_camera_status_sony_brand_model_separation(camera_api, model, source):
    client, monkeypatch = camera_api
    _install_camera(monkeypatch, model, source)

    response = client.get("/api/status")

    assert response.status_code == 200
    _assert_separated(response.get_json()["camera"], "SONY", model, connected=True)


@pytest.mark.parametrize(
    ("model", "source"),
    [
        ("ILCE-7M5 (PC Control)", "abilities"),
        ("Sony ILCE-7M5 (PC Control)", "config"),
    ],
)
def test_camera_probe_sony_brand_model_separation(camera_api, model, source):
    client, monkeypatch = camera_api
    _install_camera(monkeypatch, model, source)

    response = client.post("/api/camera/probe")

    assert response.status_code == 200
    _assert_separated(response.get_json(), "SONY", model)


def test_camera_status_nikon_brand_model_separation(camera_api):
    client, monkeypatch = camera_api
    model = "NIKON D850"
    _install_camera(monkeypatch, model)

    response = client.get("/api/status")

    assert response.status_code == 200
    _assert_separated(response.get_json()["camera"], "NIKON", model, connected=True)


def test_camera_probe_nikon_brand_model_separation(camera_api):
    client, monkeypatch = camera_api
    model = "NIKON D850"
    _install_camera(monkeypatch, model)

    response = client.post("/api/camera/probe")

    assert response.status_code == 200
    _assert_separated(response.get_json(), "NIKON", model)


def test_camera_status_idempotent_brand_model(camera_api):
    client, monkeypatch = camera_api
    model = "Sony ILCE-7M5 (PC Control)"
    _install_camera(monkeypatch, model)

    first = client.get("/api/status")
    second = client.get("/api/status")

    assert first.status_code == second.status_code == 200
    first_camera = first.get_json()["camera"]
    second_camera = second.get_json()["camera"]
    _assert_separated(first_camera, "SONY", model, connected=True)
    _assert_separated(second_camera, "SONY", model, connected=True)
    assert second_camera == first_camera


def test_camera_brand_model_persist_across_status_and_probe_calls(camera_api):
    client, monkeypatch = camera_api
    model = "Sony ILCE-7M5 (PC Control)"
    _install_camera(monkeypatch, model)

    for _reconnection in range(2):
        status_before_probe = client.get("/api/status")
        assert status_before_probe.status_code == 200
        _assert_separated(
            status_before_probe.get_json()["camera"],
            "SONY",
            model,
            connected=True,
        )
        _assert_separated(
            flask_module._state_store.snapshot("camera"),
            "SONY",
            model,
            connected=True,
        )

        probe = client.post("/api/camera/probe")
        assert probe.status_code == 200
        _assert_separated(probe.get_json(), "SONY", model)
        _assert_separated(
            flask_module._state_store.snapshot("camera"),
            "SONY",
            model,
            connected=False,
        )

        status_after_probe = client.get("/api/status")
        assert status_after_probe.status_code == 200
        _assert_separated(
            status_after_probe.get_json()["camera"],
            "SONY",
            model,
            connected=True,
        )
        _assert_separated(
            flask_module._state_store.snapshot("camera"),
            "SONY",
            model,
            connected=True,
        )

    persisted_store = StateStore(flask_module._state_store.path)
    _assert_separated(
        persisted_store.snapshot("camera"),
        "SONY",
        model,
        connected=True,
    )

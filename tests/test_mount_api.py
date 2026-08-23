import sys
from types import ModuleType
import importlib.util

import pytest

from backend.state_store import StateStore
from services.mount_service import MountService


sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

if "flask" not in sys.modules and importlib.util.find_spec("flask") is None:
    class Response:
        def __init__(self, value):
            if isinstance(value, tuple):
                self._json, self.status_code = value
            else:
                self._json, self.status_code = value, 200

        def get_json(self):
            return self._json

    class Request:
        json = None

        def get_json(self, silent=False):
            return self.json

    request = Request()

    class _TestClient:
        def __init__(self, routes):
            self.routes = routes

        def _call(self, path, method, json=None):
            request.json = json
            try:
                return Response(self.routes[(path, method)]())
            finally:
                request.json = None

        def get(self, path, **kwargs):
            return self._call(path, "GET")

        def post(self, path, json=None, **kwargs):
            return self._call(path, "POST", json=json)

    class Flask:
        def __init__(self, *args, **kwargs):
            self.config = {}
            self.routes = {}

        def route(self, path, methods=None, **kwargs):
            def register(function):
                for method in methods or ("GET",):
                    self.routes[(path, method)] = function
                return function
            return register

        def test_client(self):
            return _TestClient(self.routes)

    flask_module_stub = ModuleType("flask")
    flask_module_stub.Flask = Flask
    flask_module_stub.jsonify = lambda value: value
    flask_module_stub.request = request
    flask_module_stub.send_from_directory = lambda *args, **kwargs: None
    sys.modules["flask"] = flask_module_stub

if ("flask_socketio" not in sys.modules
        and importlib.util.find_spec("flask_socketio") is None):
    socketio_module = ModuleType("flask_socketio")

    class SocketIO:
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            pass

        def on(self, *args, **kwargs):
            return lambda function: function

    socketio_module.SocketIO = SocketIO
    socketio_module.emit = lambda *args, **kwargs: None
    sys.modules["flask_socketio"] = socketio_module

import flask_app.app as flask_module


class FakeMountPlugin:
    def __init__(self, capabilities=True):
        self.connected = False
        self.move_rate = None
        self.moving = False
        self.direction = None
        self.capabilities = capabilities

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def move(self, direction):
        self.moving = True
        self.direction = direction

    def stop(self):
        self.moving = False
        self.direction = None

    def set_speed(self, speed):
        self.move_rate = speed

    def status(self):
        return {
            "connected": self.connected,
            "moving": self.moving,
            "direction": self.direction,
            "move_rate": self.move_rate,
        }

    def get_slew_speed_capabilities(self):
        if not self.capabilities:
            return None
        return {
            "kind": "discrete",
            "values": [
                {"value": 0.5, "label": "0.5"},
                {"value": 1.0, "label": "1.0"},
                {"value": 2.0, "label": "2.0"},
            ],
        }


@pytest.fixture
def mount_api(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    plugin = FakeMountPlugin()

    def load_mount(plugin_id, log_fn=print, config=None):
        assert plugin_id == "fake"
        return plugin

    service = MountService(state_store, log_fn=lambda _message: None,
                           plugin_loader=load_mount)
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_mount_service", service)
    flask_module.app.config.update(TESTING=True)
    yield flask_module.app.test_client(), state_store, plugin
    service.close()


def test_mount_status_reports_plugin_state_and_speed_capabilities(mount_api):
    client, _state_store, _plugin = mount_api

    response = client.get("/api/mount/status")

    assert response.status_code == 200
    assert response.get_json() == {
        "active": True,
        "connected": True,
        "moving": False,
        "direction": None,
        "homing": False,
        "slew_speed": None,
        "slew_speed_caps": {
            "kind": "discrete",
            "values": [
                {"value": 0.5, "label": "0.5"},
                {"value": 1.0, "label": "1.0"},
                {"value": 2.0, "label": "2.0"},
            ],
        },
        "plugin": "fake",
    }


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"speed": True}, {"speed": "1.0"}],
)
def test_mount_speed_rejects_invalid_payloads(mount_api, payload):
    client, _state_store, _plugin = mount_api

    response = client.post("/api/mount/speed", json=payload)

    assert response.status_code == 400


def test_mount_speed_rejects_value_outside_capabilities(mount_api):
    client, _state_store, plugin = mount_api

    response = client.post("/api/mount/speed", json={"speed": 1.5})

    assert response.status_code == 400
    assert plugin.move_rate is None


def test_mount_speed_rejects_plugin_without_capabilities(mount_api):
    client, _state_store, plugin = mount_api
    plugin.capabilities = False

    response = client.post("/api/mount/speed", json={"speed": 1.0})

    assert response.status_code == 400
    assert plugin.move_rate is None


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0])
def test_mount_speed_accepts_supported_values(mount_api, speed):
    client, _state_store, plugin = mount_api

    response = client.post("/api/mount/speed", json={"speed": speed})

    assert response.status_code == 200
    assert response.get_json()["slew_speed"] == speed
    assert plugin.move_rate == speed


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"direction": "up"}, {"direction": 1}]
)
def test_mount_slew_start_rejects_invalid_direction(mount_api, payload):
    client, _state_store, plugin = mount_api

    response = client.post("/api/mount/slew/start", json=payload)

    assert response.status_code == 400
    assert plugin.moving is False


def test_mount_slew_start_and_stop_update_motion_state(mount_api):
    client, _state_store, plugin = mount_api

    started = client.post(
        "/api/mount/slew/start", json={"direction": "west"}
    )

    assert started.status_code == 200
    assert started.get_json()["moving"] is True
    assert started.get_json()["direction"] == "west"
    assert plugin.moving is True
    assert plugin.direction == "west"

    stopped = client.post("/api/mount/slew/stop")

    assert stopped.status_code == 200
    assert stopped.get_json()["moving"] is False
    assert stopped.get_json()["direction"] is None
    assert plugin.moving is False
    assert plugin.direction is None


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("get", "/api/mount/status", None),
        ("post", "/api/mount/speed", {"speed": 1.0}),
        ("post", "/api/mount/slew/start", {"direction": "north"}),
        ("post", "/api/mount/slew/stop", None),
    ],
)
def test_mount_endpoints_reject_inactive_device(
    mount_api, method, path, payload
):
    client, state_store, plugin = mount_api
    state_store.update_section(
        "devices", {"mount": {"plugin": "none", "active": False}}
    )

    response = getattr(client, method)(path, json=payload)

    assert response.status_code == 409
    assert response.get_json()["code"] == "DEVICE_INACTIVE"
    assert response.get_json()["category"] == "mount"
    assert plugin.connected is False

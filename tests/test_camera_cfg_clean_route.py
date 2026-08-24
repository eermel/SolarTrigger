import importlib.util
import sys
from importlib.machinery import ModuleSpec
from types import ModuleType

import pytest


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

    class StubClient:
        def __init__(self, routes):
            self.routes = routes

        def get(self, path, json=None):
            request.json = json
            return Response(self.routes[(path, "GET")]())

        def post(self, path, json=None):
            request.json = json
            return Response(self.routes[(path, "POST")]())

    class Flask:
        def __init__(self, *_args, **_kwargs):
            self.config = {}
            self.routes = {}

        def route(self, path, methods=None, **_kwargs):
            def register(function):
                for method in methods or ("GET",):
                    self.routes[(path, method)] = function
                return function
            return register

        def test_client(self):
            return StubClient(self.routes)

    flask_stub = ModuleType("flask")
    flask_stub.__spec__ = ModuleSpec("flask", loader=None)
    flask_stub.Flask = Flask
    flask_stub.jsonify = lambda value: value
    flask_stub.request = request
    flask_stub.send_from_directory = lambda *_args, **_kwargs: None
    sys.modules["flask"] = flask_stub

if ("flask_socketio" not in sys.modules
        and importlib.util.find_spec("flask_socketio") is None):
    class SocketIO:
        def __init__(self, *_args, **_kwargs):
            pass

        def emit(self, *_args, **_kwargs):
            pass

        def on(self, *_args, **_kwargs):
            return lambda function: function

    socketio_stub = ModuleType("flask_socketio")
    socketio_stub.__spec__ = ModuleSpec("flask_socketio", loader=None)
    socketio_stub.SocketIO = SocketIO
    socketio_stub.emit = lambda *_args, **_kwargs: None
    sys.modules["flask_socketio"] = socketio_stub

gphoto2_stub = ModuleType("gphoto2")
gphoto2_stub.__spec__ = ModuleSpec("gphoto2", loader=None)
sys.modules.setdefault("gphoto2", gphoto2_stub)

import flask_app.app as flask_module


def _configure_clean_route(tmp_path, monkeypatch):
    configs_dir = tmp_path / "configs"
    monkeypatch.setattr(flask_module, "CONFIGS_DIR", configs_dir)
    return flask_module.app.test_client(), configs_dir


def test_clean_deletes_only_first_level_json_files(tmp_path, monkeypatch):
    client, configs_dir = _configure_clean_route(tmp_path, monkeypatch)
    camera_cfg_dir = configs_dir / "camera_cfg"
    camera_cfg_dir.mkdir(parents=True)

    deleted_files = [
        camera_cfg_dir / "alpha.json",
        camera_cfg_dir / "bravo.JSON",
    ]
    for path in deleted_files:
        path.write_text("{}", encoding="utf-8")

    preserved_files = [
        camera_cfg_dir / "notes.txt",
        configs_dir / "root.json",
        tmp_path / "outside.json",
        camera_cfg_dir / "nested" / "inside.json",
    ]
    for path in preserved_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    json_symlink = camera_cfg_dir / "linked.json"
    json_symlink.symlink_to(tmp_path / "outside.json")

    response = client.post("/api/configs/camera_cfg/clean")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "deleted": 2, "errors": []}
    assert all(not path.exists() for path in deleted_files)
    assert all(path.is_file() for path in preserved_files)
    assert json_symlink.is_symlink()


@pytest.mark.parametrize("create_directory", [False, True])
def test_clean_empty_or_missing_directory_returns_zero(
    tmp_path, monkeypatch, create_directory
):
    client, configs_dir = _configure_clean_route(tmp_path, monkeypatch)
    if create_directory:
        (configs_dir / "camera_cfg").mkdir(parents=True)

    response = client.post("/api/configs/camera_cfg/clean")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "deleted": 0, "errors": []}

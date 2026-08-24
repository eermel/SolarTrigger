import importlib.util
import sys
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
    socketio_stub.SocketIO = SocketIO
    socketio_stub.emit = lambda *_args, **_kwargs: None
    sys.modules["flask_socketio"] = socketio_stub

sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


def _configure_clean_route(tmp_path, monkeypatch):
    configs_dir = tmp_path / "configs"
    trigger_dir = tmp_path / "trigger"
    monkeypatch.setattr(flask_module, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(flask_module, "TRIGGER_DIR", trigger_dir)
    return flask_module.app.test_client(), configs_dir, trigger_dir


def test_clean_deletes_only_first_level_json_files(tmp_path, monkeypatch):
    client, configs_dir, trigger_dir = _configure_clean_route(tmp_path, monkeypatch)
    circumstances_dir = configs_dir / "circumstances"
    circumstances_dir.mkdir(parents=True)

    deleted_files = [
        circumstances_dir / "alpha.json",
        circumstances_dir / "bravo.JSON",
    ]
    for path in deleted_files:
        path.write_text("{}", encoding="utf-8")

    preserved_files = [
        circumstances_dir / "notes.txt",
        configs_dir / "root.json",
        configs_dir / "registry.json",
        tmp_path / "outside.json",
        tmp_path / "data" / "eclipses" / "catalog.json",
        trigger_dir / "todayeclipse.json",
        circumstances_dir / "nested" / "inside.json",
    ]
    for path in preserved_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    response = client.post("/api/configs/circumstances/clean")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "deleted": 2, "errors": []}
    assert all(not path.exists() for path in deleted_files)
    assert all(path.is_file() for path in preserved_files)


@pytest.mark.parametrize("create_directory", [False, True])
def test_clean_empty_or_missing_directory_returns_zero(
    tmp_path, monkeypatch, create_directory
):
    client, configs_dir, _trigger_dir = _configure_clean_route(tmp_path, monkeypatch)
    if create_directory:
        (configs_dir / "circumstances").mkdir(parents=True)

    response = client.post("/api/configs/circumstances/clean")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "deleted": 0, "errors": []}

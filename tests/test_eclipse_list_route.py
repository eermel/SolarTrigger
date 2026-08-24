import json
import importlib.util
import sys
from types import ModuleType

from backend.state_store import StateStore


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

        def get(self, path):
            return Response(self.routes[(path, "GET")]())

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


def _write_json(path, data=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data or {"_date": "2027-08-02"}), encoding="utf-8")


def _configure_list_route(tmp_path, monkeypatch, *, active_file=None):
    configs_dir = tmp_path / "configs"
    configs_dir.mkdir()
    json_file = tmp_path / "todayeclipse.json"
    state_store = StateStore(tmp_path / "state.json")
    if active_file is not None:
        state_store.update_section(
            "circumstances", {"loaded": True, "active_file": active_file}
        )

    monkeypatch.setattr(flask_module, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(flask_module, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(flask_module, "JSON_FILE", json_file)
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    return flask_module.app.test_client(), configs_dir, json_file


def test_list_eclipse_returns_only_authorized_json_files_in_stable_order(
    tmp_path, monkeypatch
):
    client, configs_dir, json_file = _configure_list_route(
        tmp_path, monkeypatch, active_file="middle.json"
    )
    circumstances_dir = configs_dir / "circumstances"

    _write_json(json_file, {"_date": "2026-08-12", "C1": "17:34:00"})
    _write_json(configs_dir / "zulu.json")
    _write_json(configs_dir / "middle.json")
    _write_json(circumstances_dir / "alpha.json")
    _write_json(circumstances_dir / "bravo.json")

    (configs_dir / "notes.txt").write_text("not a config", encoding="utf-8")
    (circumstances_dir / "invalid.json").write_text("not json", encoding="utf-8")
    _write_json(configs_dir / "nested" / "hidden.json")
    _write_json(circumstances_dir / "nested" / "also-hidden.json")
    _write_json(tmp_path / "outside.json")
    (configs_dir / "suspicious.json").symlink_to(tmp_path / "outside.json")

    response = client.get("/api/configs/list_eclipse")

    assert response.status_code == 200
    assert response.get_json() == {
        "files": [
            {"name": "alpha.json", "dir": "circumstances", "active": False},
            {"name": "bravo.json", "dir": "circumstances", "active": False},
            {"name": "middle.json", "dir": "configs", "active": True},
            {"name": "todayeclipse.json", "dir": "trigger", "active": False},
            {"name": "zulu.json", "dir": "configs", "active": False},
        ]
    }


def test_list_eclipse_returns_empty_files_for_empty_directories(tmp_path, monkeypatch):
    client, configs_dir, _json_file = _configure_list_route(tmp_path, monkeypatch)
    (configs_dir / "circumstances").mkdir()

    response = client.get("/api/configs/list_eclipse")

    assert response.status_code == 200
    assert response.get_json() == {"files": []}

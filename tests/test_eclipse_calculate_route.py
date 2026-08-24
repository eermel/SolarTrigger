import json
import importlib.util
import sys
from datetime import datetime as real_datetime, timezone
from types import ModuleType

import pytest


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

    class StubClient:
        def __init__(self, routes):
            self.routes = routes

        def post(self, path, json=None):
            request.json = json
            try:
                return Response(self.routes[(path, "POST")]())
            finally:
                request.json = None

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

if "flask_socketio" not in sys.modules and importlib.util.find_spec("flask_socketio") is None:
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

import flask_app.app as flask_module


class ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def _prepare_calculation(monkeypatch, tmp_path, captured_commands):
    json_file = tmp_path / "todayeclipse.json"
    emitted = []

    class FakePopen:
        returncode = 0

        def __init__(self, command, **_kwargs):
            captured_commands.append(command)
            json_file.write_text(json.dumps({"_date": command[command.index("--date") + 1]}))
            self.stdout = iter(())

        def wait(self):
            return self.returncode

        def poll(self):
            return self.returncode

    monkeypatch.setattr(flask_module, "JSON_FILE", json_file)
    monkeypatch.setattr(flask_module, "_calc_proc", None)
    monkeypatch.setattr(flask_module, "_state", {})
    monkeypatch.setattr(flask_module, "_save_state", lambda: None)
    monkeypatch.setattr(flask_module, "_append_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(flask_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(flask_module.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, payload: emitted.append((event, payload)),
    )
    return flask_module.app.test_client(), emitted


def test_eclipse_calculate_invokes_python_calculator_and_updates_state(
    tmp_path, monkeypatch
):
    commands = []
    client, emitted = _prepare_calculation(monkeypatch, tmp_path, commands)

    response = client.post(
        "/api/eclipse/calculate",
        json={"lat": 43.6, "lon": 1.44, "alt": 150, "tz": 2, "eclipse": "2027-08-02"},
    )

    assert response.status_code == 200
    assert response.get_json() == {"status": "started"}
    assert len(commands) == 1
    command = commands[0]
    assert command[1] == str(flask_module.CALC_SCRIPT)
    assert command[1].endswith("eclipse_calculator_py.py")
    assert all("eclipse_calculator_jubier.py" not in str(arg) for arg in command)
    assert command[command.index("--date") + 1] == "2027-08-02"
    assert command[command.index("--output") + 1] == str(flask_module.JSON_FILE)
    assert flask_module._state["calc_running"] is False
    assert flask_module._state["eclipse"] == {"_date": "2027-08-02"}
    assert ("eclipse_calculated", {"status": "success", "data": {"_date": "2027-08-02"}}) in emitted


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        ("2026-01-01", "2026-08-12"),
        ("2030-01-01", "2026-08-12"),
    ],
)
def test_eclipse_calculate_auto_selects_supported_date(
    tmp_path, monkeypatch, today, expected
):
    commands = []
    client, _emitted = _prepare_calculation(monkeypatch, tmp_path, commands)

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            value = real_datetime.strptime(today, "%Y-%m-%d")
            return value.replace(tzinfo=timezone.utc) if tz else value

    monkeypatch.setattr(flask_module, "datetime", FrozenDatetime)
    monkeypatch.setattr(
        flask_module.eclipse_loader,
        "list_supported_eclipses",
        lambda: ["2026-08-12", "2024-04-08"],
    )

    response = client.post(
        "/api/eclipse/calculate",
        json={"lat": 43.6, "lon": 1.44, "eclipse": "auto"},
    )

    assert response.status_code == 200
    command = commands[0]
    assert command[command.index("--date") + 1] == expected

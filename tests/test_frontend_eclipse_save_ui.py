import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType


sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

if "flask" not in sys.modules and importlib.util.find_spec("flask") is None:
    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def get_data(self, as_text=False):
            return self.body if as_text else self.body.encode()

    class Client:
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
            return Client(self.routes)

    flask_stub = ModuleType("flask")
    flask_stub.Flask = Flask
    flask_stub.jsonify = lambda value: value
    flask_stub.request = object()
    flask_stub.send_from_directory = lambda directory, filename: Response(
        (Path(directory) / filename).read_text(encoding="utf-8")
    )
    sys.modules["flask"] = flask_stub

if (
    "flask_socketio" not in sys.modules
    and importlib.util.find_spec("flask_socketio") is None
):
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


def test_save_circumstances_controls_are_unique_and_before_observation_location(
    monkeypatch,
):
    monkeypatch.setattr(
        flask_module,
        "send_from_directory",
        lambda directory, filename: (Path(directory) / filename).read_text(
            encoding="utf-8"
        ),
    )
    response = flask_module.app.test_client().get("/")

    assert response.status_code == 200
    if hasattr(response, "get_data"):
        html = response.get_data(as_text=True)
    else:
        html = response.get_json()

    save_title = '<div class="card-title">Save circumstances</div>'
    observation_title = '<div class="card-title">Observation location</div>'
    filename_label = '<label for="eclipse-save-filename">File name</label>'
    filename_input = 'id="eclipse-save-filename"'
    save_button = (
        '<button class="btn btn-primary" type="button" '
        'onclick="saveEclipseConfig()">💾 Save</button>'
    )

    save_index = html.index(save_title)
    observation_index = html.index(observation_title)
    label_index = html.index(filename_label)
    input_index = html.index(filename_input)
    button_index = html.index(save_button)

    assert save_index < label_index < observation_index
    assert save_index < input_index < observation_index
    assert save_index < button_index < observation_index
    assert html.count(save_title) == 1
    assert html.count(filename_label) == 1
    assert len(re.findall(r'id=["\']eclipse-save-filename["\']', html)) == 1
    assert html.count('onclick="saveEclipseConfig()"') == 1

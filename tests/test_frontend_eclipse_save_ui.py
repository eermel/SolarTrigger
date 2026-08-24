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


def _render_index(monkeypatch):
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
        return response.get_data(as_text=True)
    return response.get_json()


def test_save_circumstances_controls_are_unique_and_before_observation_location(
    monkeypatch,
):
    html = _render_index(monkeypatch)

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


def test_eclipse_save_prefix_uses_backend_date_and_calculation_event(monkeypatch):
    html = _render_index(monkeypatch)

    prefix_function = re.search(
        r"function updateEclipseSaveFilename\(eclipseData\) \{(.*?)\n\}",
        html,
        re.DOTALL,
    )

    assert prefix_function is not None
    prefix_logic = prefix_function.group(1)
    assert "eclipseData._date || eclipseData._date_utc" in prefix_logic
    assert "_Circonstances_" in prefix_logic
    assert "new Date(" not in prefix_logic
    assert "Date()" not in prefix_logic
    assert "input.value.startsWith(_eclipseSavePrefix)" in prefix_logic
    assert "input.value.slice(_eclipseSavePrefix.length)" in prefix_logic

    calculated_handler = re.search(
        r"socket\.on\('eclipse_calculated', d => \{(.*?)\n\}\);",
        html,
        re.DOTALL,
    )
    assert calculated_handler is not None
    assert "d.status === 'success' && d.data" in calculated_handler.group(1)
    assert "updateEclipseSaveFilename(d.data)" in calculated_handler.group(1)


def test_eclipse_save_rejects_empty_default_prefix_suffix_before_fetch(monkeypatch):
    html = _render_index(monkeypatch)

    save_function = re.search(
        r"async function saveEclipseConfig\(\) \{(.*?)\n\}",
        html,
        re.DOTALL,
    )

    assert save_function is not None
    save_logic = save_function.group(1)
    message = "Please complete the file name after the default prefix."
    assert message in save_logic
    assert "File name is required." not in save_logic
    assert r"/^\d{8}_Circonstances_$/" in save_logic
    assert "filename === activePrefix" in save_logic
    assert "filename.startsWith(activePrefix)" in save_logic
    assert "filename.slice(filename.lastIndexOf('_') + 1) === ''" in save_logic
    assert save_logic.index(message) < save_logic.index("fetch('/api/configs/save'")

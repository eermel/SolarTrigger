import importlib.util
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


def test_trigger_ui_is_simplified_and_ordered(monkeypatch):
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

    for removed_button_id in (
        "btn-debug-gen",
        "btn-debug-realistic",
        "btn-usb",
    ):
        assert removed_button_id not in html

    dryrun_index = html.index('id="btn-dryrun"')
    start_index = html.index('id="btn-start"')
    assert dryrun_index < start_index
    assert 'id="btn-stop"' in html
    assert 'id="btn-totality-only"' in html

    for removed_handler in (
        "generateDebug(",
        "generateDebugRealistic(",
        "toggleUsb(",
        "clearDebugFiles(",
    ):
        assert removed_handler not in html

def test_trigger_log_clear_uses_standard_single_panel_button():
    html = (
        Path(__file__).resolve().parents[1]
        / "flask_app"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")

    start = html.index('id="trigger-log-title"')
    end = html.index('id="log-container-trigger"', start)
    trigger_log = html[start:end]

    assert html.count('id="log-container-trigger"') == 1
    assert '<button class="btn btn-secondary"' in trigger_log
    assert 'onclick="clearTriggerRigLog()"' in trigger_log
    assert ">CLEAR</button>" in trigger_log
    assert "cursor:pointer" not in trigger_log



def test_trigger_rig_selector_is_immediately_before_log_section():
    html = (
        Path(__file__).resolve().parents[1]
        / "flask_app"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")

    eclipse_index = html.index('id="trigger-contacts"')
    rig_index = html.index(
        'class="controls-rig-selector" role="group" aria-label="Trigger RIG"',
        eclipse_index,
    )
    log_index = html.index('id="trigger-log-title"', rig_index)

    assert eclipse_index < rig_index < log_index


def test_trigger_active_configuration_has_no_redundant_eclipse_metadata():
    html = (
        Path(__file__).resolve().parents[1]
        / "flask_app"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")

    active_start = html.index("Active configuration")
    active_end = html.index("Eclipse circumstances", active_start)
    active = html[active_start:active_end]

    assert "trig-eclipse-label" not in active
    assert "trig-eclipse-type2" not in active
    assert "trig-eclipse-type-gps" not in active


def test_trigger_eclipse_circumstances_shows_type_and_gps_type_same_header():
    html = (
        Path(__file__).resolve().parents[1]
        / "flask_app"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")

    start = html.index("Eclipse circumstances", html.index('id="page-4"'))
    end = html.index('id="trigger-contacts"', start)
    header = html[start:end]

    assert 'id="trig-eclipse-type2"' in header
    assert 'id="trig-eclipse-type-gps"' in header
    assert "GPS position type:" in header
    assert 'id="trigger-eclipse-type"' not in header


def test_trigger_partiality_log_uses_eclipse_moon_icon():
    js = (
        Path(__file__).resolve().parents[1]
        / "flask_app"
        / "static"
        / "js"
        / "solartrigger.js"
    ).read_text(encoding="utf-8")

    start = js.index("function triggerLogIcon(level)")
    end = js.index("\n}", start) + 2
    icon_function = js[start:end]

    assert "orange: '🌙'" in icon_function
    assert "orange: '🌒'" not in icon_function



def test_main_tabs_show_non_blocking_numbered_workflow():
    html = (
        Path(__file__).resolve().parents[1]
        / "flask_app"
        / "templates"
        / "index.html"
    ).read_text(encoding="utf-8")

    expected = (
        ("1", "DEVICES"),
        ("2", "SYNC GPS"),
        ("3", "ECLIPSE"),
        ("4", "PHOTO SETUP"),
        ("5", "EXPO. OPT."),
        ("6", "CAMERA"),
        ("7", "CONTROLS"),
        ("8", "TRIGGER"),
    )

    positions = []

    for number, label in expected:
        marker = (
            f'<i class="workflow-step-number" '
            f'data-step="{number}" aria-hidden="true"></i>'
        )
        assert marker in html
        assert f"<span>{label}</span>" in html
        positions.append(html.index(marker))

    assert positions == sorted(positions)

    # Auxiliary/non-workflow tabs stay unnumbered.
    add_camera_start = html.index('id="add-camera-tab"')
    add_camera_end = html.index("</button>", add_camera_start)
    assert "workflow-step-number" not in html[add_camera_start:add_camera_end]

    sequencer_start = html.index('id="sequencer-tab"')
    sequencer_end = html.index("</button>", sequencer_start)
    assert "workflow-step-number" not in html[sequencer_start:sequencer_end]


def test_workflow_tabs_have_visual_arrows_only():
    css = (
        Path(__file__).resolve().parents[1]
        / "flask_app"
        / "static"
        / "css"
        / "solartrigger.css"
    ).read_text(encoding="utf-8")

    assert "#tabs .workflow-step-number" in css
    assert "content: attr(data-step);" in css
    assert (
        "#tabs .tab[data-workflow-step]:not([data-workflow-last])::before"
        in css
    )
    assert "content: '→';" in css

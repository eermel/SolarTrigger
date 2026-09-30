from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

from backend.state_store import StateStore
from backend.runtime_daemon import RuntimeController
import backend.runtime_daemon as runtime_daemon


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")

import flask_app.app as flask_module


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "flask_app" / "templates" / "index.html"
JS = ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
RUNTIME = ROOT / "backend" / "runtime_daemon.py"


def test_ui_config_defaults_and_persistence(tmp_path):
    state_file = tmp_path / "state.json"
    store = StateStore(state_file)

    assert store.snapshot("ui_config") == {
        "debug_tab_visible": True,
        "logs_visible": True,
    }

    store.update_section(
        "ui_config",
        {
            "debug_tab_visible": False,
            "logs_visible": False,
        },
        persist=True,
    )

    restored = StateStore(state_file)
    assert restored.snapshot("ui_config") == {
        "debug_tab_visible": False,
        "logs_visible": False,
    }


def test_ui_config_api_roundtrip(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(flask_module, "_state_store", store)

    client = flask_module.app.test_client()

    initial = client.get("/api/ui-config")
    assert initial.status_code == 200
    assert initial.get_json() == {
        "debug_tab_visible": True,
        "logs_visible": True,
    }

    changed = client.post(
        "/api/ui-config",
        json={
            "debug_tab_visible": False,
            "logs_visible": False,
        },
    )
    assert changed.status_code == 200
    assert changed.get_json() == {
        "debug_tab_visible": False,
        "logs_visible": False,
    }

    restored = StateStore(tmp_path / "state.json")
    assert restored.snapshot("ui_config") == changed.get_json()


@pytest.mark.parametrize(
    "payload",
    [
        {"debug_tab_visible": "yes"},
        {"logs_visible": 1},
        {"unknown": True},
    ],
)
def test_ui_config_api_rejects_invalid_values(tmp_path, monkeypatch, payload):
    store = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(flask_module, "_state_store", store)

    response = flask_module.app.test_client().post(
        "/api/ui-config",
        json=payload,
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "UI_CONFIG_INVALID"


def test_system_ui_configuration_section_precedes_log_section():
    source = INDEX.read_text(encoding="utf-8")

    ui_pos = source.index(
        'data-system-section="ui-config"'
    )
    log_pos = source.index(
        'data-system-section="log"'
    )

    assert ui_pos < log_pos
    assert 'id="ui-debug-tab-visible-switch"' in source
    assert 'id="ui-logs-visible-switch"' in source
    assert 'role="switch"' in source


def test_ui_configuration_frontend_controls_debug_and_all_log_cards():
    source = JS.read_text(encoding="utf-8")

    assert "function applyUiConfiguration(value)" in source
    assert "document.getElementById('debug-tab')" in source
    assert "document.querySelectorAll('[id^=\"log-container-\"]')" in source
    assert "document.querySelector('.system-log-group')" in source
    assert "'ui-config-hidden'" in source
    assert "fetch('/api/ui-config'" in source


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def _ready_controller(active=False):
    controller = object.__new__(RuntimeController)
    controller.project_root = Path("/tmp/solartrigger-ready-test")
    controller._failure_audio_lock = threading.Lock()
    controller.trigger = SimpleNamespace(
        any_active_or_starting=lambda: active
    )
    controller._runtime_log = lambda *_args, **_kwargs: None
    return controller


def test_runtime_ready_tone_uses_one_contact_beep(monkeypatch):
    controller = _ready_controller(active=False)
    calls = []

    monkeypatch.setattr(
        runtime_daemon.threading,
        "Thread",
        _ImmediateThread,
    )
    monkeypatch.setattr(
        runtime_daemon.audio_service,
        "init",
        lambda *args, **kwargs: calls.append(("init", kwargs.get("driver"))),
    )
    monkeypatch.setattr(
        runtime_daemon.audio_service,
        "set_sounds_dir",
        lambda path: calls.append(("dir", Path(path).name)),
    )
    monkeypatch.setattr(
        runtime_daemon.audio_service,
        "play",
        lambda filename: calls.append(("play", filename)),
    )

    assert controller.announce_ready() is True
    assert calls == [
        ("init", "alsa"),
        ("dir", "Sounds"),
        ("play", "contact.wav"),
    ]


def test_runtime_ready_tone_is_skipped_during_active_trigger(monkeypatch):
    controller = _ready_controller(active=True)

    monkeypatch.setattr(
        runtime_daemon.audio_service,
        "play",
        lambda _filename: pytest.fail(
            "ready tone must not play during an active trigger"
        ),
    )

    assert controller.announce_ready() is False


def test_runtime_ready_tone_is_scheduled_after_ready_log():
    source = RUNTIME.read_text(encoding="utf-8")

    ready = source.index("Standalone runtime ready pid=%s socket=%s")
    tone = source.index("controller.announce_ready()", ready)
    serve = source.index("server.serve_forever", tone)

    assert ready < tone < serve

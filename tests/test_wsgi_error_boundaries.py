from pathlib import Path
import threading
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")
INSTALLER = (ROOT / "install" / "install_solareclipse.sh").read_text(encoding="utf-8")

def test_background_threads_are_idempotent():
    assert "_background_threads_lock = threading.Lock()" in APP
    assert "_background_threads_started = False" in APP
    assert "if _background_threads_started:" in APP
    assert "_background_threads_started = True" in APP

def test_wsgi_starts_background_threads():
    assert "from app import app, socketio, start_background_threads" in INSTALLER
    assert "start_background_threads()" in INSTALLER

def test_trigger_error_boundaries_exist():
    assert '"TRIGGER_SIMULATION_FAILED"' in APP
    assert '"TRIGGER_START_FAILED"' in APP
    assert '"TRIGGER_DRYRUN_FAILED"' not in APP
    assert '"DEBUG_START_FAILED"' in APP
    assert '"error": "Trigger simulation failed."' in APP
    assert '"error": "Trigger dry-run failed."' in APP
    assert '"error": "DEBUG start failed."' in APP


def test_partial_background_thread_startup_retries_only_missing_roles():
    start = APP.index("def start_background_threads():")
    end = APP.index("\n# Init au démarrage", start)
    function_source = APP[start:end]

    started = []
    attempts = []

    class FakeThread:
        fail_role = "camera-poll"

        def __init__(self, *, target, daemon, name):
            self.target = target
            self.daemon = daemon
            self.name = name

        def start(self):
            attempts.append(self.name)
            if self.name == type(self).fail_role:
                raise RuntimeError("synthetic thread start failure")
            started.append(self.name)

    namespace = {
        "threading": SimpleNamespace(Thread=FakeThread),
        "_background_threads_lock": threading.Lock(),
        "_background_threads_started": False,
        "_background_thread_roles_started": set(),
        "_warm_configured_mounts_at_startup": lambda: None,
        "_thread_status_broadcast": lambda: None,
        "_thread_camera_poll": lambda: None,
        "_thread_runtime_relay": lambda: None,
        "_trim_log_file": lambda: None,
        "runtime_client_enabled": lambda: True,
        "log": SimpleNamespace(info=lambda *_args, **_kwargs: None),
    }
    exec(function_source, namespace)

    try:
        namespace["start_background_threads"]()
    except RuntimeError as exc:
        assert "synthetic thread start failure" in str(exc)
    else:
        raise AssertionError("partial startup failure must propagate")

    assert namespace["_background_threads_started"] is False
    assert namespace["_background_thread_roles_started"] == {
        "mount-startup-warmup",
        "status-broadcast",
    }
    assert attempts == [
        "mount-startup-warmup",
        "status-broadcast",
        "camera-poll",
    ]

    FakeThread.fail_role = None
    assert namespace["start_background_threads"]() is True

    assert namespace["_background_threads_started"] is True
    assert namespace["_background_thread_roles_started"] == {
        "mount-startup-warmup",
        "status-broadcast",
        "camera-poll",
        "runtime-relay",
        "log-trimmer",
    }
    assert attempts == [
        "mount-startup-warmup",
        "status-broadcast",
        "camera-poll",
        "camera-poll",
        "runtime-relay",
        "log-trimmer",
    ]
    assert started.count("mount-startup-warmup") == 1
    assert started.count("status-broadcast") == 1

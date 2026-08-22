import json
import threading
import time
from datetime import datetime, timezone

from backend.state_store import StateStore
from backend.trigger_service import TriggerService


def test_trigger_gps_loss_after_start_does_not_interrupt(tmp_path, monkeypatch):
    store = StateStore(tmp_path / "state.json")
    store.update_section(
        "gps",
        {
            "connected": True,
            "synced": True,
            "sync_time": datetime.now(timezone.utc).isoformat(),
        },
    )
    store.update_section(
        "circumstances", {"loaded": True, "active_file": "todayeclipse.json"}
    )
    store.update_section(
        "capture", {"loaded": True, "active_file": "camera.json"}
    )
    store.set("camera_config_file", "camera.json")

    eclipse = tmp_path / "todayeclipse.json"
    eclipse.write_text(
        json.dumps(
            {
                "_generated_utc": datetime.now(timezone.utc).isoformat(),
                "TSTART": "10:00:00",
                "C1": "10:10:00",
                "C2": "10:20:00",
                "TMAX": "10:20:30",
                "C3": "10:21:00",
                "C4": "10:30:00",
                "TEND": "10:40:00",
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "eclipse_trigger.py"
    script.write_text("", encoding="utf-8")
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "camera.json").write_text("{}", encoding="utf-8")

    allow_process_exit = threading.Event()
    process_completed = threading.Event()

    class FakeStdout:
        def __init__(self):
            self.lines = iter(("PHASE 1a\n", "PHASE 1b\n", "PHASE 2\n"))

        def readline(self):
            try:
                line = next(self.lines)
            except StopIteration:
                allow_process_exit.wait(timeout=2)
                return ""
            time.sleep(0.01)
            return line

    class FakeProc:
        def __init__(self):
            self.stdout = FakeStdout()
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            allow_process_exit.wait(timeout=timeout)
            self.returncode = 0
            process_completed.set()
            return self.returncode

    monkeypatch.setattr(
        "backend.trigger_service.subprocess.Popen", lambda *args, **kwargs: FakeProc()
    )

    real_validate_start = TriggerService.validate_start
    validate_calls = []

    def counting_validate_start(self, require_gps=True):
        validate_calls.append(require_gps)
        return real_validate_start(self, require_gps=require_gps)

    monkeypatch.setattr(TriggerService, "validate_start", counting_validate_start)

    logs = []
    svc = TriggerService(
        store,
        script,
        eclipse,
        tmp_path / "events.log",
        configs,
        lambda *args: logs.append(args),
        lambda *args: None,
    )

    assert svc.start(simulate=False, dry_run=False) is True
    assert store.snapshot("trigger")["running"] is True

    store.update_section("gps", {"connected": False, "synced": False})
    time.sleep(0.05)

    assert store.snapshot("trigger")["running"] is True
    assert process_completed.is_set() is False
    assert validate_calls == [True]

    allow_process_exit.set()
    deadline = time.monotonic() + 2
    while store.snapshot("trigger")["running"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert process_completed.is_set() is True
    assert store.snapshot("trigger")["running"] is False
    assert validate_calls == [True]
    assert not any("GPS" in str(entry) for entry in logs)

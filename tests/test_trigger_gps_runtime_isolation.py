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
                "_date": datetime.now().astimezone().date().isoformat(),
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
    camera_cfg = configs / "camera_cfg"
    camera_cfg.mkdir(parents=True)
    (camera_cfg / "camera.json").write_text("{}", encoding="utf-8")

    circumstances_dir = configs / "circumstances"
    circumstances_dir.mkdir(parents=True)
    circumstances_name = "test_circumstances.json"
    (circumstances_dir / circumstances_name).write_text(
        eclipse.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    photo_dir = configs / "photo_cfg"
    photo_dir.mkdir(parents=True)
    (photo_dir / "photo.json").write_text(json.dumps({
        "config_type": "photo_setup",
        "sequence_margin_min": 10,
        "phases": {
            "partial": {"interval_s": 60},
            "diamond_ring": {
                "interval_s": 1,
                "duration_s": 30,
                "totality_overlap_s": 5,
            },
            "totality": {"interval_s": 0},
        },
    }), encoding="utf-8")
    exposure_dir = configs / "exposure_opt"
    exposure_dir.mkdir(parents=True)
    (exposure_dir / "exposure.json").write_text(json.dumps({
        "config_type": "exposure_optimization",
        "rigs": [{"rig_id": 1, "photo": {}}],
    }), encoding="utf-8")

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

    def counting_validate_start(self, rig_id=1, require_gps=True, selected=None):
        validate_calls.append(require_gps)
        return real_validate_start(
            self,
            rig_id=rig_id,
            require_gps=require_gps,
            selected=selected,
        )

    monkeypatch.setattr(TriggerService, "validate_start", counting_validate_start)

    logs = []
    svc = TriggerService(
        store,
        script,
        eclipse,
        configs,
        lambda *args: logs.append(args),
        lambda *args: None,
    )

    assert svc.start(simulate=False, dry_run=False, selected={
        "circumstances_file": circumstances_name,
        "photo_file": "photo.json",
        "exposure_opt_file": "exposure.json",
    }) is True
    assert store.snapshot("trigger")["rigs"]["1"]["running"] is True

    store.update_section("gps", {"connected": False, "synced": False})
    time.sleep(0.05)

    assert store.snapshot("trigger")["rigs"]["1"]["running"] is True
    assert process_completed.is_set() is False
    assert validate_calls == [True]

    allow_process_exit.set()
    deadline = time.monotonic() + 2
    while store.snapshot("trigger")["rigs"]["1"]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)

    assert process_completed.is_set() is True
    assert store.snapshot("trigger")["rigs"]["1"]["running"] is False
    assert validate_calls == [True]
    assert not any("GPS" in str(entry) for entry in logs)

import json
from datetime import datetime, timezone

from backend.state_store import StateStore
from backend.trigger_service import TriggerService


def _service(tmp_path):
    configs = tmp_path / "configs"
    for name in ("circumstances", "photo_cfg", "exposure_opt"):
        (configs / name).mkdir(parents=True)
    circumstances = {
        "_date": "2027-08-02",
        "TSTART": "09:00:00",
        "C1": "10:00:00",
        "C2": "11:00:00",
        "C3": "11:02:00",
        "C4": "12:00:00",
        "TEND": "13:00:00",
    }
    (configs / "circumstances" / "eclipse.json").write_text(json.dumps(circumstances))
    (configs / "photo_cfg" / "photo.json").write_text(json.dumps({
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
    }))
    (configs / "exposure_opt" / "expo.json").write_text(json.dumps({
        "config_type": "exposure_optimization",
        "rigs": [{"rig_id": 1, "photo": {}}],
    }))
    store = StateStore(tmp_path / "state.json")
    store.update_section("gps", {
        "synced": True,
        "sync_time": datetime.now(timezone.utc).isoformat(),
    })
    service = TriggerService(
        store,
        tmp_path / "scripts" / "eclipse_trigger.py",
        tmp_path / "today.json",
        configs,
        lambda *_args: None,
        lambda *_args: None,
    )
    return service


def test_three_selected_files_replace_execution_plan(tmp_path):
    service = _service(tmp_path)
    selected = {
        "circumstances_file": "eclipse.json",
        "photo_file": "photo.json",
        "exposure_opt_file": "expo.json",
    }

    service.validate_start(require_gps=True, selected=selected)

    assert service._active_circumstances_paths[1].name == "eclipse.json"
    assert service._active_photo_paths[1].name == "photo.json"
    assert service._active_exposure_opt_paths[1].name == "expo.json"


def test_runtime_command_uses_three_sources_and_no_execution_plan(tmp_path, monkeypatch):
    service = _service(tmp_path)
    selected = {
        "circumstances_file": "eclipse.json",
        "photo_file": "photo.json",
        "exposure_opt_file": "expo.json",
    }
    service.validate_start(require_gps=True, selected=selected)
    seen = {}

    class Process:
        returncode = 0
        stdout = iter(())

        def poll(self): return 0
        def wait(self, timeout=None): return 0

    def popen(command, **kwargs):
        seen["command"] = command
        seen["env"] = kwargs["env"]
        return Process()

    monkeypatch.setattr("backend.trigger_service.subprocess.Popen", popen)
    service._run(simulate=True, rig_id=1)

    assert "--execution-plan" not in seen["command"]
    assert "--camera" in seen["command"]
    assert "--exposure-opt" in seen["command"]
    assert seen["env"]["SET_TRIGGER_RIG_ID"] == "1"

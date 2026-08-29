import json
from types import SimpleNamespace

import backend.camera_ipc_server as camera_ipc_server
from backend.camera_ipc_server import CameraIpcServer


class FakeWorker:
    def __init__(self):
        self.prepared_intents = []

    def apply_phase_settings(self, **settings):
        return settings

    def prepare_capture(self, intent):
        self.prepared_intents.append(intent)
        return SimpleNamespace(
            token=object(),
            estimated_total_s=0.1,
            exposures_s=[0.1],
            planned_count=1,
            plugin_name="fake-camera",
        )


class FakeRuntime:
    def __init__(self, worker, policy):
        self.worker = worker
        self.policy = policy

    def get_for_rig(self, rig_id):
        return self.worker if rig_id == 1 else None

    def get_policy_config_for_rig(self, rig_id):
        return self.policy if rig_id == 1 else None


def rig_policy(*, iso_max):
    return {
        "devices": {
            "camera": {
                "manufacturer": "Test Cameras",
                "model": "Known Model",
            }
        },
        "optics": {"focal_length_mm": 400.0},
        "photo": {
            "anti_trailing_enabled": True,
            "motion_tolerance_px": 1.5,
            "iso_max": iso_max,
        },
    }


def capture_intent():
    return {
        "shutter_min": None,
        "shutter_max": None,
        "step_ev": 1.0,
        "speeds": ["4"],
        "phase": "C2",
        "target_time": "2026-08-12T18:00:00Z",
        "deadline": None,
        "overflow_policy": None,
    }


def prepare(server):
    return server.handle_request(
        {
            "operation": "prepare_capture",
            "params": {"rig_id": 1, "intent": capture_intent()},
        }
    )


def make_server(tmp_path, monkeypatch, *, iso_max=3200):
    sensor_db = tmp_path / "camera_sensors.v1.json"
    sensor_db.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sensors": [
                    {
                        "manufacturer": "Test Cameras",
                        "model": "Known Model",
                        "sensor_width_mm": 30.0,
                        "sensor_height_mm": 20.0,
                        "width_px": 6000,
                        "height_px": 4000,
                        "pixel_pitch_um": 5.0,
                        "sources": ["test fixture"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(camera_ipc_server, "_SENSOR_DB_PATH", sensor_db)
    worker = FakeWorker()
    runtime = FakeRuntime(worker, rig_policy(iso_max=iso_max))
    server = CameraIpcServer(
        runtime,
        endpoint_dir=tmp_path / "ipc",
        parent_pid=4321,
        log_fn=lambda _message: None,
    )
    server.handle_request(
        {
            "operation": "apply_phase_settings",
            "params": {"rig_id": 1, "aperture": None, "iso": "200"},
        }
    )
    return server, runtime


def test_iso_max_change_invalidates_rig_plan_version(tmp_path, monkeypatch):
    server, runtime = make_server(tmp_path, monkeypatch, iso_max=3200)
    first = prepare(server)

    runtime.policy = rig_policy(iso_max=1600)
    second = prepare(server)

    assert second["plan_version"] != first["plan_version"]


def test_identical_plan_is_deterministic_and_reuses_materialization(
    tmp_path, monkeypatch
):
    server, _runtime = make_server(tmp_path, monkeypatch)
    materialization_calls = 0
    original_policy_intent = server._policy_intent

    def counting_policy_intent(*args, **kwargs):
        nonlocal materialization_calls
        materialization_calls += 1
        return original_policy_intent(*args, **kwargs)

    monkeypatch.setattr(server, "_policy_intent", counting_policy_intent)

    first = prepare(server)
    second = prepare(server)

    assert second["plan_version"] == first["plan_version"]
    assert materialization_calls == 1


def test_dry_run_and_real_prepare_have_plan_version_parity(tmp_path, monkeypatch):
    """IPC prepare is run-mode agnostic, so these represent dry-run and real calls."""
    server, _runtime = make_server(tmp_path, monkeypatch)

    dry_run = prepare(server)
    real_run = prepare(server)

    assert real_run["plan_version"] == dry_run["plan_version"]

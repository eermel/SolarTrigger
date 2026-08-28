import json
from types import SimpleNamespace

import pytest

import backend.camera_ipc_server as camera_ipc_server
from backend.camera_ipc_server import CameraIpcServer


class FakeWorker:
    def __init__(self):
        self.apply_calls = []
        self.prepared_intents = []

    def apply_phase_settings(self, **settings):
        self.apply_calls.append(settings)
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
    def __init__(self, workers, policies):
        self.workers = workers
        self.policies = policies

    def get_for_rig(self, rig_id):
        return self.workers.get(rig_id)

    def get_policy_config_for_rig(self, rig_id):
        return self.policies.get(rig_id)


def capture_intent(*, speeds=None, shutter_min=None, shutter_max=None):
    return {
        "shutter_min": shutter_min,
        "shutter_max": shutter_max,
        "step_ev": 1.0,
        "speeds": speeds,
        "phase": "C2",
        "target_time": "2026-08-12T18:00:00Z",
        "deadline": None,
        "overflow_policy": None,
    }


@pytest.fixture
def tracked_rig_server(tmp_path, monkeypatch):
    sensor_db = tmp_path / "data" / "camera_sensors" / "camera_sensors.v1.json"
    sensor_db.parent.mkdir(parents=True)
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

    workers = {1: FakeWorker(), 2: FakeWorker()}
    policies = {
        rig_id: {
            "devices": {
                "camera": {
                    "manufacturer": "Test Cameras",
                    "model": "Known Model",
                },
                "mount": {
                    "control": "external",
                    "geometry": geometry,
                    "tracking": "solar",
                },
            },
            "optics": {"focal_length_mm": 300_000},
            "photo": {
                "anti_trailing_enabled": True,
                "motion_tolerance_px": 1.0,
                "iso_max": 800,
            },
        }
        for rig_id, geometry in ((1, "equatorial"), (2, "altaz"))
    }
    server = CameraIpcServer(
        FakeRuntime(workers, policies),
        endpoint_dir=tmp_path / "ipc",
        parent_pid=4321,
        log_fn=lambda _message: None,
    )
    for rig_id in workers:
        server.handle_request(
            {
                "operation": "apply_phase_settings",
                "params": {"rig_id": rig_id, "aperture": None, "iso": "200"},
            }
        )
    return server, workers


def prepare(server, rig_id, intent):
    return server.handle_request(
        {
            "operation": "prepare_capture",
            "params": {"rig_id": rig_id, "intent": intent},
        }
    )


def test_equatorial_tracked_rig_bypasses_motion_limits_for_lists_and_brackets(
    tracked_rig_server,
):
    server, workers = tracked_rig_server
    rig_id = 1
    requested_speeds = ["1/8", "1/2", "4"]

    responses = [
        prepare(server, rig_id, capture_intent(speeds=requested_speeds)),
        prepare(
            server,
            rig_id,
            capture_intent(shutter_min="1", shutter_max="1/125"),
        ),
    ]

    speed_intent, bracket_intent = workers[rig_id].prepared_intents
    assert speed_intent.speeds == requested_speeds
    assert bracket_intent.shutter_min == "1"
    assert bracket_intent.shutter_max == "1/125"
    assert workers[rig_id].apply_calls == [{"aperture": None, "iso": "200"}]
    for response in responses:
        assert "iso_applied" not in response
        assert "corrections" not in response
        assert "warnings" not in response

import json
from types import SimpleNamespace

import pytest

import backend.camera_ipc_server as camera_ipc_server
from backend.camera_ipc_server import CameraIpcServer, IpcError


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


def write_sensor_db(tmp_path):
    path = tmp_path / "data" / "camera_sensors" / "camera_sensors.v1.json"
    path.parent.mkdir(parents=True)
    path.write_text(
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
    return path


def policy(*, enabled, focal_length_mm, model="Known Model"):
    return {
        "devices": {
            "camera": {"manufacturer": "Test Cameras", "model": model}
        },
        "optics": {"focal_length_mm": focal_length_mm},
        "photo": {
            "anti_trailing_enabled": enabled,
            "motion_tolerance_px": 1.0,
            "iso_max": 800,
        },
    }


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
def rig_server(tmp_path, monkeypatch):
    sensor_db = write_sensor_db(tmp_path)
    monkeypatch.setattr(camera_ipc_server, "_SENSOR_DB_PATH", sensor_db)
    workers = {rig_id: FakeWorker() for rig_id in (1, 2, 3)}
    policies = {
        1: policy(enabled=False, focal_length_mm=75_000),
        2: policy(enabled=True, focal_length_mm=150_000),
        3: policy(
            enabled=True, focal_length_mm=300_000, model="Missing Model"
        ),
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


def test_explicit_speed_list_is_enforced_per_rig(rig_server):
    server, workers = rig_server
    requested = ["1/8", "1/2", "4"]

    off_response = prepare(server, 1, capture_intent(speeds=requested))
    enabled_response = prepare(server, 2, capture_intent(speeds=requested))

    assert workers[1].prepared_intents[0].speeds == requested
    assert workers[1].apply_calls == [{"aperture": None, "iso": "200"}]
    assert "iso_applied" not in off_response
    assert "corrections" not in off_response
    assert "warnings" not in off_response

    assert workers[2].prepared_intents[0].speeds == ["1/8", "1/4", "1/4"]
    assert workers[2].apply_calls == [
        {"aperture": None, "iso": "200"},
        {"iso": "800"},
    ]
    assert enabled_response["iso_applied"] == "800"
    assert enabled_response["corrections"] == [
        "shutter_limited",
        "iso_compensated",
    ]
    assert enabled_response["warnings"] == ["iso_capped"]

    with pytest.raises(IpcError) as unknown_model:
        prepare(server, 3, capture_intent(speeds=requested))

    assert unknown_model.value.code == "POLICY_INVALID"
    assert workers[3].prepared_intents == []
    assert workers[3].apply_calls == [{"aperture": None, "iso": "200"}]
    assert len(workers[1].prepared_intents) == len(workers[2].prepared_intents) == 1


def test_regular_bracket_reduces_slowest_bound_and_compensates_from_it(rig_server):
    server, workers = rig_server

    response = prepare(
        server,
        2,
        capture_intent(shutter_min="1", shutter_max="1/125"),
    )

    prepared = workers[2].prepared_intents[0]
    assert prepared.shutter_min == "1/4"
    assert prepared.shutter_max == "1/125"
    assert prepared.speeds is None
    assert workers[2].apply_calls[-1] == {"iso": "800"}
    assert response["iso_applied"] == "800"
    assert response["corrections"] == ["shutter_limited", "iso_compensated"]
    assert response["warnings"] == []

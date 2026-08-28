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
    def __init__(self, worker, policy):
        self.worker = worker
        self.policy = policy

    def get_for_rig(self, rig_id):
        return self.worker if rig_id == 1 else None

    def get_policy_config_for_rig(self, rig_id):
        return self.policy if rig_id == 1 else None


def field_rotation_policy(*, radius=1.0):
    photo = {
        "anti_trailing_enabled": True,
        "motion_tolerance_px": 1.0,
        "iso_max": 800,
    }
    if radius is not None:
        photo["field_rotation_radius_deg"] = radius
    return {
        "devices": {
            "camera": {
                "manufacturer": "Test Cameras",
                "model": "Known Model",
            },
            "mount": {
                "control": "external",
                "geometry": "altaz",
                "tracking": "solar",
            },
        },
        "eclipse": {"reference_site": {"lat": 44.0, "lon": 2.0}},
        "optics": {"focal_length_mm": 1000.0},
        "photo": photo,
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


@pytest.fixture
def make_server(tmp_path, monkeypatch):
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
    monkeypatch.setattr(
        camera_ipc_server, "field_rotation_rate_deg_s", lambda *_args: 0.01
    )

    def factory(policy):
        worker = FakeWorker()
        server = CameraIpcServer(
            FakeRuntime(worker, policy),
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
        return server, worker

    return factory


def prepare(server):
    return server.handle_request(
        {
            "operation": "prepare_capture",
            "params": {"rig_id": 1, "intent": capture_intent()},
        }
    )


def test_positive_radius_applies_field_rotation_shutter_and_iso(make_server):
    server, worker = make_server(field_rotation_policy(radius=1.0))

    response = prepare(server)

    assert response["iso_applied"] == "800"
    assert "shutter_limited" in response["corrections"]
    assert "iso_compensated" in response["corrections"]
    assert "warnings" in response
    assert worker.prepared_intents[0].speeds != ["4"]
    assert worker.apply_calls[-1] == {"iso": "800"}


def test_zero_radius_leaves_capture_and_iso_unchanged(make_server):
    server, worker = make_server(field_rotation_policy(radius=0.0))

    response = prepare(server)

    assert worker.prepared_intents[0].speeds == ["4"]
    assert worker.apply_calls == [{"aperture": None, "iso": "200"}]
    assert "iso_applied" not in response
    assert "corrections" not in response
    assert "warnings" not in response


def test_missing_radius_is_policy_invalid_before_worker_prepare(make_server):
    server, worker = make_server(field_rotation_policy(radius=None))

    with pytest.raises(IpcError) as error:
        prepare(server)

    assert error.value.code == "POLICY_INVALID"
    assert "field_rotation_radius_deg" in error.value.message
    assert worker.prepared_intents == []
    assert worker.apply_calls == [{"aperture": None, "iso": "200"}]

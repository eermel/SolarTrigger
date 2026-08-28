import json

import backend.camera_ipc_server as camera_ipc_server
from backend.camera_ipc_server import CameraIpcServer


class StubWorker:
    def __init__(self):
        self.calls = []

    def connect(self):
        self.calls.append("connect")
        return self


class CapabilityWorker(StubWorker):
    def __init__(self, capabilities=None):
        super().__init__()
        self.capabilities = {} if capabilities is None else capabilities

    def get_vibration_capabilities(self):
        self.calls.append("get_vibration_capabilities")
        return self.capabilities


class StubRuntime:
    def __init__(self, worker, camera):
        self.worker = worker
        self.policy = {
            "devices": {"camera": camera},
            "optics": {"focal_length_mm": 400},
            "photo": {"iso_max": 800},
        }

    def get_for_rig(self, rig_id):
        return self.worker if rig_id == 1 else None

    def get_policy_config_for_rig(self, rig_id):
        return self.policy if rig_id == 1 else None


def write_sensor_db(tmp_path):
    sensor_db = tmp_path / "camera_sensors.v1.json"
    sensor_db.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "sensors": [
                    {
                        "manufacturer": "Test Cameras",
                        "model": "Mirrorless One",
                        "sensor_width_mm": 36.0,
                        "sensor_height_mm": 24.0,
                        "width_px": 6000,
                        "height_px": 4000,
                        "camera_type": "mirrorless",
                        "sources": ["test fixture"],
                    },
                    {
                        "manufacturer": "Test Cameras",
                        "model": "DSLR One",
                        "sensor_width_mm": 36.0,
                        "sensor_height_mm": 24.0,
                        "width_px": 6000,
                        "height_px": 4000,
                        "camera_type": "dslr",
                        "sources": ["test fixture"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return sensor_db


def capability_result(tmp_path, monkeypatch, worker, model):
    monkeypatch.setattr(camera_ipc_server, "_SENSOR_DB_PATH", write_sensor_db(tmp_path))
    runtime = StubRuntime(
        worker,
        {"manufacturer": "Test Cameras", "model": model},
    )
    server = CameraIpcServer(
        runtime,
        endpoint_dir=tmp_path / "ipc",
        parent_pid=4321,
        log_fn=lambda _message: None,
    )
    return server.handle_request(
        {"operation": "camera.capabilities", "params": {"rig_id": 1}}
    )


def test_mirrorless_reports_type_and_default_plugin_capabilities(tmp_path, monkeypatch):
    worker = CapabilityWorker()

    result = capability_result(tmp_path, monkeypatch, worker, "Mirrorless One")

    assert result["camera_type"] == "mirrorless"
    assert result["vibration_caps"] == {}
    assert worker.calls == ["connect", "get_vibration_capabilities"]


def test_dslr_without_capability_method_remains_control_neutral(tmp_path, monkeypatch):
    worker = StubWorker()

    result = capability_result(tmp_path, monkeypatch, worker, "DSLR One")

    assert result["camera_type"] == "dslr"
    assert result["vibration_caps"] in ({}, None)
    assert worker.calls == ["connect"]


def test_plugin_capabilities_are_passed_through_unmodified(tmp_path, monkeypatch):
    capabilities = {"efcs": True}
    worker = CapabilityWorker(capabilities)

    result = capability_result(tmp_path, monkeypatch, worker, "DSLR One")

    assert result["vibration_caps"] is capabilities
    assert result["vibration_caps"] == {"efcs": True}
    assert worker.calls == ["connect", "get_vibration_capabilities"]

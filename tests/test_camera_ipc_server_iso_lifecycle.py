from pathlib import Path
from types import SimpleNamespace

import pytest

import backend.camera_ipc_server as camera_ipc_server
from backend.camera_ipc_server import CameraIpcServer, IpcError


class FakeWorker:
    def __init__(self) -> None:
        self.fail_apply = False
        self.apply_calls = []

    def apply_phase_settings(self, **settings):
        self.apply_calls.append(settings)
        if self.fail_apply:
            raise RuntimeError("forced apply failure")
        return settings

    def prepare_capture(self, intent):
        return SimpleNamespace(
            token=object(),
            estimated_total_s=0.1,
            exposures_s=[0.1],
            planned_count=1,
            plugin_name="fake-camera",
        )


class FakeRuntime:
    def __init__(self, workers, *, policy=None) -> None:
        self.workers = workers
        self.policy = policy

    def get_for_rig(self, rig_id):
        return self.workers.get(rig_id)

    def get_policy_config_for_rig(self, rig_id):
        return self.policy


def make_server(tmp_path, workers, *, policy=None):
    return CameraIpcServer(
        FakeRuntime(workers, policy=policy),
        endpoint_dir=tmp_path / "ipc",
        parent_pid=4321,
        log_fn=lambda _message: None,
    )


def request(server, operation, params, session_id=None):
    envelope = {"operation": operation, "params": params}
    if session_id is not None:
        envelope["session_id"] = session_id
    return server.handle_request(envelope)


def apply_iso(server, rig_id, iso, session_id=None):
    return request(
        server,
        "apply_phase_settings",
        {"rig_id": rig_id, "aperture": None, "iso": iso},
        session_id,
    )


def capture_intent():
    return {
        "shutter_min": "1/125",
        "shutter_max": "1/125",
        "step_ev": 1.0,
        "speeds": None,
        "phase": "C2",
        "target_time": "2026-08-12T18:00:00Z",
        "deadline": None,
        "overflow_policy": None,
    }


def anti_trailing_policy():
    return {
        "devices": {
            "camera": {"manufacturer": "Nikon", "model": "D850"}
        },
        "optics": {"focal_length_mm": 400},
        "photo": {
            "anti_trailing_enabled": True,
            "motion_tolerance_px": 1.5,
            "iso_max": 3200,
        },
    }


def test_successful_apply_updates_only_its_rig_and_none_preserves_iso(tmp_path):
    workers = {1: FakeWorker(), 2: FakeWorker()}
    server = make_server(tmp_path, workers)

    apply_iso(server, 1, "200")
    apply_iso(server, 1, None)

    assert server._rig_iso_targets == {1: 200}
    assert workers[1].apply_calls == [
        {"aperture": None, "iso": "200"},
        {"aperture": None, "iso": None},
    ]
    assert 2 not in server._rig_iso_targets


def test_failed_apply_leaves_previous_iso_unchanged(tmp_path):
    worker = FakeWorker()
    server = make_server(tmp_path, {1: worker})
    apply_iso(server, 1, "200")
    worker.fail_apply = True

    with pytest.raises(RuntimeError, match="forced apply failure"):
        apply_iso(server, 1, "400")

    assert server._rig_iso_targets == {1: 200}


def test_revoke_clears_iso_until_new_apply(tmp_path, monkeypatch):
    sensor_db = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "camera_sensors"
        / "camera_sensors_2017plus_zwo.json"
    )
    monkeypatch.setattr(camera_ipc_server, "_SENSOR_DB_PATH", sensor_db)
    worker = FakeWorker()
    server = make_server(tmp_path, {1: worker}, policy=anti_trailing_policy())
    first_session = server.activate_session("first-session")
    apply_iso(server, 1, "200", first_session)

    server.revoke_session(first_session)

    assert server._rig_iso_targets == {}
    second_session = server.activate_session("second-session")
    with pytest.raises(IpcError) as missing_iso:
        request(
            server,
            "prepare_capture",
            {"rig_id": 1, "intent": capture_intent()},
            second_session,
        )
    assert missing_iso.value.code == "POLICY_INVALID"

    apply_iso(server, 1, "200", second_session)
    prepared = request(
        server,
        "prepare_capture",
        {"rig_id": 1, "intent": capture_intent()},
        second_session,
    )
    assert prepared["iso_applied"] == "200"


def test_stop_clears_iso_targets(tmp_path):
    server = make_server(tmp_path, {1: FakeWorker()})
    apply_iso(server, 1, "200")

    server.stop()

    assert server._rig_iso_targets == {}

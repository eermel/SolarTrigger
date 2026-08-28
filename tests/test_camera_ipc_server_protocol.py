import json
import os
import socket
import stat
import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

from backend.camera_ipc_server import CameraIpcServer, IpcError, MAX_MESSAGE_BYTES


class CountingRuntimeClock:
    def __init__(self):
        self.calls = []

    def remaining(self, deadline):
        self.calls.append(deadline)
        return 1.0


class FakeWorker:
    def __init__(self, clock=None):
        self.clock = clock
        self.calls = []

    def connect(self):
        self.calls.append(("connect",))
        return SimpleNamespace(name="fake-camera")

    def init_settings(self, **settings):
        self.calls.append(("init_settings", settings))

    def apply_phase_settings(self, **settings):
        self.calls.append(("apply_phase_settings", settings))
        return settings

    def prepare_capture(self, intent):
        self.calls.append(("prepare_capture", intent))
        return SimpleNamespace(
            token=object(), estimated_total_s=0.5, exposures_s=[0.5],
            planned_count=1, plugin_name="fake-camera"
        )

    def _consume_deadline(self, deadline):
        if deadline is not None and self.clock is not None:
            self.clock.remaining(deadline)

    def trigger_prepared(self, token, deadline=None):
        self._consume_deadline(deadline)
        self.calls.append(("trigger_prepared", token, deadline))
        return {"triggered": True}

    def shoot_speed_list(self, speeds, **options):
        self._consume_deadline(options.get("deadline"))
        self.calls.append(("shoot_speed_list", speeds, options))
        return {"shot": len(speeds)}


class FakeRuntime:
    def __init__(self, workers=None):
        self.workers = workers or {}

    def active_camera_rig_ids(self):
        return tuple(sorted(self.workers))

    def get_for_rig(self, rig_id):
        return self.workers.get(rig_id)


def make_server(tmp_path, workers=None, **kwargs):
    endpoint_dir = tmp_path / "ipc"
    return CameraIpcServer(
        FakeRuntime(workers), endpoint_dir=endpoint_dir, parent_pid=4321,
        log_fn=lambda _message: None, **kwargs
    )


def request(server, operation, params=None, session_id=None):
    value = {"operation": operation, "params": params or {}}
    if session_id is not None:
        value["session_id"] = session_id
    return server.handle_request(value)


def read_request(payload):
    left, right = socket.socketpair()
    try:
        right.sendall(payload)
        right.shutdown(socket.SHUT_WR)
        return CameraIpcServer._read_request(left)
    finally:
        left.close()
        right.close()


def test_protocol_parses_one_json_line_and_rejects_invalid_inputs():
    assert read_request(b'{"operation":"ping"}\n') == {"operation": "ping"}
    for payload, code in (
        (b"not-json\n", "INVALID_JSON"),
        (b"[]\n", "INVALID_REQUEST"),
        (b'{"operation":"ping"}\n{}\n', "INVALID_REQUEST"),
        (b"x" * (MAX_MESSAGE_BYTES + 1), "MESSAGE_TOO_LARGE"),
    ):
        with pytest.raises(IpcError) as caught:
            read_request(payload)
        assert caught.value.code == code


def test_structured_errors_and_rig_semantics(tmp_path):
    server = make_server(tmp_path)
    assert server._error("INVALID_JSON", "bad") == {
        "ok": False, "error": {"code": "INVALID_JSON", "message": "bad"}
    }
    with pytest.raises(IpcError) as invalid:
        request(server, "camera.initialize", {"rig_id": 5})
    assert invalid.value.code == "INVALID_RIG"
    with pytest.raises(IpcError) as unknown:
        request(server, "camera.initialize", {"rig_id": 1})
    assert unknown.value.code == "UNKNOWN_RIG"
    with pytest.raises(IpcError) as operation:
        request(server, "not-allowed")
    assert operation.value.code == "UNKNOWN_OPERATION"


def test_session_token_lifecycle_and_single_deadline_conversion(tmp_path):
    clock = CountingRuntimeClock()
    worker = FakeWorker(clock)
    server_clock = CountingRuntimeClock()
    server = make_server(tmp_path, {1: worker}, clock=server_clock)
    session = server.activate_session("session-one")
    prepared = request(server, "prepare_capture", {
        "rig_id": 1,
        "intent": {
            "shutter_min": "1/100", "shutter_max": "1/100",
            "step_ev": 1.0, "speeds": None, "phase": "C2",
            "target_time": "2026-08-12T18:00:00Z", "deadline": None,
            "overflow_policy": None,
        },
    }, session)
    token_id = prepared["token_id"]
    result = request(server, "trigger_prepared", {
        "rig_id": 1, "token_id": token_id,
        "deadline": "2026-08-12T18:00:01+00:00",
    }, session)
    assert result == {"triggered": True}
    assert clock.calls == [datetime(2026, 8, 12, 18, 0, 1)]
    assert server_clock.calls == []
    with pytest.raises(IpcError) as consumed:
        request(server, "trigger_prepared", {"rig_id": 1, "token_id": token_id}, session)
    assert consumed.value.code == "UNKNOWN_TOKEN"

    request(server, "shoot_speed_list", {
        "rig_id": 1, "speeds": ["1/100"],
        "deadline": "2026-08-12T20:00:00+02:00",
    }, session)
    assert clock.calls[-1] == datetime(2026, 8, 12, 18, 0)
    assert len(clock.calls) == 2
    server.revoke_session(session)


def test_revoke_session_purges_its_tokens(tmp_path):
    server = make_server(tmp_path, {1: FakeWorker()})
    session = server.activate_session("session-one")
    prepared = request(server, "prepare_capture", {
        "rig_id": 1,
        "intent": {
            "shutter_min": "1/100", "shutter_max": "1/100",
            "step_ev": 1.0, "speeds": None, "phase": "C2",
            "target_time": "2026-08-12T18:00:00Z", "deadline": None,
            "overflow_policy": None,
        },
    }, session)
    server.revoke_session(session)
    server.activate_session("session-two")
    with pytest.raises(IpcError) as caught:
        request(server, "trigger_prepared", {
            "rig_id": 1, "token_id": prepared["token_id"]
        }, "session-two")
    assert caught.value.code == "UNKNOWN_TOKEN"


def test_endpoint_permissions_filename_and_stale_socket_cleanup(tmp_path):
    server = make_server(tmp_path)
    assert server.socket_path.name == "camera-ipc-4321.sock"
    server.start()
    try:
        assert stat.S_IMODE(server.socket_path.stat().st_mode) == 0o600
    finally:
        server.stop()

    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(server.socket_path))
    stale.close()
    server.start()
    server.stop()


def test_unsafe_endpoint_directory_and_file_are_refused(tmp_path):
    unsafe_dir = tmp_path / "unsafe"
    unsafe_dir.mkdir(mode=0o777)
    os.chmod(unsafe_dir, 0o777)
    with pytest.raises(IpcError) as directory_error:
        CameraIpcServer(FakeRuntime(), endpoint_dir=unsafe_dir)
    assert directory_error.value.code == "UNSAFE_ENDPOINT"

    server = make_server(tmp_path)
    server.socket_path.write_text("do not replace")
    with pytest.raises(IpcError) as file_error:
        server.start()
    assert file_error.value.code == "UNSAFE_ENDPOINT"


def test_active_endpoint_is_detected(tmp_path):
    first = make_server(tmp_path)
    second = make_server(tmp_path)
    first.start()
    try:
        with pytest.raises(IpcError) as caught:
            second.start()
        assert caught.value.code == "ENDPOINT_IN_USE"
    finally:
        first.stop()


def test_independent_connections_are_handled_concurrently(tmp_path):
    server = make_server(tmp_path)
    server.start()
    barrier = threading.Barrier(3)
    responses = []

    def client():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(server.socket_path))
            barrier.wait(timeout=2)
            connection.sendall(b'{"operation":"ping"}\n')
            responses.append(json.loads(connection.makefile().readline()))

    threads = [threading.Thread(target=client) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
    server.stop()
    assert responses == [
        {"ok": True, "result": {"ok": True}},
        {"ok": True, "result": {"ok": True}},
    ]

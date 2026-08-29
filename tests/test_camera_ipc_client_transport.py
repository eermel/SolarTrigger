import json
import socket
import threading

import pytest

from scripts.camera_ipc_client import CameraIpcClient, CameraIpcError, MAX_MESSAGE_BYTES


class StubServer:
    def __init__(self, socket_path, handlers):
        self.socket_path = socket_path
        self.handlers = iter(handlers)
        self.requests = []
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(timeout=2)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.thread.join(timeout=2)
        assert not self.thread.is_alive()

    def _serve(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(self.socket_path))
            listener.listen()
            self.ready.set()
            for handler in self.handlers:
                connection, _ = listener.accept()
                with connection:
                    request = self._read_line(connection)
                    self.requests.append(json.loads(request))
                    handler(connection)

    @staticmethod
    def _read_line(connection):
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = connection.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)


def send_json(value):
    payload = json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"

    def handler(connection):
        connection.sendall(payload)

    return handler


def test_ping_and_list_rigs_use_independent_validated_requests(tmp_path):
    socket_path = tmp_path / "camera.sock"
    handlers = [
        send_json({"ok": True, "result": {"ok": True}}),
        send_json({"ok": True, "result": {"rig_ids": [1, 4]}}),
    ]
    with StubServer(socket_path, handlers) as server:
        client = CameraIpcClient(socket_path, "session-123", log_fn=lambda _line: None)
        assert client.ping() == {"ok": True}
        assert client.list_rigs() == {"rig_ids": [1, 4]}

    assert server.requests == [
        {"operation": "ping", "params": {}, "session_id": "session-123"},
        {
            "operation": "list_active_camera_rigs",
            "params": {},
            "session_id": "session-123",
        },
    ]


def test_oversize_response_maps_to_message_too_large_and_logs_once(tmp_path):
    socket_path = tmp_path / "camera.sock"

    def send_oversize(connection):
        connection.sendall(b"x" * (MAX_MESSAGE_BYTES + 1))

    logs = []
    with StubServer(socket_path, [send_oversize]):
        client = CameraIpcClient(socket_path, "session-123", log_fn=logs.append)
        with pytest.raises(CameraIpcError) as caught:
            client.ping()

    assert caught.value.code == "MESSAGE_TOO_LARGE"
    assert caught.value.operation == "ping"
    assert logs == [
        "CAMERA_IPC_ERROR code=MESSAGE_TOO_LARGE operation=ping "
        "rig_id=none message=response exceeds size limit"
    ]


def test_server_error_preserves_invalid_session_and_logs_once(tmp_path):
    socket_path = tmp_path / "camera.sock"
    response = {
        "ok": False,
        "error": {"code": "INVALID_SESSION", "message": "session is inactive"},
    }
    logs = []
    with StubServer(socket_path, [send_json(response)]):
        client = CameraIpcClient(socket_path, "invalid-secret", log_fn=logs.append)
        with pytest.raises(CameraIpcError) as caught:
            client.list_active_camera_rigs()

    assert caught.value.code == "INVALID_SESSION"
    assert caught.value.operation == "list_active_camera_rigs"
    assert logs == [
        "CAMERA_IPC_ERROR code=INVALID_SESSION operation=list_active_camera_rigs "
        "rig_id=none message=session is inactive"
    ]
    assert "invalid-secret" not in logs[0]


def test_connection_loss_has_stable_error_and_one_log(tmp_path):
    socket_path = tmp_path / "camera.sock"
    logs = []

    with StubServer(socket_path, [lambda _connection: None]):
        client = CameraIpcClient(
            socket_path,
            "session-123",
            log_fn=logs.append,
        )
        with pytest.raises(CameraIpcError) as caught:
            client.ping(timeout_s=1.0)

    assert caught.value.code == "IPC_UNAVAILABLE"
    assert caught.value.operation == "ping"
    assert logs == [
        "CAMERA_IPC_ERROR code=IPC_UNAVAILABLE operation=ping "
        "rig_id=none message=camera IPC is unavailable"
    ]


def test_timeout_has_stable_error_and_one_log(tmp_path):
    socket_path = tmp_path / "camera.sock"
    logs = []

    request_received = threading.Event()
    release_server = threading.Event()

    def hold_connection(_connection):
        request_received.set()
        release_server.wait()

    with StubServer(socket_path, [hold_connection]):
        client = CameraIpcClient(
            socket_path,
            "session-123",
            log_fn=logs.append,
        )

        try:
            with pytest.raises(CameraIpcError) as caught:
                client.ping(timeout_s=0.01)

            assert request_received.wait(timeout=1.0)
        finally:
            # Le serveur est libéré explicitement : aucun sleep n'est utilisé
            # comme mécanisme de synchronisation du test.
            release_server.set()

    assert caught.value.code == "TIMEOUT"
    assert caught.value.operation == "ping"
    assert logs == [
        "CAMERA_IPC_ERROR code=TIMEOUT operation=ping "
        "rig_id=none message=camera IPC request timed out"
    ]

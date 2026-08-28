from datetime import datetime

from backend.camera_ipc_server import CameraIpcServer
from backend.camera_worker import CameraWorker
from backend.camera_worker_runtime import CameraWorkerRuntime
from backend.generic_worker import GenericWorker
from plugins.camera.base import CaptureResult
from services.camera_service import CameraService


class CountingRuntimeClock:
    def __init__(self, remaining_seconds):
        self.remaining_seconds = remaining_seconds
        self.calls = []

    def remaining(self, deadline):
        self.calls.append(deadline)
        return self.remaining_seconds


class RecordingCameraPlugin:
    def __init__(self):
        self.deadlines = []
        self.result = CaptureResult(frames=1, planned=1, detail="ok")

    def shoot_speeds(self, _fastest, _slowest, _step_il, **options):
        self.deadlines.append(options["deadline"])
        return self.result


def _rig():
    return {
        "rig_id": 1,
        "enabled": True,
        "devices": {"camera": {"backend": "gphoto2"}},
    }


def test_ipc_deadline_is_converted_once_at_camera_service_boundary(
    tmp_path, monkeypatch
):
    clock = CountingRuntimeClock(remaining_seconds=12.5)
    plugin = RecordingCameraPlugin()
    service = CameraService(clock=clock)
    service.plugin = plugin
    worker_clocks = []
    worker_deadlines = []
    server_clocks = []
    servers = []
    monkeypatch.setattr("services.camera_service.time.monotonic", lambda: 100.0)

    original_submit_with_priority = GenericWorker.submit_with_priority

    def recording_submit_with_priority(self, priority, callable, *args, **kwargs):
        worker_deadlines.append(kwargs.get("worker_deadline"))
        return original_submit_with_priority(
            self, priority, callable, *args, **kwargs
        )

    monkeypatch.setattr(
        GenericWorker,
        "submit_with_priority",
        recording_submit_with_priority,
    )

    def worker_factory(*, rig_id, clock, log_fn):
        worker_clocks.append(clock)
        return CameraWorker(
            rig_id=rig_id,
            clock=clock,
            log_fn=log_fn,
            service_factory=lambda: service,
        )

    def ipc_server_factory(runtime, *, clock, log_fn):
        server_clocks.append(clock)
        server = CameraIpcServer(
            runtime,
            clock=clock,
            log_fn=log_fn,
            endpoint_dir=tmp_path / "ipc",
            parent_pid=4321,
        )
        server.start = lambda: server.socket_path
        servers.append(server)
        return server

    runtime = CameraWorkerRuntime(
        clock=clock,
        worker_factory=worker_factory,
        ipc_server_factory=ipc_server_factory,
        log_fn=lambda _message: None,
    )
    runtime.reconcile({"rigs": [_rig()]})
    session = runtime.open_ipc_session()
    try:
        result = servers[0].handle_request(
            {
                "operation": "shoot_speed_list",
                "session_id": session.session_id,
                "params": {
                    "rig_id": 1,
                    "speeds": ["1/100"],
                    "deadline": "2026-08-12T20:00:00+02:00",
                },
            }
        )

        assert result is plugin.result
        assert worker_clocks == [clock]
        assert server_clocks == [clock]
        assert clock.calls == [datetime(2026, 8, 12, 18, 0)]
        assert plugin.deadlines == [112.5]
        assert worker_deadlines == plugin.deadlines
    finally:
        runtime.shutdown()

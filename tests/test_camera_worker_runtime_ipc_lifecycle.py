from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.camera_ipc_server import CameraIpcServer
from backend.camera_worker_runtime import CameraIpcSession, CameraWorkerRuntime


class FakeWorker:
    def __init__(self, *, rig_id, clock, log_fn):
        self.rig_id = rig_id
        self.clock = clock
        self.log_fn = log_fn
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _rig(rig_id=1):
    return {
        "rig_id": rig_id,
        "enabled": True,
        "devices": {"camera": {"backend": "gphoto2"}},
    }


def _runtime(tmp_path, *, clock=None):
    servers = []

    def server_factory(runtime, **kwargs):
        server = CameraIpcServer(
            runtime,
            endpoint_dir=tmp_path / "ipc",
            parent_pid=4321,
            **kwargs,
        )
        servers.append(server)
        return server

    runtime = CameraWorkerRuntime(
        clock=clock,
        worker_factory=FakeWorker,
        ipc_server_factory=server_factory,
        log_fn=lambda _message: None,
    )
    return runtime, servers


def test_import_construction_and_reconcile_do_not_create_ipc_socket(tmp_path):
    runtime, servers = _runtime(tmp_path)

    runtime.reconcile({"rigs": [_rig()]})

    assert servers == []
    assert not (tmp_path / "ipc").exists()
    runtime.shutdown()


def test_open_requires_an_active_camera_rig(tmp_path):
    runtime, servers = _runtime(tmp_path)

    with pytest.raises(RuntimeError, match="without active camera rigs"):
        runtime.open_ipc_session()

    assert servers == []


def test_open_starts_server_and_returns_an_immutable_absolute_lease(tmp_path):
    runtime, servers = _runtime(tmp_path)
    runtime.reconcile({"rigs": [_rig()]})

    session = runtime.open_ipc_session()

    assert isinstance(session, CameraIpcSession)
    assert Path(session.socket_path).is_absolute()
    assert Path(session.socket_path).is_socket()
    assert servers[0]._active_session == session.session_id
    with pytest.raises(FrozenInstanceError):
        session.session_id = "replacement"
    runtime.close_ipc_session(session.session_id)


def test_last_close_unlinks_socket_and_restart_reuses_owned_clock(tmp_path):
    clock = object()
    runtime, servers = _runtime(tmp_path, clock=clock)
    runtime.reconcile({"rigs": [_rig()]})

    first = runtime.open_ipc_session()
    first_path = Path(first.socket_path)
    assert servers[0]._clock is clock

    runtime.close_ipc_session(first.session_id)

    assert not first_path.exists()
    assert runtime.active_camera_rig_ids() == (1,)

    second = runtime.open_ipc_session()
    assert len(servers) == 2
    assert servers[1]._clock is clock
    assert runtime._clock is clock
    assert Path(second.socket_path).is_socket()
    runtime.close_ipc_session(second.session_id)


def test_shutdown_stops_ipc_before_workers_and_clears_runtime(tmp_path):
    runtime, _servers = _runtime(tmp_path)
    runtime.reconcile({"rigs": [_rig()]})
    worker = runtime.get_for_rig(1)
    session = runtime.open_ipc_session()

    runtime.shutdown()

    assert not Path(session.socket_path).exists()
    assert worker.stopped is True
    assert runtime.active_camera_rig_ids() == ()
    with pytest.raises(ValueError, match="not active"):
        runtime.close_ipc_session(session.session_id)

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import socket
import threading
import time

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
        self.recovery_clears = 0

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def clear_runtime_recovery_state(self):
        self.recovery_clears += 1


def _rig(rig_id=1):
    return {
        "rig_id": rig_id,
        "enabled": True,
        "devices": {"camera": {"backend": "gphoto2"}},
    }


def _runtime(tmp_path, *, clock=None, worker_factory=FakeWorker):
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
        worker_factory=worker_factory,
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


def test_session_close_clears_only_its_rig_recovery_state(tmp_path):
    runtime, _servers = _runtime(tmp_path)
    runtime.reconcile({"rigs": [_rig(1), _rig(2)]})
    worker1 = runtime.get_for_rig(1)
    worker2 = runtime.get_for_rig(2)

    session1 = runtime.open_ipc_session(rig_ids=(1,))
    session2 = runtime.open_ipc_session(rig_ids=(2,))

    runtime.close_ipc_session(session1.session_id)

    assert worker1.recovery_clears == 1
    assert worker2.recovery_clears == 0

    runtime.close_ipc_session(session2.session_id)

    assert worker1.recovery_clears == 1
    assert worker2.recovery_clears == 1


class OwnershipWorker(FakeWorker):
    events = []
    fail_stop = False

    def __init__(self, *, rig_id, clock, log_fn):
        super().__init__(rig_id=rig_id, clock=clock, log_fn=log_fn)
        self.camera_entry = None
        self.stop_calls = 0

    def configure_camera(self, camera_entry):
        self.camera_entry = dict(camera_entry)

    def start(self):
        self.started = True
        self.events.append(("start", self.camera_entry.get("model")))

    def stop(self):
        self.stop_calls += 1
        self.events.append(("stop", self.camera_entry.get("model")))
        if self.fail_stop:
            return False
        self.stopped = True
        return True


def _rig_with_model(model):
    rig = _rig()
    rig["devices"]["camera"]["model"] = model
    return rig


def test_reconcile_stops_previous_camera_owner_before_starting_replacement(
    tmp_path,
):
    OwnershipWorker.events = []
    OwnershipWorker.fail_stop = False
    runtime, _servers = _runtime(
        tmp_path,
        worker_factory=OwnershipWorker,
    )

    runtime.reconcile({"rigs": [_rig_with_model("OLD")]})
    old_worker = runtime.get_for_rig(1)
    OwnershipWorker.events.clear()

    runtime.reconcile({"rigs": [_rig_with_model("NEW")]})

    new_worker = runtime.get_for_rig(1)
    assert new_worker is not old_worker
    assert OwnershipWorker.events == [
        ("stop", "OLD"),
        ("start", "NEW"),
    ]
    runtime.shutdown()


def test_reconcile_retains_unstoppable_owner_and_never_starts_replacement(
    tmp_path,
):
    class UnstoppableOwnershipWorker(OwnershipWorker):
        fail_stop = True
        instances = []

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.instances.append(self)

    UnstoppableOwnershipWorker.events = []
    UnstoppableOwnershipWorker.instances = []
    runtime, _servers = _runtime(
        tmp_path,
        worker_factory=UnstoppableOwnershipWorker,
    )

    runtime.reconcile({"rigs": [_rig_with_model("OLD")]})
    old_worker = runtime.get_for_rig(1)

    with pytest.raises(RuntimeError, match="ownership was not released"):
        runtime.reconcile({"rigs": [_rig_with_model("NEW")]})

    assert runtime.get_for_rig(1) is old_worker
    assert runtime.active_camera_rig_ids() == (1,)
    assert len(UnstoppableOwnershipWorker.instances) == 1
    assert UnstoppableOwnershipWorker.events == [
        ("start", "OLD"),
        ("stop", "OLD"),
    ]


def test_release_idle_workers_refuses_direct_access_if_owner_survives(
    tmp_path,
):
    class UnstoppableOwnershipWorker(OwnershipWorker):
        fail_stop = True

    UnstoppableOwnershipWorker.events = []
    runtime, _servers = _runtime(
        tmp_path,
        worker_factory=UnstoppableOwnershipWorker,
    )
    runtime.reconcile({"rigs": [_rig_with_model("CAMERA")]})
    worker = runtime.get_for_rig(1)

    with pytest.raises(RuntimeError, match="direct camera access refused"):
        runtime.release_idle_workers()

    assert runtime.get_for_rig(1) is worker
    assert runtime.active_camera_rig_ids() == (1,)
    assert worker.stop_calls == 1


def test_shutdown_retains_unstoppable_camera_owner_for_retry(tmp_path):
    class RetryableStopWorker(OwnershipWorker):
        fail_stop = True

    RetryableStopWorker.events = []
    runtime, _servers = _runtime(
        tmp_path,
        worker_factory=RetryableStopWorker,
    )
    runtime.reconcile({"rigs": [_rig_with_model("CAMERA")]})
    worker = runtime.get_for_rig(1)

    with pytest.raises(RuntimeError, match="could not release ownership"):
        runtime.shutdown()

    assert runtime.get_for_rig(1) is worker
    assert runtime.active_camera_rig_ids() == (1,)

    RetryableStopWorker.fail_stop = False
    runtime.shutdown()

    assert runtime.get_for_rig(1) is None
    assert runtime.active_camera_rig_ids() == ()


def test_shutdown_removes_only_workers_that_confirmed_stop(tmp_path):
    class MixedStopWorker(OwnershipWorker):
        instances = {}

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.__class__.instances[self.rig_id] = self

        def stop(self):
            self.stop_calls += 1
            if self.rig_id == 2:
                return False
            self.stopped = True
            return True

    MixedStopWorker.instances = {}
    runtime, _servers = _runtime(
        tmp_path,
        worker_factory=MixedStopWorker,
    )
    runtime.reconcile({"rigs": [_rig(1), _rig(2)]})

    with pytest.raises(RuntimeError, match="RIG 2"):
        runtime.shutdown()

    assert runtime.get_for_rig(1) is None
    assert runtime.get_for_rig(2) is MixedStopWorker.instances[2]
    assert runtime.active_camera_rig_ids() == (2,)


def test_camera_ipc_stop_does_not_unlink_replacement_socket(tmp_path):
    class Runtime:
        def active_camera_rig_ids(self):
            return ()

        def get_for_rig(self, _rig_id):
            return None

    server = CameraIpcServer(
        Runtime(),
        endpoint_dir=tmp_path / "ipc-race",
        parent_pid=9876,
        log_fn=lambda _message: None,
    )
    path = server.start()

    # Simulate a replacement endpoint appearing after the old listener becomes
    # unreachable but before its stop path unlinks the pathname.
    path.unlink()
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement.bind(str(path))
    try:
        replacement_stat = path.lstat()

        server.stop(timeout=1.0)

        current = path.lstat()
        assert (current.st_dev, current.st_ino) == (
            replacement_stat.st_dev,
            replacement_stat.st_ino,
        )
    finally:
        replacement.close()
        path.unlink(missing_ok=True)


def test_camera_ipc_stop_fails_closed_until_active_handler_drains(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    class BlockingWorker:
        def get_parameter(self, _parameter):
            entered.set()
            assert release.wait(2.0)
            return "ok"

    worker = BlockingWorker()

    class Runtime:
        def active_camera_rig_ids(self):
            return (1,)

        def get_for_rig(self, rig_id):
            return worker if rig_id == 1 else None

    server = CameraIpcServer(
        Runtime(),
        endpoint_dir=tmp_path / "ipc-drain",
        parent_pid=2468,
        log_fn=lambda _message: None,
    )
    path = server.start()
    session_id = server.activate_session("drain-session", (1,))
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(path))
    client.sendall(
        (
            json.dumps(
                {
                    "operation": "camera.get_parameter",
                    "params": {"rig_id": 1, "parameter": "iso"},
                    "session_id": session_id,
                }
            )
            + "\n"
        ).encode("utf-8")
    )

    assert entered.wait(1.0)

    try:
        assert server.stop(timeout=0.05) is False
        assert server.stopping is True
        assert server.stopped is False

        release.set()
        deadline = time.monotonic() + 1.0
        while not server.stopped and time.monotonic() < deadline:
            time.sleep(0.01)

        assert server.stopped is True
        assert server.stopping is False
    finally:
        release.set()
        client.close()
        server.stop(timeout=1.0)


def test_runtime_shutdown_does_not_stop_workers_while_ipc_handler_survives(
    tmp_path,
):
    runtime, _servers = _runtime(
        tmp_path,
        worker_factory=OwnershipWorker,
    )
    OwnershipWorker.events = []
    OwnershipWorker.fail_stop = False
    runtime.reconcile({"rigs": [_rig_with_model("CAMERA")]})
    worker = runtime.get_for_rig(1)

    class DrainingServer:
        def __init__(self):
            self.stopping = True
            self.stopped = False
            self.stop_calls = 0

        def stop(self, timeout=None):
            self.stop_calls += 1
            return False

    server = DrainingServer()
    runtime._ipc_server = server

    with pytest.raises(RuntimeError, match="active handlers did not drain"):
        runtime.shutdown()

    assert worker.stop_calls == 0
    assert runtime.get_for_rig(1) is worker
    assert runtime._ipc_server is server

    server.stopping = False
    server.stopped = True
    runtime.shutdown()

    assert worker.stop_calls == 1
    assert runtime.get_for_rig(1) is None


def test_failed_last_session_stop_freezes_new_camera_ownership(tmp_path):
    holder = {}

    class NonDrainingServer:
        def __init__(self, runtime, **_kwargs):
            self.runtime = runtime
            self.socket_path = tmp_path / "pending-camera-ipc.sock"
            self.sessions = set()
            self.stopping = False
            self.stopped = False

        def start(self):
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            return self.socket_path

        def activate_session(self, session_id, rig_ids=None):
            self.sessions.add(session_id)

        def revoke_session(self, session_id):
            self.sessions.remove(session_id)

        def stop(self, timeout=None):
            self.stopping = True
            return False

    def server_factory(runtime, **kwargs):
        server = NonDrainingServer(runtime, **kwargs)
        holder["server"] = server
        return server

    runtime = CameraWorkerRuntime(
        worker_factory=OwnershipWorker,
        ipc_server_factory=server_factory,
        log_fn=lambda _message: None,
    )
    OwnershipWorker.events = []
    OwnershipWorker.fail_stop = False
    runtime.reconcile({"rigs": [_rig_with_model("CAMERA")]})
    session = runtime.open_ipc_session((1,))
    server = holder["server"]

    with pytest.raises(RuntimeError, match="did not drain active handlers"):
        runtime.close_ipc_session(session.session_id)

    assert runtime._ipc_server is server
    assert server.stopping is True

    with pytest.raises(RuntimeError, match="shutdown is still in progress"):
        runtime.open_ipc_session((1,))

    changed = {"rigs": [_rig_with_model("NEW")]}
    with pytest.raises(RuntimeError, match="shutdown is still in progress"):
        runtime.reconcile(changed)

    with pytest.raises(RuntimeError, match="shutdown is still in progress"):
        runtime.release_idle_workers()


def test_policy_snapshot_failure_prevents_ipc_server_and_session_creation(
    tmp_path,
    monkeypatch,
):
    runtime, servers = _runtime(tmp_path)
    runtime.reconcile({"rigs": [_rig()]})

    def fail_policy(_rig_id):
        raise RuntimeError("synthetic policy snapshot failure")

    monkeypatch.setattr(
        runtime,
        "get_policy_config_for_rig",
        fail_policy,
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic policy snapshot failure",
    ):
        runtime.open_ipc_session((1,))

    assert servers == []
    assert runtime._ipc_server is None
    assert runtime._ipc_session_ids == set()
    assert runtime._ipc_session_rigs == {}
    assert runtime._leased_policy_configs == {}
    assert runtime.active_camera_rig_ids() == (1,)

    runtime.shutdown()


def test_activation_failure_retains_non_drained_ipc_server_fail_closed(
    tmp_path,
):
    holder = {}

    class ActivationFailServer:
        def __init__(self, runtime, **_kwargs):
            self.runtime = runtime
            self.socket_path = tmp_path / "activation-fail.sock"
            self.stopping = False
            self.stopped = False
            self.stop_calls = 0

        def start(self):
            self.socket_path.parent.mkdir(parents=True, exist_ok=True)
            return self.socket_path

        def activate_session(self, _session_id, _rig_ids=None):
            raise RuntimeError("synthetic activation failure")

        def stop(self, timeout=None):
            self.stop_calls += 1
            self.stopping = True
            return False

    def server_factory(runtime, **kwargs):
        server = ActivationFailServer(runtime, **kwargs)
        holder["server"] = server
        return server

    runtime = CameraWorkerRuntime(
        worker_factory=OwnershipWorker,
        ipc_server_factory=server_factory,
        log_fn=lambda _message: None,
    )
    OwnershipWorker.events = []
    OwnershipWorker.fail_stop = False
    runtime.reconcile({"rigs": [_rig_with_model("CAMERA")]})

    with pytest.raises(
        RuntimeError,
        match="activation failed and server cleanup did not complete",
    ) as caught:
        runtime.open_ipc_session((1,))

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert "synthetic activation failure" in str(caught.value.__cause__)

    server = holder["server"]
    assert server.stop_calls == 1
    assert server.stopping is True
    assert runtime._ipc_server is server
    assert runtime._ipc_session_ids == set()
    assert runtime._ipc_session_rigs == {}
    assert runtime._leased_policy_configs == {}

    with pytest.raises(RuntimeError, match="shutdown is still in progress"):
        runtime.open_ipc_session((1,))

    with pytest.raises(RuntimeError, match="shutdown is still in progress"):
        runtime.release_idle_workers()

    server.stopping = False
    server.stopped = True
    runtime.shutdown()

    assert runtime._ipc_server is None
    assert runtime.active_camera_rig_ids() == ()

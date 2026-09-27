from pathlib import Path

import pytest

from backend.focuser_worker_runtime import FocuserWorkerRuntime
from backend.mount_worker_runtime import MountWorkerRuntime


class FakeProcessWorker:
    created = []

    def __init__(
        self,
        *,
        rig_id,
        backend,
        device_config,
        state_path,
        log_fn,
    ):
        self.rig_id = rig_id
        self.backend = backend
        self.device_config = device_config
        self.state_path = Path(state_path)
        self.log_fn = log_fn
        self.started = False
        self.stopped = False
        type(self).created.append(self)

    def start(self):
        self.started = True

    def shutdown(self, timeout=2.0):
        self.stopped = True
        return True


def _mount_config(serial="mount-1"):
    return {
        "rigs": [
            {
                "rig_id": 1,
                "devices": {
                    "mount": {
                        "backend": "indi",
                        "serial": serial,
                        "plugin_config": {
                            "host": "127.0.0.1",
                        },
                    }
                },
            }
        ]
    }


def _focuser_config(serial="focus-2"):
    return {
        "rigs": [
            {
                "rig_id": 2,
                "devices": {
                    "focuser": {
                        "backend": "zwo_eaf",
                        "serial": serial,
                        "limits": [0, 50000],
                    }
                },
            }
        ]
    }


def test_mount_runtime_process_mode_builds_serializable_worker(tmp_path):
    FakeProcessWorker.created = []

    runtime = MountWorkerRuntime(
        state_path=tmp_path / "state.json",
        process_worker_factory=FakeProcessWorker,
        log_fn=lambda _message: None,
    )

    runtime.reconcile(_mount_config())

    worker = runtime.get_for_rig(1)

    assert worker is FakeProcessWorker.created[0]
    assert worker.started is True
    assert worker.backend == "indi"
    assert worker.device_config == {
        "backend": "indi",
        "serial": "mount-1",
        "plugin_config": {
            "host": "127.0.0.1",
        },
    }
    assert worker.state_path == tmp_path / "state.json"

    runtime.stop_all()

    assert worker.stopped is True


def test_focuser_runtime_process_mode_builds_serializable_worker(tmp_path):
    FakeProcessWorker.created = []

    runtime = FocuserWorkerRuntime(
        state_path=tmp_path / "state.json",
        process_worker_factory=FakeProcessWorker,
        log_fn=lambda _message: None,
    )

    runtime.reconcile(_focuser_config())

    worker = runtime.get_for_rig(2)

    assert worker is FakeProcessWorker.created[0]
    assert worker.started is True
    assert worker.backend == "zwo_eaf"
    assert worker.device_config == {
        "backend": "zwo_eaf",
        "serial": "focus-2",
        "limits": [0, 50000],
    }
    assert worker.state_path == tmp_path / "state.json"

    runtime.stop_all()

    assert worker.stopped is True


def test_mount_runtime_replaces_changed_process_worker(tmp_path):
    FakeProcessWorker.created = []

    runtime = MountWorkerRuntime(
        state_path=tmp_path / "state.json",
        process_worker_factory=FakeProcessWorker,
        log_fn=lambda _message: None,
    )

    runtime.reconcile(_mount_config("mount-1"))
    first = runtime.get_for_rig(1)

    runtime.reconcile(_mount_config("mount-2"))
    second = runtime.get_for_rig(1)

    assert second is not first
    assert first.stopped is True
    assert second.started is True

    runtime.stop_all()


def test_focuser_runtime_replaces_changed_process_worker(tmp_path):
    FakeProcessWorker.created = []

    runtime = FocuserWorkerRuntime(
        state_path=tmp_path / "state.json",
        process_worker_factory=FakeProcessWorker,
        log_fn=lambda _message: None,
    )

    runtime.reconcile(_focuser_config("focus-1"))
    first = runtime.get_for_rig(2)

    runtime.reconcile(_focuser_config("focus-2"))
    second = runtime.get_for_rig(2)

    assert second is not first
    assert first.stopped is True
    assert second.started is True

    runtime.stop_all()


@pytest.mark.parametrize(
    ("runtime_cls", "config_factory", "rig_id", "old_id", "new_id"),
    [
        (MountWorkerRuntime, _mount_config, 1, "mount-1", "mount-2"),
        (FocuserWorkerRuntime, _focuser_config, 2, "focus-1", "focus-2"),
    ],
)
def test_runtime_stops_old_owner_before_starting_replacement(
    tmp_path,
    runtime_cls,
    config_factory,
    rig_id,
    old_id,
    new_id,
):
    events = []

    class OrderedWorker(FakeProcessWorker):
        created = []

        def start(self):
            events.append(("start", self.device_config["serial"]))
            super().start()

        def shutdown(self, timeout=2.0):
            events.append(("shutdown", self.device_config["serial"]))
            return super().shutdown(timeout=timeout)

    runtime = runtime_cls(
        state_path=tmp_path / "state.json",
        process_worker_factory=OrderedWorker,
        log_fn=lambda _message: None,
    )
    runtime.reconcile(config_factory(old_id))
    old_worker = runtime.get_for_rig(rig_id)
    events.clear()

    runtime.reconcile(config_factory(new_id))

    assert runtime.get_for_rig(rig_id) is not old_worker
    assert events == [
        ("shutdown", old_id),
        ("start", new_id),
    ]
    runtime.stop_all()


@pytest.mark.parametrize(
    ("runtime_cls", "config_factory", "rig_id", "old_id", "new_id"),
    [
        (MountWorkerRuntime, _mount_config, 1, "mount-1", "mount-2"),
        (FocuserWorkerRuntime, _focuser_config, 2, "focus-1", "focus-2"),
    ],
)
def test_runtime_keeps_unstoppable_owner_and_never_starts_replacement(
    tmp_path,
    runtime_cls,
    config_factory,
    rig_id,
    old_id,
    new_id,
):
    events = []

    class UnstoppableWorker(FakeProcessWorker):
        created = []

        def start(self):
            events.append(("start", self.device_config["serial"]))
            super().start()

        def shutdown(self, timeout=2.0):
            serial = self.device_config["serial"]
            events.append(("shutdown", serial))
            if serial == old_id:
                return False
            return super().shutdown(timeout=timeout)

    runtime = runtime_cls(
        state_path=tmp_path / "state.json",
        process_worker_factory=UnstoppableWorker,
        log_fn=lambda _message: None,
    )
    runtime.reconcile(config_factory(old_id))
    old_worker = runtime.get_for_rig(rig_id)
    events.clear()

    with pytest.raises(RuntimeError, match="ownership was not released"):
        runtime.reconcile(config_factory(new_id))

    assert runtime.get_for_rig(rig_id) is old_worker
    assert UnstoppableWorker.created[1].started is False
    assert ("start", new_id) not in events


@pytest.mark.parametrize(
    ("runtime_cls", "config_factory", "rig_id", "device_id"),
    [
        (MountWorkerRuntime, _mount_config, 1, "mount-1"),
        (FocuserWorkerRuntime, _focuser_config, 2, "focus-1"),
    ],
)
def test_stop_all_retains_worker_when_shutdown_reports_false(
    tmp_path,
    runtime_cls,
    config_factory,
    rig_id,
    device_id,
):
    class UnstoppableWorker(FakeProcessWorker):
        created = []

        def shutdown(self, timeout=2.0):
            return False

    runtime = runtime_cls(
        state_path=tmp_path / "state.json",
        process_worker_factory=UnstoppableWorker,
        log_fn=lambda _message: None,
    )
    runtime.reconcile(config_factory(device_id))
    worker = runtime.get_for_rig(rig_id)

    with pytest.raises(RuntimeError, match="could not release worker ownership"):
        runtime.stop_all(timeout=0.01)

    assert runtime.get_for_rig(rig_id) is worker

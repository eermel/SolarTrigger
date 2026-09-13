from pathlib import Path

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

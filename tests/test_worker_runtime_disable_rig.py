from __future__ import annotations

import backend.focuser_worker_runtime as focuser_runtime_module
import backend.mount_worker_runtime as mount_runtime_module
from backend.focuser_worker_runtime import FocuserWorkerRuntime
from backend.mount_worker_runtime import MountWorkerRuntime


class StubWorker:
    instances: list["StubWorker"] = []

    def __init__(self, *, rig_id: int, service_factory, log_fn) -> None:
        self.rig_id = rig_id
        self.start_calls = 0
        self.shutdown_calls = 0
        self.__class__.instances.append(self)

    def start(self) -> None:
        self.start_calls += 1

    def shutdown(self, timeout=None) -> None:
        self.shutdown_calls += 1


def _config(*, rig_1_enabled: bool) -> dict:
    return {
        "rigs": [
            {
                "rig_id": 1,
                "enabled": rig_1_enabled,
                "devices": {
                    "mount": {"backend": "indi", "serial": "mount-1"},
                    "focuser": {"backend": "indi", "serial": "focuser-1"},
                },
            },
            {
                "rig_id": 2,
                "enabled": True,
                "devices": {
                    "mount": {"backend": "indi", "serial": "mount-2"},
                    "focuser": None,
                },
            },
        ]
    }


def _service_factory_provider(_binding):
    return object


def test_mount_runtime_disables_only_target_rig_workers(monkeypatch):
    StubWorker.instances = []
    monkeypatch.setattr(mount_runtime_module, "MountWorker", StubWorker)
    runtime = MountWorkerRuntime(_service_factory_provider)

    runtime.reconcile(_config(rig_1_enabled=True))
    rig_1_worker = runtime.get_for_rig(1)
    rig_2_worker = runtime.get_for_rig(2)

    runtime.reconcile(_config(rig_1_enabled=False))

    assert rig_1_worker is not None
    assert rig_1_worker.start_calls == 1
    assert rig_1_worker.shutdown_calls == 1
    assert runtime.get_for_rig(1) is None
    assert runtime.get_for_rig(2) is rig_2_worker
    assert rig_2_worker is not None
    assert rig_2_worker.start_calls == 1
    assert rig_2_worker.shutdown_calls == 0


def test_focuser_runtime_disables_only_target_rig_workers(monkeypatch):
    StubWorker.instances = []
    monkeypatch.setattr(focuser_runtime_module, "FocuserWorker", StubWorker)
    runtime = FocuserWorkerRuntime(_service_factory_provider)

    runtime.reconcile(_config(rig_1_enabled=True))
    rig_1_worker = runtime.get_for_rig(1)

    runtime.reconcile(_config(rig_1_enabled=False))

    assert rig_1_worker is not None
    assert rig_1_worker.start_calls == 1
    assert rig_1_worker.shutdown_calls == 1
    assert runtime.get_for_rig(1) is None
    assert runtime.get_for_rig(2) is None
    assert StubWorker.instances == [rig_1_worker]

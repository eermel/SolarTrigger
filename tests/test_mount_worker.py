from __future__ import annotations

import threading

from backend.mount_worker import MountWorker


class FakeMountService:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.close_calls = 0
        self.status_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def status(self) -> str:
        self.status_calls += 1
        return "ready"


def _mount_worker_threads(rig_id: int) -> list[threading.Thread]:
    name = f"mount-worker-r{rig_id}"
    return [thread for thread in threading.enumerate() if thread.name == name]


def test_stop_invokes_hardware_stop_and_leaves_worker_running():
    rig_id = 301
    service = FakeMountService()
    worker = MountWorker(rig_id=rig_id, service_factory=lambda: service)

    assert not _mount_worker_threads(rig_id)
    worker.start()
    try:
        assert worker.running
        assert len(_mount_worker_threads(rig_id)) == 1

        worker.stop()

        assert service.stop_calls == 1
        assert worker.running
        assert len(_mount_worker_threads(rig_id)) == 1
        assert worker.status() == "ready"
        assert service.status_calls == 1
    finally:
        worker.shutdown(timeout=1.0)

    assert service.close_calls == 1
    assert not worker.running
    assert not _mount_worker_threads(rig_id)


def test_shutdown_stops_thread_and_closes_once():
    rig_id = 302
    service = FakeMountService()
    worker = MountWorker(rig_id=rig_id, service_factory=lambda: service)

    assert not _mount_worker_threads(rig_id)
    worker.start()
    assert worker.running
    assert len(_mount_worker_threads(rig_id)) == 1

    worker.status()
    worker.shutdown(timeout=1.0)

    assert not worker.running
    assert not _mount_worker_threads(rig_id)
    assert service.close_calls == 1

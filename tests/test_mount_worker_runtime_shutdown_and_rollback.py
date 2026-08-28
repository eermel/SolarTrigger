from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

import backend.mount_worker_runtime as runtime_module
from backend.mount_worker_runtime import MountBinding, MountWorkerRuntime


class FakeMountService:
    def status(self) -> str:
        return "ready"

    def close(self) -> None:
        pass


def two_mount_config(*, revision: int | None = None) -> dict[str, Any]:
    rigs = []
    for rig_id, serial in ((601, "runtime-mount-a"), (602, "runtime-mount-b")):
        mount: dict[str, Any] = {"backend": "indi", "serial": serial}
        if revision is not None:
            mount["revision"] = revision
        rigs.append(
            {
                "rig_id": rig_id,
                "enabled": True,
                "devices": {"mount": mount},
            }
        )
    return {"rigs": rigs}


def working_provider(_binding: MountBinding) -> Callable[[], FakeMountService]:
    return FakeMountService


def mount_threads() -> list[threading.Thread]:
    names = {"mount-worker-r601", "mount-worker-r602"}
    return [thread for thread in threading.enumerate() if thread.name in names]


def registry_workers(runtime: MountWorkerRuntime) -> list[Any]:
    return [entry.worker for entry in runtime._registry.values()]


def test_stop_all_removes_both_workers_and_runtime_can_reconcile_again():
    runtime = MountWorkerRuntime(working_provider, log_fn=lambda *_args: None)
    try:
        runtime.reconcile(two_mount_config())

        assert len(runtime._registry) == 2
        assert len(mount_threads()) == 2
        assert all(worker.running for worker in registry_workers(runtime))

        runtime.stop_all(timeout=1.0)

        assert runtime._registry == {}
        assert mount_threads() == []

        runtime.reconcile(two_mount_config())

        assert len(runtime._registry) == 2
        assert len(mount_threads()) == 2
    finally:
        runtime.stop_all(timeout=1.0)

    assert mount_threads() == []


def test_provider_failure_on_second_creation_rolls_back_from_empty():
    calls = 0

    def failing_provider(
        _binding: MountBinding,
    ) -> Callable[[], FakeMountService]:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("second binding failed")
        return FakeMountService

    runtime = MountWorkerRuntime(failing_provider, log_fn=lambda *_args: None)
    try:
        with pytest.raises(RuntimeError, match="second binding failed"):
            runtime.reconcile(two_mount_config())

        assert calls == 2
        assert runtime._registry == {}
        assert mount_threads() == []
    finally:
        runtime.stop_all(timeout=1.0)


def test_provider_failure_preserves_previously_running_workers():
    fail_creations = False
    replacement_calls = 0

    def provider(_binding: MountBinding) -> Callable[[], FakeMountService]:
        nonlocal replacement_calls
        if fail_creations:
            replacement_calls += 1
            if replacement_calls == 2:
                raise RuntimeError("second replacement failed")
        return FakeMountService

    runtime = MountWorkerRuntime(provider, log_fn=lambda *_args: None)
    try:
        runtime.reconcile(two_mount_config(revision=1))
        original_workers = registry_workers(runtime)
        fail_creations = True

        with pytest.raises(RuntimeError, match="second replacement failed"):
            runtime.reconcile(two_mount_config(revision=2))

        assert registry_workers(runtime) == original_workers
        assert all(worker.running for worker in original_workers)
        assert len(mount_threads()) == 2
    finally:
        runtime.stop_all(timeout=1.0)

    assert mount_threads() == []


def test_invalid_factory_rolls_back_partial_creation():
    calls = 0

    def provider(_binding: MountBinding):
        nonlocal calls
        calls += 1
        return FakeMountService if calls == 1 else object()

    runtime = MountWorkerRuntime(provider, log_fn=lambda *_args: None)
    try:
        with pytest.raises(TypeError, match="factory must be callable"):
            runtime.reconcile(two_mount_config())

        assert runtime._registry == {}
        assert mount_threads() == []
    finally:
        runtime.stop_all(timeout=1.0)


def test_worker_start_failure_rolls_back_all_created_workers(monkeypatch):
    started: list[StubWorker] = []

    class StubWorker:
        def __init__(self, **_kwargs) -> None:
            self.running = False
            self.shutdown_calls = 0
            started.append(self)

        def start(self) -> None:
            if len(started) == 2:
                raise RuntimeError("second start failed")
            self.running = True

        def shutdown(self, timeout=None) -> None:
            self.shutdown_calls += 1
            self.running = False

    monkeypatch.setattr(runtime_module, "MountWorker", StubWorker)
    runtime = MountWorkerRuntime(working_provider, log_fn=lambda *_args: None)

    with pytest.raises(RuntimeError, match="second start failed"):
        runtime.reconcile(two_mount_config())

    assert runtime._registry == {}
    assert len(started) == 2
    assert [worker.shutdown_calls for worker in started] == [1, 1]
    assert not any(worker.running for worker in started)


def test_runtime_has_no_gps_update_path_and_unrelated_noop_changes_nothing():
    runtime = MountWorkerRuntime(working_provider, log_fn=lambda *_args: None)
    try:
        runtime.reconcile(two_mount_config())
        workers_before = registry_workers(runtime)
        threads_before = mount_threads()

        assert not hasattr(runtime, "gps_update")
        assert not hasattr(runtime, "update_gps")
        unrelated_gps_update = lambda _fix: None
        unrelated_gps_update({"latitude": 48.8566, "longitude": 2.3522})

        assert registry_workers(runtime) == workers_before
        assert mount_threads() == threads_before
        assert all(worker.running for worker in workers_before)
    finally:
        runtime.stop_all(timeout=1.0)

    assert mount_threads() == []

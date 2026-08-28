from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

from backend.mount_worker_runtime import MountBinding, MountWorkerRuntime


class FakeMountService:
    def __init__(self) -> None:
        self.close_calls = 0

    def status(self) -> str:
        return "ready"

    def close(self) -> None:
        self.close_calls += 1


class CapturingProvider:
    def __init__(self) -> None:
        self.bindings: list[MountBinding] = []
        self.services: list[FakeMountService] = []

    def __call__(self, binding: MountBinding) -> Callable[[], FakeMountService]:
        self.bindings.append(binding)

        def factory() -> FakeMountService:
            service = FakeMountService()
            self.services.append(service)
            return service

        return factory


def config_with_mount(
    mount: dict[str, Any] | None,
    *,
    enabled: bool = True,
    rig_id: int = 401,
) -> dict[str, Any]:
    return {
        "rigs": [
            {
                "rig_id": rig_id,
                "enabled": enabled,
                "devices": {"mount": mount},
            }
        ]
    }


def _entries(runtime: MountWorkerRuntime):
    return list(runtime._registry.values())


def _worker_threads(rig_id: int) -> list[threading.Thread]:
    name = f"mount-worker-r{rig_id}"
    return [thread for thread in threading.enumerate() if thread.name == name]


@pytest.mark.parametrize(
    ("mount", "enabled"),
    [
        ({"backend": "indi", "serial": "mount-disabled"}, False),
        (None, True),
        ({"backend": "none", "serial": "mount-none"}, True),
        ({"backend": "external", "serial": "mount-external"}, True),
    ],
    ids=[
        "disabled_rig_no_worker",
        "mount_null_no_worker",
        "backend_none_no_worker",
        "backend_external_no_worker",
    ],
)
def test_non_pilotable_mount_does_not_create_worker(mount, enabled):
    provider = CapturingProvider()
    runtime = MountWorkerRuntime(provider, log_fn=lambda *_args: None)

    runtime.reconcile(config_with_mount(mount, enabled=enabled))

    assert not _entries(runtime)
    assert provider.bindings == []


def test_enabled_valid_backend_and_serial_creates_one_worker():
    provider = CapturingProvider()
    runtime = MountWorkerRuntime(provider, log_fn=lambda *_args: None)
    try:
        runtime.reconcile(
            config_with_mount({"backend": "indi", "serial": "mount-serial"})
        )

        [entry] = _entries(runtime)
        assert entry.binding.identity == ("serial", "mount-serial")
        assert entry.worker.running
        assert len(_worker_threads(401)) == 1
        assert entry.worker.status() == "ready"
        assert len(provider.services) == 1
    finally:
        runtime.stop_all(timeout=1.0)

    assert not _worker_threads(401)


def test_fallback_identity_and_mount_entry_are_immutable_snapshots():
    provider = CapturingProvider()
    runtime = MountWorkerRuntime(provider, log_fn=lambda *_args: None)
    mount = {
        "backend": "indi",
        "fallback_physical_path": "pci-1/usb-2",
        "options": {"port": 7624},
    }
    config = config_with_mount(mount)
    try:
        runtime.reconcile(config)

        [binding] = provider.bindings
        assert binding.identity == ("fallback", "pci-1/usb-2")
        mount["fallback_physical_path"] = "changed"
        mount["options"]["port"] = 9999
        assert binding.identity == ("fallback", "pci-1/usb-2")
        assert binding.mount_entry["fallback_physical_path"] == "pci-1/usb-2"
        assert binding.mount_entry["options"]["port"] == 7624
        with pytest.raises(TypeError):
            binding.mount_entry["backend"] = "other"
    finally:
        runtime.stop_all(timeout=1.0)


def test_unchanged_reconcile_reuses_same_worker_object():
    provider = CapturingProvider()
    runtime = MountWorkerRuntime(provider, log_fn=lambda *_args: None)
    config = config_with_mount({"backend": "indi", "serial": "stable-mount"})
    try:
        runtime.reconcile(config)
        first = _entries(runtime)[0].worker
        first.status()

        runtime.reconcile(config)

        assert _entries(runtime)[0].worker is first
        assert first.running
        assert len(provider.bindings) == 1
        assert len(provider.services) == 1
    finally:
        runtime.stop_all(timeout=1.0)


@pytest.mark.parametrize(
    "replacement",
    [
        {"backend": "indi", "serial": "replace-me", "port": 7625},
        {"backend": "alpaca", "serial": "replace-me"},
    ],
    ids=["changing_binding", "changing_backend"],
)
def test_changing_binding_or_backend_replaces_worker(replacement):
    provider = CapturingProvider()
    runtime = MountWorkerRuntime(provider, log_fn=lambda *_args: None)
    original = {"backend": "indi", "serial": "replace-me"}
    try:
        runtime.reconcile(config_with_mount(original))
        old_worker = _entries(runtime)[0].worker
        old_worker.status()

        runtime.reconcile(config_with_mount(replacement))

        new_worker = _entries(runtime)[0].worker
        assert new_worker is not old_worker
        assert not old_worker.running
        assert new_worker.running
        new_worker.status()
        assert len(provider.services) == 2
        assert provider.services[0].close_calls == 1
    finally:
        runtime.stop_all(timeout=1.0)


def test_missing_identity_raises_and_preserves_existing_worker():
    provider = CapturingProvider()
    runtime = MountWorkerRuntime(provider, log_fn=lambda *_args: None)
    valid = config_with_mount(
        {"backend": "indi", "serial": "existing-mount"}, rig_id=402
    )
    invalid = {
        "rigs": valid["rigs"]
        + [
            {
                "rig_id": 403,
                "enabled": True,
                "devices": {"mount": {"backend": "indi"}},
            }
        ]
    }
    try:
        runtime.reconcile(valid)
        old_worker = _entries(runtime)[0].worker
        old_worker.status()

        with pytest.raises(ValueError, match="no stable device identity"):
            runtime.reconcile(invalid)

        assert _entries(runtime)[0].worker is old_worker
        assert old_worker.running
        assert len(provider.bindings) == 1
        assert len(provider.services) == 1
        assert provider.services[0].close_calls == 0
    finally:
        runtime.stop_all(timeout=1.0)


@pytest.mark.parametrize(
    "identity_fields",
    [
        {"serial": "duplicate-mount"},
        {"fallback_physical_path": "pci-1/usb-duplicate"},
    ],
    ids=["serial", "fallback"],
)
def test_duplicate_identity_is_rejected_atomically(identity_fields):
    provider = CapturingProvider()
    runtime = MountWorkerRuntime(provider, log_fn=lambda *_args: None)
    config = {
        "rigs": [
            {
                "rig_id": rig_id,
                "enabled": True,
                "devices": {"mount": {"backend": "indi", **identity_fields}},
            }
            for rig_id in (404, 405)
        ]
    }

    with pytest.raises(ValueError, match="duplicate mount identity"):
        runtime.reconcile(config)

    assert not _entries(runtime)
    assert provider.bindings == []
    assert provider.services == []
    assert not _worker_threads(404)
    assert not _worker_threads(405)


def test_provider_absent_raises_when_pilotable_mount_is_present():
    runtime = MountWorkerRuntime(log_fn=lambda *_args: None)

    with pytest.raises(RuntimeError, match="provider is not set"):
        runtime.reconcile(
            config_with_mount({"backend": "indi", "serial": "needs-provider"})
        )

    assert not _entries(runtime)


@pytest.mark.parametrize(
    "config",
    [
        config_with_mount(None),
        config_with_mount({"backend": "none"}),
        config_with_mount({"backend": "external"}),
    ],
)
def test_provider_absent_is_allowed_for_non_pilotable_config(config):
    runtime = MountWorkerRuntime(log_fn=lambda *_args: None)

    runtime.reconcile(config)

    assert not _entries(runtime)

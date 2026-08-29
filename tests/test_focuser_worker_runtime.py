from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import backend.focuser_worker_runtime as runtime_module
from backend.focuser_worker_runtime import FocuserBinding, FocuserWorkerRuntime


class FakeFocuserService:
    pass


class StubWorker:
    instances: list["StubWorker"] = []
    fail_start_rig_id: int | None = None

    def __init__(self, *, rig_id: int, service_factory, log_fn) -> None:
        self.rig_id = rig_id
        self.service_factory = service_factory
        self.log_fn = log_fn
        self.running = False
        self.shutdown_calls: list[float | None] = []
        self.__class__.instances.append(self)

    def start(self) -> None:
        if self.rig_id == self.fail_start_rig_id:
            raise RuntimeError("start failed")
        self.running = True

    def shutdown(self, timeout=None) -> None:
        self.shutdown_calls.append(timeout)
        self.running = False


@pytest.fixture(autouse=True)
def stub_workers(monkeypatch):
    StubWorker.instances = []
    StubWorker.fail_start_rig_id = None
    monkeypatch.setattr(runtime_module, "FocuserWorker", StubWorker)


def focuser_config(*entries: tuple[int, dict[str, Any], bool]) -> dict[str, Any]:
    return {
        "rigs": [
            {
                "rig_id": rig_id,
                "enabled": enabled,
                "devices": {"focuser": focuser},
            }
            for rig_id, focuser, enabled in entries
        ]
    }


def valid_config(*, revision: int | None = None) -> dict[str, Any]:
    entries = []
    for rig_id, serial in ((701, "focuser-a"), (702, "focuser-b")):
        focuser: dict[str, Any] = {"backend": "INDI", "serial": serial}
        if revision is not None:
            focuser["revision"] = revision
        entries.append((rig_id, focuser, True))
    return focuser_config(*entries)


class CapturingProvider:
    def __init__(self) -> None:
        self.bindings: list[FocuserBinding] = []

    def __call__(
        self, binding: FocuserBinding
    ) -> Callable[[], FakeFocuserService]:
        self.bindings.append(binding)
        return FakeFocuserService


def workers(runtime: FocuserWorkerRuntime) -> list[StubWorker]:
    return [entry.worker for entry in runtime._registry.values()]


def test_binding_is_normalized_and_recursively_immutable_snapshot():
    provider = CapturingProvider()
    runtime = FocuserWorkerRuntime(provider, log_fn=lambda *_args: None)
    focuser = {
        "backend": " INDI ",
        "fallback_physical_path": "usb-1/2",
        "options": {"limits": [1, 2]},
    }

    runtime.reconcile(focuser_config((701, focuser, True)))
    [binding] = provider.bindings
    focuser["fallback_physical_path"] = "changed"
    focuser["options"]["limits"].append(3)

    assert binding.backend == "indi"
    assert binding.identity == ("fallback", "usb-1/2")
    assert binding.focuser_entry["fallback_physical_path"] == "usb-1/2"
    assert binding.focuser_entry["options"]["limits"] == (1, 2)
    with pytest.raises(TypeError):
        binding.focuser_entry["backend"] = "other"


def test_duplicate_identity_raises_before_creating_workers():
    provider = CapturingProvider()
    runtime = FocuserWorkerRuntime(provider)
    duplicate = {"backend": "indi", "serial": "same-focuser"}

    with pytest.raises(ValueError, match="duplicate focuser identity"):
        runtime.reconcile(
            focuser_config((701, duplicate, True), (702, duplicate.copy(), True))
        )

    assert runtime._registry == {}
    assert provider.bindings == []
    assert StubWorker.instances == []


def test_missing_identity_raises_and_preserves_existing_worker():
    provider = CapturingProvider()
    runtime = FocuserWorkerRuntime(provider)
    runtime.reconcile(valid_config())
    original = workers(runtime)

    with pytest.raises(ValueError, match="no stable device identity"):
        runtime.reconcile(
            focuser_config(
                (701, {"backend": "indi", "serial": "focuser-a"}, True),
                (703, {"backend": "indi"}, True),
            )
        )

    assert workers(runtime) == original
    assert all(worker.running for worker in original)
    assert len(provider.bindings) == 2


def test_provider_is_required_only_for_eligible_focuser():
    runtime = FocuserWorkerRuntime()

    with pytest.raises(RuntimeError, match="provider is not set"):
        runtime.reconcile(
            focuser_config((701, {"backend": "indi", "serial": "eligible"}, True))
        )

    runtime.reconcile(
        focuser_config(
            (701, {"backend": "indi", "serial": "disabled"}, False),
            (702, {"backend": "none"}, True),
            (703, {"backend": "external"}, True),
        )
    )
    assert runtime._registry == {}


def test_get_for_rig_and_reconcile_reuse_replace_and_remove():
    provider = CapturingProvider()
    runtime = FocuserWorkerRuntime(provider)
    runtime.reconcile(valid_config(revision=1))
    first_701 = runtime.get_for_rig(701)
    first_702 = runtime.get_for_rig(702)

    runtime.reconcile(valid_config(revision=1))
    assert runtime.get_for_rig(701) is first_701
    assert runtime.get_for_rig(702) is first_702
    assert runtime.get_for_rig(999) is None

    runtime.reconcile(
        focuser_config(
            (701, {"backend": "indi", "serial": "focuser-a", "revision": 2}, True)
        )
    )

    replacement = runtime.get_for_rig(701)
    assert replacement is not first_701
    assert replacement.running
    assert not first_701.running
    assert not first_702.running
    assert runtime.get_for_rig(702) is None


@pytest.mark.parametrize("failure", ["provider", "factory", "start"])
def test_reconcile_failure_rolls_back_and_preserves_previous_registry(failure):
    fail = False
    calls = 0

    def provider(binding: FocuserBinding):
        nonlocal calls
        calls += 1
        if fail and failure == "provider" and binding.rig_id == 702:
            raise RuntimeError("provider failed")
        if fail and failure == "factory" and binding.rig_id == 702:
            return object()
        return FakeFocuserService

    runtime = FocuserWorkerRuntime(provider)
    runtime.reconcile(valid_config(revision=1))
    original = workers(runtime)
    fail = True
    if failure == "start":
        StubWorker.fail_start_rig_id = 702

    expected = TypeError if failure == "factory" else RuntimeError
    with pytest.raises(expected):
        runtime.reconcile(valid_config(revision=2))

    assert workers(runtime) == original
    assert all(worker.running for worker in original)
    created = StubWorker.instances[2:]
    assert created
    assert all(worker.shutdown_calls == [None] for worker in created)
    assert not any(worker.running for worker in created)


def test_stop_all_passes_timeout_and_clears_registry():
    runtime = FocuserWorkerRuntime(CapturingProvider())
    runtime.reconcile(valid_config())
    existing = workers(runtime)

    runtime.stop_all(timeout=0.25)

    assert runtime._registry == {}
    assert all(worker.shutdown_calls == [0.25] for worker in existing)


def test_singleton_accessor_sets_provider_once(monkeypatch):
    monkeypatch.setattr(runtime_module, "_focuser_worker_runtime", None)
    provider = CapturingProvider()

    runtime = runtime_module.get_focuser_worker_runtime(log_fn=lambda *_args: None)
    assert runtime_module.get_focuser_worker_runtime(provider) is runtime
    assert runtime_module.get_focuser_worker_runtime(provider) is runtime

    with pytest.raises(RuntimeError, match="already set"):
        runtime_module.get_focuser_worker_runtime(CapturingProvider())


def test_two_zwo_device_ids_create_two_distinct_workers():
    provider = CapturingProvider()
    runtime = FocuserWorkerRuntime(provider, log_fn=lambda *_args: None)

    runtime.reconcile(
        focuser_config(
            (
                701,
                {
                    "backend": "zwo_eaf",
                    "device_id": "zwo_eaf:0",
                },
                True,
            ),
            (
                702,
                {
                    "backend": "zwo_eaf",
                    "device_id": "zwo_eaf:7",
                },
                True,
            ),
        )
    )

    worker_0 = runtime.get_for_rig(701)
    worker_7 = runtime.get_for_rig(702)

    assert worker_0 is not None
    assert worker_7 is not None
    assert worker_0 is not worker_7

    assert len(provider.bindings) == 2
    assert {
        binding.identity
        for binding in provider.bindings
    } == {
        ("device_id", "zwo_eaf:0"),
        ("device_id", "zwo_eaf:7"),
    }

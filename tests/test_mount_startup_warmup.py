import sys
from types import ModuleType

import pytest


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


class FakeWorker:
    def __init__(self, result=True, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def warmup(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


class FakeRuntime:
    def __init__(self, workers):
        self.workers = workers
        self.config = None

    def reconcile(self, config):
        self.config = config

    def get_for_rig(self, rig_id):
        return self.workers.get(rig_id)


def test_startup_warmup_connects_all_configured_mount_workers(monkeypatch):
    config = {
        "schema_version": 2,
        "rigs": [
            {"rig_id": 1, "devices": {"mount": {"backend": "indi", "serial": "m1"}}},
            {"rig_id": 2, "devices": {"mount": {"backend": "indi", "serial": "m2"}}},
            {"rig_id": 3, "devices": {"mount": None}},
        ],
    }
    workers = {1: FakeWorker(), 2: FakeWorker()}
    runtime = FakeRuntime(workers)

    monkeypatch.setattr(flask_module, "load_rig_configuration", lambda: config)
    monkeypatch.setattr(
        flask_module,
        "get_mount_worker_runtime",
        lambda **_kwargs: runtime,
    )

    flask_module._warm_configured_mounts_at_startup()

    assert runtime.config == config
    assert workers[1].calls == 1
    assert workers[2].calls == 1


def test_startup_warmup_is_best_effort_per_mount(monkeypatch):
    config = {
        "schema_version": 2,
        "rigs": [
            {"rig_id": 1, "devices": {"mount": {"backend": "indi", "serial": "m1"}}},
            {"rig_id": 2, "devices": {"mount": {"backend": "indi", "serial": "m2"}}},
        ],
    }
    workers = {
        1: FakeWorker(error=RuntimeError("offline")),
        2: FakeWorker(),
    }
    runtime = FakeRuntime(workers)

    monkeypatch.setattr(flask_module, "load_rig_configuration", lambda: config)
    monkeypatch.setattr(
        flask_module,
        "get_mount_worker_runtime",
        lambda **_kwargs: runtime,
    )

    flask_module._warm_configured_mounts_at_startup()

    assert workers[1].calls == 1
    assert workers[2].calls == 1

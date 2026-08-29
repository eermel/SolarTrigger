from __future__ import annotations

from copy import deepcopy

from backend.camera_worker_runtime import CameraWorkerRuntime


class FakeWorker:
    def __init__(self, *, rig_id, clock, log_fn):
        self.rig_id = rig_id
        self.clock = clock
        self.log_fn = log_fn
        self.started = False
        self.stopped = False
        self.camera_entry = None

    def configure_camera(self, camera_entry):
        self.camera_entry = deepcopy(camera_entry)

    def start(self):
        self.started = True

    def stop(self, timeout=None):
        self.stopped = True


def config(camera, *, rig_id=1, enabled=False):
    return {
        "rigs": [
            {
                "rig_id": rig_id,
                "enabled": enabled,
                "devices": {
                    "camera": deepcopy(camera),
                },
            }
        ]
    }


def test_runtime_passes_configured_camera_identity_to_worker():
    workers = []

    def factory(**kwargs):
        worker = FakeWorker(**kwargs)
        workers.append(worker)
        return worker

    runtime = CameraWorkerRuntime(
        clock=object(),
        worker_factory=factory,
        log_fn=lambda *_args: None,
    )

    camera = {
        "backend": "sony",
        "manufacturer": "Sony Corporation",
        "model": "ILCE-7M5",
        "serial": "A7V-SERIAL",
    }

    runtime.reconcile(config(camera))

    worker = runtime.get_for_rig(1)

    assert worker is workers[0]
    assert worker.camera_entry == camera
    assert worker.started is True


def test_unchanged_camera_binding_reuses_same_worker():
    runtime = CameraWorkerRuntime(
        clock=object(),
        worker_factory=FakeWorker,
        log_fn=lambda *_args: None,
    )

    camera = {
        "backend": "sony",
        "model": "ILCE-7M5",
        "serial": "A7V-SERIAL",
    }
    cfg = config(camera)

    runtime.reconcile(cfg)
    first = runtime.get_for_rig(1)

    runtime.reconcile(cfg)
    second = runtime.get_for_rig(1)

    assert second is first
    assert first.started is True
    assert first.stopped is False


def test_changing_camera_serial_replaces_worker():
    workers = []

    def factory(**kwargs):
        worker = FakeWorker(**kwargs)
        workers.append(worker)
        return worker

    runtime = CameraWorkerRuntime(
        clock=object(),
        worker_factory=factory,
        log_fn=lambda *_args: None,
    )

    runtime.reconcile(
        config({
            "backend": "sony",
            "model": "ILCE-7M5",
            "serial": "A7V-SERIAL",
        })
    )
    old_worker = runtime.get_for_rig(1)

    runtime.reconcile(
        config({
            "backend": "sony",
            "model": "ILCE-6600",
            "serial": "A6600-SERIAL",
        })
    )
    new_worker = runtime.get_for_rig(1)

    assert new_worker is not old_worker
    assert old_worker.stopped is True
    assert new_worker.started is True
    assert new_worker.camera_entry["serial"] == "A6600-SERIAL"


def test_trigger_disabled_rig_camera_still_has_worker():
    runtime = CameraWorkerRuntime(
        clock=object(),
        worker_factory=FakeWorker,
        log_fn=lambda *_args: None,
    )

    runtime.reconcile(
        config(
            {
                "backend": "sony",
                "model": "ILCE-7M5",
                "serial": "A7V-SERIAL",
            },
            enabled=False,
        )
    )

    worker = runtime.get_for_rig(1)

    assert worker is not None
    assert worker.started is True

from backend.camera_worker_runtime import CameraWorkerRuntime


class FakeWorker:
    def __init__(self, *, rig_id, clock, log_fn):
        self.rig_id = rig_id
        self.clock = clock
        self.log_fn = log_fn
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


def _rig(rig_id, *, enabled=True, backend="gphoto2"):
    return {
        "rig_id": rig_id,
        "enabled": enabled,
        "devices": {"camera": {"backend": backend}},
    }


def test_reconcile_injects_the_owned_clock_into_every_worker():
    clock = object()
    workers = []

    def worker_factory(**kwargs):
        worker = FakeWorker(**kwargs)
        workers.append(worker)
        return worker

    runtime = CameraWorkerRuntime(clock=clock, worker_factory=worker_factory)

    runtime.reconcile({"rigs": [_rig(7), _rig(3)]})

    assert len(workers) == 2
    assert all(worker.clock is clock for worker in workers)


def test_active_camera_rig_ids_is_an_ascending_registry_snapshot():
    runtime = CameraWorkerRuntime(clock=object(), worker_factory=FakeWorker)
    runtime.reconcile(
        {
            "rigs": [
                _rig(9),
                _rig(2),
                _rig(5, enabled=False),
                _rig(6, backend="external"),
            ]
        }
    )

    snapshot = runtime.active_camera_rig_ids()

    assert snapshot == (2, 9)
    assert isinstance(snapshot, tuple)


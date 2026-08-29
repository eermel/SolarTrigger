import backend.camera_worker_runtime as camera_runtime_module
import backend.focuser_worker_runtime as focuser_runtime_module
import backend.mount_worker_runtime as mount_runtime_module


class FakeCameraWorker:
    def __init__(self, *, rig_id, clock, log_fn):
        self.rig_id = rig_id
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False


class FakeDeviceWorker:
    def __init__(self, *, rig_id, service_factory, log_fn):
        self.rig_id = rig_id
        self.service_factory = service_factory
        self.started = False

    def start(self):
        self.started = True

    def shutdown(self, timeout=None):
        self.started = False


def _neutral_service_factory_provider(_binding):
    return object


def test_reference_config_owns_five_independent_persistent_workers(monkeypatch):
    config = {
        "rigs": [
            {
                "rig_id": 1,
                "enabled": True,
                "devices": {
                    "camera": {"backend": "fake-camera"},
                    "mount": {"backend": "fake-mount", "serial": "mount-1"},
                    "focuser": {
                        "backend": "fake-focuser",
                        "serial": "focuser-1",
                    },
                },
            },
            {
                "rig_id": 2,
                "enabled": True,
                "devices": {
                    "camera": {"backend": "fake-camera"},
                    "mount": {"backend": "fake-mount", "serial": "mount-2"},
                },
            },
        ]
    }

    camera_singleton = camera_runtime_module.CameraWorkerRuntime(
        worker_factory=FakeCameraWorker,
        log_fn=lambda *_args: None,
    )
    monkeypatch.setattr(
        camera_runtime_module, "_camera_worker_runtime", camera_singleton
    )
    monkeypatch.setattr(mount_runtime_module, "_mount_worker_runtime", None)
    monkeypatch.setattr(focuser_runtime_module, "_focuser_worker_runtime", None)
    monkeypatch.setattr(mount_runtime_module, "MountWorker", FakeDeviceWorker)
    monkeypatch.setattr(focuser_runtime_module, "FocuserWorker", FakeDeviceWorker)

    camera_runtime = camera_runtime_module.get_camera_worker_runtime()
    mount_runtime = mount_runtime_module.get_mount_worker_runtime(
        _neutral_service_factory_provider
    )
    focuser_runtime = focuser_runtime_module.get_focuser_worker_runtime(
        _neutral_service_factory_provider
    )

    try:
        camera_runtime.reconcile(config)
        mount_runtime.reconcile(config)
        focuser_runtime.reconcile(config)

        camera_workers = tuple(
            camera_runtime.get_for_rig(rig_id) for rig_id in (1, 2)
        )
        mount_workers = tuple(
            mount_runtime.get_for_rig(rig_id) for rig_id in (1, 2)
        )
        focuser_worker = focuser_runtime.get_for_rig(1)
        workers = (*camera_workers, *mount_workers, focuser_worker)

        assert camera_runtime.active_camera_rig_ids() == (1, 2)
        assert focuser_runtime.get_for_rig(2) is None
        assert all(worker is not None and worker.started for worker in workers)
        assert len({id(worker) for worker in workers}) == 5

        camera_runtime.reconcile(config)
        mount_runtime.reconcile(config)
        focuser_runtime.reconcile(config)

        assert tuple(camera_runtime.get_for_rig(rig_id) for rig_id in (1, 2)) == (
            camera_workers
        )
        assert tuple(mount_runtime.get_for_rig(rig_id) for rig_id in (1, 2)) == (
            mount_workers
        )
        assert focuser_runtime.get_for_rig(1) is focuser_worker
    finally:
        camera_runtime.shutdown()
        mount_runtime.stop_all(timeout=1.0)
        focuser_runtime.stop_all(timeout=1.0)

    assert camera_runtime.active_camera_rig_ids() == ()
    assert all(not worker.started for worker in workers)

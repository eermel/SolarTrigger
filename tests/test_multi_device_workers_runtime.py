import backend.camera_worker_runtime as camera_runtime_module
import backend.focuser_worker_runtime as focuser_runtime_module
import backend.mount_worker_runtime as mount_runtime_module


def _service_factory_provider(_binding):
    return object


def test_five_workers_scenario_produces_expected_workers(monkeypatch):
    monkeypatch.setattr(camera_runtime_module, "_camera_worker_runtime", None)
    monkeypatch.setattr(mount_runtime_module, "_mount_worker_runtime", None)
    monkeypatch.setattr(focuser_runtime_module, "_focuser_worker_runtime", None)

    config = {
        "rigs": [
            {
                "rig_id": 1,
                "enabled": True,
                "devices": {
                    "camera": {"backend": "x"},
                    "mount": {"backend": "indi", "serial": "mount-1"},
                    "focuser": {"backend": "usb", "serial": "focuser-1"},
                },
            },
            {
                "rig_id": 2,
                "enabled": True,
                "devices": {
                    "camera": {"backend": "y"},
                    "mount": {"backend": "indi", "serial": "mount-2"},
                    "focuser": None,
                },
            },
        ]
    }

    camera_runtime = camera_runtime_module.get_camera_worker_runtime(
        log_fn=lambda *_args: None
    )
    mount_runtime = mount_runtime_module.get_mount_worker_runtime(
        _service_factory_provider, log_fn=lambda *_args: None
    )
    focuser_runtime = focuser_runtime_module.get_focuser_worker_runtime(
        _service_factory_provider, log_fn=lambda *_args: None
    )

    try:
        camera_runtime.reconcile(config)
        mount_runtime.reconcile(config)
        focuser_runtime.reconcile(config)

        assert len(camera_runtime.active_camera_rig_ids()) == 2
        assert mount_runtime.get_for_rig(1) is not None
        assert mount_runtime.get_for_rig(2) is not None
        assert focuser_runtime.get_for_rig(1) is not None
        assert focuser_runtime.get_for_rig(2) is None
    finally:
        camera_runtime.shutdown()
        mount_runtime.stop_all(timeout=1.0)
        focuser_runtime.stop_all(timeout=1.0)

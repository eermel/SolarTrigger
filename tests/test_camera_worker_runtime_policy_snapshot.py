from backend.camera_worker_runtime import CameraWorkerRuntime


class FakeWorker:
    def __init__(self, *, rig_id, clock, log_fn):
        self.rig_id = rig_id

    def start(self):
        pass

    def stop(self):
        pass


def test_reconcile_exposes_policy_snapshot_for_active_rig():
    runtime = CameraWorkerRuntime(clock=object(), worker_factory=FakeWorker)
    config = {
        "eclipse": {
            "reference_site": {
                "lat": 44.135,
                "lon": 4.81,
            }
        },
        "rigs": [
            {
                "rig_id": 7,
                "enabled": True,
                "devices": {
                    "camera": {
                        "backend": "gphoto2",
                        "manufacturer": "Canon",
                        "model": "EOS R5",
                        "alias": "wide-field",
                    }
                },
                "optics": {"focal_length_mm": 400},
                "photo": {
                    "atmos_enabled": False,
                    "anti_trailing_enabled": True,
                    "motion_tolerance_px": 1.5,
                    "iso_max": 3200,
                },
            }
        ]
    }

    runtime.reconcile(config)

    assert runtime.get_policy_config_for_rig(7) == {
        "eclipse": {
            "reference_site": {
                "lat": 44.135,
                "lon": 4.81,
            }
        },
        "devices": {
            "camera": {
                "backend": "gphoto2",
                "manufacturer": "Canon",
                "model": "EOS R5",
                "alias": "wide-field",
            },
            "mount": {
                "control": None,
                "geometry": None,
                "tracking": None,
            },
        },
        "optics": {"focal_length_mm": 400},
        "photo": {
            "atmos_enabled": False,
            "anti_trailing_enabled": True,
            "motion_tolerance_px": 1.5,
            "iso_compensation_enabled": True,
            "iso_max": 3200,
        },
    }


def test_policy_snapshot_preserves_explicit_iso_compensation_off():
    runtime = CameraWorkerRuntime(clock=object(), worker_factory=FakeWorker)
    config = {
        "rigs": [
            {
                "rig_id": 1,
                "enabled": True,
                "devices": {
                    "camera": {
                        "backend": "gphoto2",
                        "manufacturer": "Nikon",
                        "model": "D850",
                    }
                },
                "optics": {"focal_length_mm": 430},
                "photo": {
                    "anti_trailing_enabled": True,
                    "motion_tolerance_px": 1.0,
                    "iso_compensation_enabled": False,
                    "iso_max": 6400,
                },
            }
        ]
    }

    runtime.reconcile(config)

    assert (
        runtime.get_policy_config_for_rig(1)["photo"][
            "iso_compensation_enabled"
        ]
        is False
    )


def test_policy_snapshot_preserves_camera_backend():
    config = {
        "rigs": [
            {
                "rig_id": 1,
                "devices": {
                    "camera": {
                        "backend": "nikon-dslr",
                        "manufacturer": "Nikon",
                        "model": "Nikon DSC D850",
                        "alias": "D850",
                    },
                    "mount": None,
                },
                "optics": {"focal_length_mm": 800},
                "photo": {
                    "atmos_enabled": False,
                    "anti_trailing_enabled": True,
                    "motion_tolerance_px": 1.0,
                    "iso_compensation_enabled": True,
                    "iso_max": 6400,
                },
            }
        ],
        "eclipse": {
            "reference_site": {"lat": 24.0, "lon": 35.0},
        },
    }

    runtime = CameraWorkerRuntime(
        worker_factory=lambda **_kwargs: None,
    )
    runtime._registry = {1: object()}
    runtime._config = config

    snapshot = runtime.get_policy_config_for_rig(1)

    assert snapshot["devices"]["camera"]["backend"] == "nikon-dslr"

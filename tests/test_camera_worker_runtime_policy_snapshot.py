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
                    "anti_trailing_enabled": True,
                    "motion_tolerance_px": 1.5,
                    "iso_max": 3200,
                },
            }
        ]
    }

    runtime.reconcile(config)

    assert runtime.get_policy_config_for_rig(7) == {
        "devices": {
            "camera": {
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
            "anti_trailing_enabled": True,
            "motion_tolerance_px": 1.5,
            "iso_max": 3200,
        },
    }

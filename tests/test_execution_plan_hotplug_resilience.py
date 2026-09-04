from datetime import datetime, timedelta

from backend.execution_plan_runtime import ExecutionPlanRuntime


class FakeClock:
    def __init__(self, start):
        self.current = start

    def now(self):
        return self.current

    def remaining(self, target):
        return (target - self.current).total_seconds()

    def sleep(self, seconds):
        self.current += timedelta(seconds=seconds)


def _photo(when, index, shutter):
    return {
        "time": when,
        "rig_id": 1,
        "action": "PHOTO",
        "params": {
            "shutter": shutter,
            "expected_frames": 1,
        },
        "index": index,
    }


def test_photo_failure_does_not_stop_later_timeline_commands():
    start = datetime(2027, 8, 2, 10, 0, 0)
    clock = FakeClock(start)
    logs = []

    class Camera:
        def __init__(self):
            self.calls = []
            self.attempt = 0

        def execute_photo(self, rig_id, params):
            self.attempt += 1
            self.calls.append(
                (rig_id, params["shutter"])
            )
            if self.attempt == 1:
                raise RuntimeError("camera disconnected")

        def set_parameter(
            self,
            rig_id,
            parameter,
            value,
            *,
            fallback_parameter=None,
        ):
            raise AssertionError("unexpected SET")

    camera = Camera()

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=camera,
        log_fn=logs.append,
    )

    runtime.run({
        "_commands_runtime": [
            _photo(start + timedelta(seconds=1), 0, "1/500"),
            _photo(start + timedelta(seconds=2), 1, "1/250"),
        ]
    })

    assert camera.calls == [
        (1, "1/500"),
        (1, "1/250"),
    ]

    assert any(
        "photo_lost=1" in message
        and "index=0" in message
        for message in logs
    )


def test_failed_set_is_retried_before_photo_after_camera_returns():
    start = datetime(2027, 8, 2, 10, 0, 0)
    clock = FakeClock(start)
    logs = []

    class Camera:
        def __init__(self):
            self.set_attempts = 0
            self.calls = []

        def set_parameter(
            self,
            rig_id,
            parameter,
            value,
            *,
            fallback_parameter=None,
        ):
            self.set_attempts += 1
            self.calls.append(
                ("SET", parameter, value)
            )

            # SET programmé: caméra absente.
            # Première PHOTO: caméra encore absente.
            # Deuxième PHOTO: caméra revenue.
            if self.set_attempts <= 2:
                raise RuntimeError("camera absent")

        def execute_photo(self, rig_id, params):
            self.calls.append(
                ("PHOTO", params["shutter"])
            )

    camera = Camera()

    plan = {
        "_commands_runtime": [
            {
                "time": start + timedelta(seconds=1),
                "rig_id": 1,
                "action": "SET",
                "params": {
                    "parameter": "iso",
                    "value": "800",
                },
                "index": 0,
            },
            _photo(start + timedelta(seconds=2), 1, "1/250"),
            _photo(start + timedelta(seconds=3), 2, "1/125"),
        ]
    }

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=camera,
        log_fn=logs.append,
    )

    runtime.run(plan)

    # SET initial échoué + retry avant PHOTO1 échoué + retry avant PHOTO2 OK.
    assert camera.calls == [
        ("SET", "iso", "800"),
        ("SET", "iso", "800"),
        ("SET", "iso", "800"),
        ("PHOTO", "1/125"),
    ]

    assert any(
        "photo_lost=1" in message
        and "reason=pending_set" in message
        and "index=1" in message
        for message in logs
    )

    assert any(
        "pending_set_applied parameter=iso" in message
        for message in logs
    )

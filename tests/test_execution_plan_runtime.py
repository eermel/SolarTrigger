from datetime import datetime, timedelta

import pytest

from backend.execution_plan_runtime import (
    ExecutionPlanError,
    ExecutionPlanRuntime,
    load_execution_plan,
)


class FakeClock:
    def __init__(self, start):
        self.current = start

    def now(self):
        return self.current

    def remaining(self, target):
        return (target - self.current).total_seconds()

    def sleep(self, seconds):
        self.current += timedelta(seconds=seconds)


class FakeCamera:
    def __init__(self):
        self.calls = []

    def set_parameter(
        self,
        rig_id,
        parameter,
        value,
        *,
        fallback_parameter=None,
    ):
        self.calls.append(
            (
                "SET",
                rig_id,
                parameter,
                value,
                fallback_parameter,
            )
        )

    def execute_photo(self, rig_id, params):
        self.calls.append(("PHOTO", rig_id, dict(params)))


def test_load_execution_plan_requires_schema_v2(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(
        '{"schema_version":1,"config_type":"execution_plan","commands":[]}'
    )

    with pytest.raises(ExecutionPlanError):
        load_execution_plan(path)


def test_runtime_executes_set_and_photo():
    clock = FakeClock(datetime(2027, 8, 2, 10, 0, 0))
    camera = FakeCamera()

    plan = {
        "_commands_runtime": [
            {
                "time": datetime(2027, 8, 2, 10, 0, 1),
                "rig_id": 1,
                "action": "SET",
                "params": {
                    "parameter": "iso",
                    "value": "100",
                },
                "index": 0,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 2),
                "rig_id": 1,
                "action": "PHOTO",
                "params": {
                    "shutter": "1/500",
                    "expected_frames": 1,
                },
                "index": 1,
            },
        ]
    }

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=camera,
        log_fn=lambda _message: None,
    )

    runtime.run(plan)

    assert camera.calls == [
        ("SET", 1, "iso", "100", None),
        (
            "PHOTO",
            1,
            {
                "shutter": "1/500",
                "expected_frames": 1,
            },
        ),
    ]


def test_runtime_skips_past_commands():
    clock = FakeClock(datetime(2027, 8, 2, 10, 0, 5))
    camera = FakeCamera()

    plan = {
        "_commands_runtime": [
            {
                "time": datetime(2027, 8, 2, 10, 0, 1),
                "rig_id": 1,
                "action": "PHOTO",
                "params": {
                    "shutter": "1/500",
                    "expected_frames": 1,
                },
                "index": 0,
            }
        ]
    }

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=camera,
        log_fn=lambda _message: None,
    )

    runtime.run(plan)

    assert camera.calls == []


def test_rigs_have_independent_execution_threads():
    clock = FakeClock(datetime(2027, 8, 2, 10, 0, 0))
    camera = FakeCamera()

    plan = {
        "_commands_runtime": [
            {
                "time": datetime(2027, 8, 2, 10, 0, 1),
                "rig_id": 1,
                "action": "PHOTO",
                "params": {"shutter": "1/250", "expected_frames": 1},
                "index": 0,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 1),
                "rig_id": 2,
                "action": "PHOTO",
                "params": {"shutter": "1/500", "expected_frames": 1},
                "index": 1,
            },
        ]
    }

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=camera,
        log_fn=lambda _message: None,
    )

    runtime.run(plan)

    assert sorted(call[1] for call in camera.calls) == [1, 2]


def test_blocking_photo_on_one_rig_does_not_block_other_rig():
    import threading
    import time

    class RealClock:
        def __init__(self):
            self.start_wall = datetime.now()
            self.start_mono = time.monotonic()

        def now(self):
            return self.start_wall + timedelta(
                seconds=time.monotonic() - self.start_mono
            )

        def remaining(self, target):
            return (target - self.now()).total_seconds()

        def sleep(self, seconds):
            if seconds > 0:
                time.sleep(seconds)

    class BlockingCamera:
        def __init__(self):
            self.calls = []
            self.lock = threading.Lock()

        def set_parameter(
            self,
            rig_id,
            parameter,
            value,
            *,
            fallback_parameter=None,
        ):
            with self.lock:
                self.calls.append(
                    ("SET", rig_id, time.monotonic())
                )

        def execute_photo(self, rig_id, params):
            with self.lock:
                self.calls.append(
                    ("PHOTO_START", rig_id, time.monotonic())
                )

            if rig_id == 1:
                time.sleep(0.20)

            with self.lock:
                self.calls.append(
                    ("PHOTO_END", rig_id, time.monotonic())
                )

    clock = RealClock()
    camera = BlockingCamera()

    base = clock.now()

    plan = {
        "_commands_runtime": [
            {
                "time": base + timedelta(seconds=0.05),
                "rig_id": 1,
                "action": "PHOTO",
                "params": {
                    "shutter": "1/250",
                    "expected_frames": 1,
                },
                "index": 0,
            },
            {
                "time": base + timedelta(seconds=0.10),
                "rig_id": 2,
                "action": "PHOTO",
                "params": {
                    "shutter": "1/500",
                    "expected_frames": 1,
                },
                "index": 1,
            },
        ]
    }

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=camera,
        log_fn=lambda _message: None,
    )

    runtime.run(plan)

    starts = {
        rig_id: ts
        for kind, rig_id, ts in camera.calls
        if kind == "PHOTO_START"
    }

    assert 1 in starts
    assert 2 in starts

    # RIG2 must start while RIG1 is still blocked in its PHOTO.
    assert starts[2] - starts[1] < 0.15


def test_apply_initial_state_uses_direct_set_parameter_calls():
    clock = FakeClock(datetime(2027, 8, 2, 10, 0, 0))
    camera = FakeCamera()

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=camera,
        log_fn=lambda _message: None,
    )

    runtime.apply_initial_state({
        "initial_state_required": {
            "1": {
                "iso": "100",
                "capturemode": "Single Shot",
            },
            "2": {
                "iso": "200",
                "shutterspeed2": "1/1000",
            },
        }
    })

    assert camera.calls == [
        ("SET", 1, "iso", "100", None),
        ("SET", 1, "capturemode", "Single Shot", None),
        ("SET", 2, "iso", "200", None),
        ("SET", 2, "shutterspeed2", "1/1000", None),
    ]


def test_apply_initial_state_rejects_invalid_shape():
    runtime = ExecutionPlanRuntime(
        clock=FakeClock(datetime(2027, 8, 2, 10, 0, 0)),
        camera_client=FakeCamera(),
        log_fn=lambda _message: None,
    )

    with pytest.raises(ExecutionPlanError):
        runtime.apply_initial_state({
            "initial_state_required": {
                "1": ["iso", "100"],
            }
        })


def test_apply_initial_state_rejects_invalid_rig_id():
    runtime = ExecutionPlanRuntime(
        clock=FakeClock(datetime(2027, 8, 2, 10, 0, 0)),
        camera_client=FakeCamera(),
        log_fn=lambda _message: None,
    )

    with pytest.raises(ExecutionPlanError):
        runtime.apply_initial_state({
            "initial_state_required": {
                "RIG1": {"iso": "100"},
            }
        })


def test_rebase_execution_plan_preserves_all_command_offsets():
    from backend.execution_plan_runtime import rebase_execution_plan

    path_start = datetime(2027, 8, 2, 10, 0, 0)

    plan = {
        "sequence_start_utc": "2027-08-02T10:00:00.000Z",
        "sequence_end_utc": "2027-08-02T10:10:00.000Z",
        "commands": [
            {
                "time_utc": "2027-08-02T10:00:05.000Z",
                "rig_id": 1,
                "action": "SET",
                "params": {"parameter": "iso", "value": "100"},
            },
            {
                "time_utc": "2027-08-02T10:00:07.250Z",
                "rig_id": 2,
                "action": "PHOTO",
                "params": {"shutter": "1/500", "expected_frames": 1},
            },
        ],
        "_commands_runtime": [
            {
                "time": datetime(2027, 8, 2, 10, 0, 5),
                "rig_id": 1,
                "action": "SET",
                "params": {"parameter": "iso", "value": "100"},
                "index": 0,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 7, 250000),
                "rig_id": 2,
                "action": "PHOTO",
                "params": {"shutter": "1/500", "expected_frames": 1},
                "index": 1,
            },
        ],
    }

    rebased = rebase_execution_plan(
        plan,
        datetime(2030, 1, 1, 12, 0, 0),
    )

    commands = rebased["_commands_runtime"]

    assert commands[0]["time"] == datetime(
        2030, 1, 1, 12, 0, 5
    )
    assert commands[1]["time"] == datetime(
        2030, 1, 1, 12, 0, 7, 250000
    )

    assert (
        commands[1]["time"] - commands[0]["time"]
    ).total_seconds() == 2.25

    assert rebased["sequence_start_utc"] == (
        "2030-01-01T12:00:00.000Z"
    )
    assert rebased["sequence_end_utc"] == (
        "2030-01-01T12:10:00.000Z"
    )

    assert rebased["commands"][0]["time_utc"] == (
        "2030-01-01T12:00:05.000Z"
    )
    assert rebased["commands"][1]["time_utc"] == (
        "2030-01-01T12:00:07.250Z"
    )


def test_rebase_execution_plan_does_not_mutate_original():
    from backend.execution_plan_runtime import rebase_execution_plan

    original_time = datetime(2027, 8, 2, 10, 0, 5)

    plan = {
        "sequence_start_utc": "2027-08-02T10:00:00.000Z",
        "sequence_end_utc": "2027-08-02T10:10:00.000Z",
        "commands": [
            {
                "time_utc": "2027-08-02T10:00:05.000Z",
                "rig_id": 1,
                "action": "PHOTO",
                "params": {},
            }
        ],
        "_commands_runtime": [
            {
                "time": original_time,
                "rig_id": 1,
                "action": "PHOTO",
                "params": {},
                "index": 0,
            }
        ],
    }

    rebase_execution_plan(
        plan,
        datetime(2030, 1, 1, 12, 0, 0),
    )

    assert plan["_commands_runtime"][0]["time"] == original_time
    assert plan["sequence_start_utc"] == (
        "2027-08-02T10:00:00.000Z"
    )
    assert plan["commands"][0]["time_utc"] == (
        "2027-08-02T10:00:05.000Z"
    )


def test_prepare_for_execution_replays_past_sets_but_never_past_photos():
    clock = FakeClock(datetime(2027, 8, 2, 10, 0, 5))
    camera = FakeCamera()

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=camera,
        log_fn=lambda _message: None,
    )

    plan = {
        "initial_state_required": {
            "1": {
                "iso": "100",
            }
        },
        "_commands_runtime": [
            {
                "time": datetime(2027, 8, 2, 10, 0, 1),
                "rig_id": 1,
                "action": "SET",
                "params": {
                    "parameter": "shutterspeed",
                    "value": "1/250",
                },
                "index": 0,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 2),
                "rig_id": 1,
                "action": "PHOTO",
                "params": {
                    "shutter": "1/250",
                    "expected_frames": 1,
                },
                "index": 1,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 3),
                "rig_id": 1,
                "action": "SET",
                "params": {
                    "parameter": "iso",
                    "value": "200",
                },
                "index": 2,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 10),
                "rig_id": 1,
                "action": "SET",
                "params": {
                    "parameter": "iso",
                    "value": "400",
                },
                "index": 3,
            },
        ],
    }

    runtime.prepare_for_execution(plan)

    assert camera.calls == [
        ("SET", 1, "iso", "100", None),
        ("SET", 1, "shutterspeed", "1/250", None),
        ("SET", 1, "iso", "200", None),
    ]


def test_runtime_logs_dispatch_lateness_and_summary():
    start = datetime(2027, 8, 2, 10, 0, 0)
    clock = FakeClock(start)
    camera = FakeCamera()
    logs = []

    plan = {
        "_commands_runtime": [
            {
                "index": 0,
                "rig_id": 1,
                "action": "SET",
                "time": start + timedelta(seconds=1),
                "params": {
                    "parameter": "iso",
                    "value": 100,
                },
            },
            {
                "index": 1,
                "rig_id": 1,
                "action": "PHOTO",
                "time": start + timedelta(seconds=2),
                "params": {},
            },
        ]
    }

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=camera,
        log_fn=logs.append,
    )

    runtime.run(plan)

    dispatch_logs = [
        line
        for line in logs
        if line.startswith("EXECUTION_PLAN rig=")
    ]

    assert len(dispatch_logs) == 2

    assert "scheduled=2027-08-02T10:00:01Z" in dispatch_logs[0]
    assert "dispatch=2027-08-02T10:00:01Z" in dispatch_logs[0]
    assert "lateness_ms=+0.000" in dispatch_logs[0]

    assert "scheduled=2027-08-02T10:00:02Z" in dispatch_logs[1]
    assert "dispatch=2027-08-02T10:00:02Z" in dispatch_logs[1]
    assert "lateness_ms=+0.000" in dispatch_logs[1]

    timing_logs = [
        line
        for line in logs
        if line.startswith("EXECUTION_PLAN TIMING ")
    ]

    assert timing_logs == [
        "EXECUTION_PLAN TIMING "
        "count=2 "
        "mean_ms=+0.000 "
        "p95_ms=+0.000 "
        "max_ms=+0.000"
    ]

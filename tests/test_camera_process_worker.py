from __future__ import annotations

import time

import pytest

import backend.camera_process_worker as camera_process_worker
from backend.camera_process_worker import ProcessCameraWorker
from backend.generic_worker import (
    WorkerTimeoutError,
    WorkerUnavailableError,
)


def _fake_camera_child(
    conn,
    rig_id,
    camera_entry,
    clock_spec,
    call_timeout_s,
):
    conn.send({"kind": "ready"})

    while True:
        request = conn.recv()
        operation = request.get("operation")

        if operation == "__stop__":
            return

        if operation == "read_info":
            conn.send(
                {
                    "kind": "result",
                    "value": {
                        "rig_id": rig_id,
                        "model": camera_entry.get("model"),
                    },
                }
            )
            continue

        if operation == "test_photo_fast":
            # Deliberately never answer.
            while True:
                time.sleep(1)

        conn.send(
            {
                "kind": "error",
                "class": "RuntimeError",
                "code": None,
                "message": f"unsupported fake operation: {operation}",
            }
        )


def _worker(timeout=0.10):
    worker = ProcessCameraWorker(
        rig_id=1,
        call_timeout_s=timeout,
        process_target=_fake_camera_child,
        log_fn=lambda _message: None,
    )
    worker.configure_camera(
        {
            "backend": "gphoto2",
            "model": "FAKE",
        }
    )
    worker.start()
    return worker


def test_process_worker_returns_normal_result():
    worker = _worker()
    try:
        result = worker.read_info()
        assert result == {
            "rig_id": 1,
            "model": "FAKE",
        }
        assert worker.generation == 1
    finally:
        worker.stop()


def test_hung_camera_process_is_killed_and_command_is_not_retried():
    worker = _worker(timeout=0.05)
    try:
        generation = worker.generation

        before = time.monotonic()
        with pytest.raises(WorkerTimeoutError):
            worker.test_photo_fast("1/500")
        elapsed = time.monotonic() - before

        assert elapsed < 3.0
        assert not worker.running
        assert worker.generation == generation

        # Recovery happens only for the NEXT command.
        result = worker.read_info()
        assert result["model"] == "FAKE"
        assert worker.generation == generation + 1
    finally:
        worker.stop()


def test_stop_kills_a_child_that_cannot_reply():
    worker = _worker(timeout=5.0)

    # Replace the normal command path with a direct request that makes the fake
    # child hang, then verify stop itself remains bounded.
    conn = worker._conn
    assert conn is not None
    conn.send(
        {
            "operation": "test_photo_fast",
            "args": ("1/500",),
            "kwargs": {},
        }
    )

    time.sleep(0.05)

    before = time.monotonic()
    stopped = worker.stop(timeout=0.05)
    elapsed = time.monotonic() - before

    assert stopped is True
    assert elapsed < 2.0
    assert not worker.running


def test_stopped_process_worker_rejects_commands():
    worker = _worker()
    worker.stop()

    with pytest.raises(WorkerUnavailableError):
        worker.read_info()


def test_camera_bootstrap_arms_parent_death_before_target(monkeypatch):
    events = []

    monkeypatch.setattr(
        camera_process_worker,
        "arm_parent_death_signal",
        lambda parent_pid: events.append(("arm", parent_pid)),
    )

    def target(conn, rig_id, camera_entry, clock_spec, call_timeout_s):
        events.append(
            (
                "target",
                conn,
                rig_id,
                camera_entry,
                clock_spec,
                call_timeout_s,
            )
        )

    marker = object()
    camera_process_worker._camera_process_bootstrap(
        target,
        marker,
        3,
        {"backend": "fake"},
        {"clock": "spec"},
        12.5,
        4321,
    )

    assert events == [
        ("arm", 4321),
        (
            "target",
            marker,
            3,
            {"backend": "fake"},
            {"clock": "spec"},
            12.5,
        ),
    ]


def test_camera_bootstrap_never_touches_target_if_parent_admission_fails(
    monkeypatch,
):
    called = []

    def reject(_parent_pid):
        raise RuntimeError("parent disappeared")

    monkeypatch.setattr(
        camera_process_worker,
        "arm_parent_death_signal",
        reject,
    )

    with pytest.raises(RuntimeError, match="parent disappeared"):
        camera_process_worker._camera_process_bootstrap(
            lambda *_args: called.append(True),
            object(),
            1,
            {"backend": "fake"},
            None,
            1.0,
            4321,
        )

    assert called == []

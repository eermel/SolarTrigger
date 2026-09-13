from __future__ import annotations

import time

import pytest

from backend.focuser_process_worker import ProcessFocuserWorker
from backend.generic_worker import WorkerTimeoutError
from backend.mount_process_worker import ProcessMountWorker
from plugins.mount.indi_client import IndiClientError


def _fake_device_child(conn, spec, call_timeout_s):
    conn.send({"kind": "ready"})

    while True:
        request = conn.recv()
        operation = request.get("operation")

        if operation == "__shutdown__":
            return

        if operation == "status":
            conn.send(
                {
                    "kind": "result",
                    "value": {
                        "rig_id": spec["rig_id"],
                        "backend": spec["backend"],
                    },
                }
            )
            continue

        if operation in {"start_slew", "move_to"}:
            while True:
                time.sleep(1)

        if operation == "warmup":
            conn.send(
                {
                    "kind": "error",
                    "module": "plugins.mount.indi_client",
                    "class": "IndiClientError",
                    "code": "INDI_UNAVAILABLE",
                    "message": "INDI is unavailable",
                    "command": ["indi_getprop"],
                    "returncode": 1,
                    "stderr": "failed",
                }
            )
            continue

        conn.send(
            {
                "kind": "result",
                "value": {"operation": operation},
            }
        )


def _mount(timeout=0.05):
    worker = ProcessMountWorker(
        rig_id=1,
        backend="indi",
        device_config={"serial": "mount-1"},
        state_path="/tmp/process-worker-test-state.json",
        call_timeout_s=timeout,
        log_fn=lambda _message: None,
        process_target=_fake_device_child,
    )
    worker.start()
    return worker


def _focuser(timeout=0.05):
    worker = ProcessFocuserWorker(
        rig_id=2,
        backend="zwo_eaf",
        device_config={"serial": "focus-2"},
        state_path="/tmp/process-worker-test-state.json",
        call_timeout_s=timeout,
        log_fn=lambda _message: None,
        process_target=_fake_device_child,
    )
    worker.start()
    return worker


def test_mount_process_returns_normal_result():
    worker = _mount()

    try:
        assert worker.status() == {
            "rig_id": 1,
            "backend": "indi",
        }
        assert worker.generation == 1
    finally:
        worker.shutdown()


def test_focuser_process_returns_normal_result():
    worker = _focuser()

    try:
        assert worker.status() == {
            "rig_id": 2,
            "backend": "zwo_eaf",
        }
        assert worker.generation == 1
    finally:
        worker.shutdown()


def test_hung_mount_is_killed_and_next_command_respawns():
    worker = _mount()

    try:
        generation = worker.generation
        started = time.monotonic()

        with pytest.raises(WorkerTimeoutError):
            worker.start_slew("east")

        assert time.monotonic() - started < 3.0
        assert not worker.running
        assert worker.generation == generation

        assert worker.status()["backend"] == "indi"
        assert worker.generation == generation + 1
    finally:
        worker.shutdown()


def test_hung_focuser_is_killed_and_next_command_respawns():
    worker = _focuser()

    try:
        generation = worker.generation
        started = time.monotonic()

        with pytest.raises(WorkerTimeoutError):
            worker.move_to(12345)

        assert time.monotonic() - started < 3.0
        assert not worker.running
        assert worker.generation == generation

        assert worker.status()["backend"] == "zwo_eaf"
        assert worker.generation == generation + 1
    finally:
        worker.shutdown()


def test_mount_indi_error_contract_is_preserved():
    worker = _mount()

    try:
        with pytest.raises(IndiClientError) as caught:
            worker.warmup()

        assert caught.value.code == "INDI_UNAVAILABLE"
        assert caught.value.command == ["indi_getprop"]
        assert caught.value.returncode == 1
        assert caught.value.stderr == "failed"
    finally:
        worker.shutdown()


@pytest.mark.parametrize("factory", [_mount, _focuser])
def test_shutdown_is_bounded_for_hung_child(factory):
    worker = factory(timeout=5.0)

    conn = worker._conn
    assert conn is not None

    operation = (
        "start_slew"
        if worker.device_kind == "mount"
        else "move_to"
    )
    args = (
        ("east",)
        if worker.device_kind == "mount"
        else (12345,)
    )

    conn.send(
        {
            "operation": operation,
            "args": args,
            "kwargs": {},
        }
    )

    time.sleep(0.05)
    started = time.monotonic()

    assert worker.shutdown(timeout=0.05) is True
    assert time.monotonic() - started < 2.0
    assert not worker.running

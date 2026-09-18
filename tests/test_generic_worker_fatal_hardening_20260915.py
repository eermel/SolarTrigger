import threading

import pytest

from backend.generic_worker import (
    GenericWorker,
    WorkerUnavailableError,
)


def test_normal_job_exception_does_not_poison_worker():
    worker = GenericWorker(
        rig_id=201,
        device_kind="normal-exception-test",
        log_fn=lambda _msg: None,
    )
    worker.start()
    try:
        failed = worker.submit(
            lambda: (_ for _ in ()).throw(ValueError("ordinary failure"))
        )
        with pytest.raises(ValueError, match="ordinary failure"):
            failed.result(timeout=1.0)

        assert worker.healthy is True
        assert worker.submit(lambda: 42).result(timeout=1.0) == 42
    finally:
        worker.stop(timeout=1.0)


def test_system_exit_marks_worker_unhealthy_and_future_resolves():
    worker = GenericWorker(
        rig_id=202,
        device_kind="fatal-exception-test",
        log_fn=lambda _msg: None,
    )
    worker.start()

    failed = worker.submit(
        lambda: (_ for _ in ()).throw(SystemExit("fatal worker exit"))
    )

    with pytest.raises(SystemExit, match="fatal worker exit"):
        failed.result(timeout=1.0)

    deadline = threading.Event()
    # running/healthy state is changed by the same worker thread immediately
    # after completing the Future. A tiny bounded poll avoids a scheduling
    # race in the assertion without sleeping for a fixed long interval.
    import time
    until = time.monotonic() + 1.0
    while worker.healthy and time.monotonic() < until:
        time.sleep(0.001)

    assert worker.healthy is False

    # unhealthy is published before the worker thread has necessarily completed
    # its finally cleanup. Wait independently for thread termination.
    until = time.monotonic() + 1.0
    while worker.running and time.monotonic() < until:
        time.sleep(0.001)

    assert worker.running is False
    assert "worker thread crashed: SystemExit" in (worker.unhealthy_reason or "")

    with pytest.raises(WorkerUnavailableError):
        worker.submit(lambda: "must not run")

    assert worker.stop(timeout=1.0) is True


def test_keyboard_interrupt_also_fails_closed():
    worker = GenericWorker(
        rig_id=203,
        device_kind="keyboard-interrupt-test",
        log_fn=lambda _msg: None,
    )
    worker.start()

    failed = worker.submit(
        lambda: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    with pytest.raises(KeyboardInterrupt):
        failed.result(timeout=1.0)

    import time
    until = time.monotonic() + 1.0
    while worker.healthy and time.monotonic() < until:
        time.sleep(0.001)

    assert worker.healthy is False

    # unhealthy is published before the worker thread has necessarily completed
    # its finally cleanup. Wait independently for thread termination, just as
    # for the SystemExit fatal path above.
    until = time.monotonic() + 1.0
    while worker.running and time.monotonic() < until:
        time.sleep(0.001)

    assert worker.running is False
    assert worker.stop(timeout=1.0) is True

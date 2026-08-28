from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from backend.generic_worker import GenericWorker


def _worker_threads(name: str) -> list[threading.Thread]:
    return [thread for thread in threading.enumerate() if thread.name == name]


def test_start_stop_calls_device_close_once_and_no_thread_left():
    close_calls = []
    worker = GenericWorker(
        rig_id=101,
        device_kind="lifecycle-test",
        device_close=lambda: close_calls.append(time.monotonic()),
    )
    thread_name = "lifecycle-test-worker-r101"
    assert not _worker_threads(thread_name)

    worker.start()
    assert worker.running
    assert len(_worker_threads(thread_name)) == 1

    worker.stop(timeout=1.0)
    worker.stop(timeout=1.0)

    assert close_calls and len(close_calls) == 1
    assert not worker.running
    assert not _worker_threads(thread_name)


def test_submit_job_result_and_exception_and_last_error():
    worker = GenericWorker(rig_id=102, device_kind="result-test", log_fn=lambda _: None)
    worker.start()
    try:
        assert worker.submit(lambda left, right: left + right, 20, 22).result(1.0) == 42

        before = datetime.now(timezone.utc)

        def fail():
            raise ValueError("deliberate worker failure")

        failed = worker.submit(fail)
        with pytest.raises(ValueError, match="deliberate worker failure"):
            failed.result(1.0)
        after = datetime.now(timezone.utc)

        error = worker.last_error
        assert error is not None
        assert error["rig_id"] == 102
        assert error["device_kind"] == "result-test"
        assert "deliberate worker failure" in error["message"]
        recorded_at = datetime.fromisoformat(error["when"])
        assert before <= recorded_at <= after
    finally:
        worker.stop(timeout=1.0)


def test_two_workers_do_not_block_each_other():
    w1 = GenericWorker(rig_id=103, device_kind="concurrency-long")
    w2 = GenericWorker(rig_id=104, device_kind="concurrency-short")
    w1.start()
    w2.start()
    try:
        long_started = threading.Event()

        def long_job():
            long_started.set()
            time.sleep(0.5)
            return "long"

        long_future = w1.submit(long_job)
        assert long_started.wait(1.0)

        started_at = time.monotonic()
        short_future = w2.submit(lambda: (time.sleep(0.05), "short")[1])
        assert short_future.result(0.2) == "short"
        assert time.monotonic() - started_at < 0.2
        assert not long_future.done()
        assert long_future.result(1.0) == "long"
    finally:
        w1.stop(timeout=1.0)
        w2.stop(timeout=1.0)


def test_shutdown_policy_cancel_pending():
    worker = GenericWorker(
        rig_id=105,
        device_kind="cancel-test",
        shutdown_policy="cancel_pending",
    )
    in_flight_started = threading.Event()
    release_in_flight = threading.Event()

    def in_flight_job():
        in_flight_started.set()
        assert release_in_flight.wait(1.0)
        return "completed"

    worker.start()
    in_flight = worker.submit(in_flight_job)
    assert in_flight_started.wait(1.0)
    queued = [worker.submit(lambda: "must not run") for _ in range(3)]

    stopper = threading.Thread(target=worker.stop, kwargs={"timeout": 1.0})
    stopper.start()
    try:
        deadline = time.monotonic() + 0.5
        while not all(future.cancelled() for future in queued):
            assert time.monotonic() < deadline
            time.sleep(0.005)
    finally:
        release_in_flight.set()
        stopper.join(1.0)

    assert not stopper.is_alive()
    assert in_flight.result(0.1) == "completed"
    assert all(future.cancelled() for future in queued)
    assert not worker.running
    assert not _worker_threads("cancel-test-worker-r105")


def test_shutdown_policy_drain():
    worker = GenericWorker(
        rig_id=106,
        device_kind="drain-test",
        shutdown_policy="drain",
    )
    executed = []
    worker.start()
    futures = [
        worker.submit(lambda value=value: executed.append(value) or value)
        for value in range(4)
    ]

    worker.stop(timeout=1.0)

    assert [future.result(0.1) for future in futures] == list(range(4))
    assert executed == list(range(4))
    assert not worker.running
    assert not _worker_threads("drain-test-worker-r106")

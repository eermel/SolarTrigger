from __future__ import annotations

import threading

import pytest

from backend.generic_worker import (
    BusyDeviceError,
    GenericWorker,
    PRIORITY_DIAGNOSTIC,
    PRIORITY_MANUAL,
    PRIORITY_SEQUENCER,
    PRIORITY_STOP,
)


def test_priority_ordering():
    worker = GenericWorker(rig_id=201, device_kind="priority-order")
    executed = []
    futures = [
        worker.submit(lambda: executed.append("manual-1")),
        worker.submit(lambda: executed.append("manual-2")),
        worker.submit_with_priority(
            PRIORITY_SEQUENCER,
            lambda: executed.append("sequencer"),
        ),
    ]

    worker.start()
    try:
        for future in futures:
            future.result(1.0)
    finally:
        worker.stop(timeout=1.0)

    assert executed == ["sequencer", "manual-1", "manual-2"]


def test_stop_preempts_queued():
    worker = GenericWorker(rig_id=202, device_kind="priority-stop")
    in_flight_started = threading.Event()
    release_in_flight = threading.Event()
    dequeued_priorities = []
    original_get = worker._queue.get

    def recording_get(*args, **kwargs):
        item = original_get(*args, **kwargs)
        dequeued_priorities.append(item[0])
        return item

    worker._queue.get = recording_get

    def in_flight_job():
        in_flight_started.set()
        assert release_in_flight.wait(1.0)

    worker.start()
    in_flight = worker.submit(in_flight_job)
    assert in_flight_started.wait(1.0)
    queued = [worker.submit(lambda: None) for _ in range(2)]

    stopper = threading.Thread(target=worker.stop, kwargs={"timeout": 1.0})
    stopper.start()
    try:
        release_in_flight.set()
        stopper.join(1.0)
    finally:
        release_in_flight.set()
        if stopper.is_alive():
            stopper.join(1.0)

    assert not stopper.is_alive()
    in_flight.result(0.1)
    for future in queued:
        future.result(0.1)
    assert dequeued_priorities == [
        PRIORITY_MANUAL,
        PRIORITY_STOP,
        PRIORITY_MANUAL,
        PRIORITY_MANUAL,
    ]


def test_diagnostic_reject_if_busy():
    worker = GenericWorker(rig_id=203, device_kind="priority-diagnostic")
    in_flight_started = threading.Event()
    release_in_flight = threading.Event()

    def long_manual_job():
        in_flight_started.set()
        assert release_in_flight.wait(1.0)

    worker.start()
    in_flight = worker.submit(long_manual_job)
    assert in_flight_started.wait(1.0)
    try:
        with pytest.raises(BusyDeviceError):
            worker.submit_with_priority(
                PRIORITY_DIAGNOSTIC,
                lambda: None,
                reject_if_busy=True,
            )
    finally:
        release_in_flight.set()
        worker.stop(timeout=1.0)

    in_flight.result(0.1)

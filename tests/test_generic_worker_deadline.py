from __future__ import annotations

import time

import pytest

from backend.generic_worker import ExpiredJobError, GenericWorker


@pytest.mark.parametrize(
    ("purpose", "logged_purpose"),
    [("eclipse-capture", "eclipse-capture"), (None, "unspecified")],
)
def test_expired_job_is_skipped_and_logged_without_changing_last_error(
    purpose, logged_purpose
):
    logs = []
    calls = []
    worker = GenericWorker(rig_id=301, device_kind="deadline-test", log_fn=logs.append)

    def must_not_run():
        calls.append("called")

    future = worker.submit(
        must_not_run,
        worker_deadline=time.monotonic() - 1.0,
        purpose=purpose,
    )
    worker.start()
    try:
        with pytest.raises(ExpiredJobError) as raised:
            future.result(1.0)

        assert raised.value.code == "EXPIRED"
        assert calls == []
        assert logs == [
            f"deadline-test worker for rig 301 skipped expired job: {logged_purpose}"
        ]
        assert worker.last_error is None
    finally:
        worker.stop(timeout=1.0)


def test_job_without_deadline_executes_normally():
    worker = GenericWorker(rig_id=302, device_kind="no-deadline-test")
    worker.start()
    try:
        assert worker.submit(lambda value: value * 2, 21).result(1.0) == 42
    finally:
        worker.stop(timeout=1.0)


def test_normal_job_error_is_recorded_and_is_not_expired():
    worker = GenericWorker(
        rig_id=303,
        device_kind="job-error-test",
        log_fn=lambda _message: None,
    )

    def fail():
        raise ValueError("ordinary job failure")

    worker.start()
    try:
        future = worker.submit(fail)
        with pytest.raises(ValueError, match="ordinary job failure") as raised:
            future.result(1.0)

        assert not isinstance(raised.value, ExpiredJobError)
        assert worker.last_error is not None
        assert worker.last_error["message"] == "ordinary job failure"
    finally:
        worker.stop(timeout=1.0)

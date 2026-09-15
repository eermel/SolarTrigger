import pytest

from backend.camera_process_worker import ProcessCameraWorker
from backend.generic_worker import WorkerUnavailableError
from services.camera_service import PreparedCapture


class _Proc:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class _Conn:
    pass


def _prepared():
    return PreparedCapture(
        token="child-token",
        estimated_total_s=0.1,
        exposures_s=[0.01],
        planned_count=1,
        plugin_name="test",
    )


def _worker():
    worker = ProcessCameraWorker(
        rig_id=1,
        process_target=lambda *_args, **_kwargs: None,
    )
    worker._started = True
    worker._camera_entry = {"manufacturer": "test", "model": "test"}
    return worker


def test_trigger_prepared_rejects_dead_origin_generation_before_respawn(monkeypatch):
    worker = _worker()
    worker._generation = 4
    worker._process = _Proc(alive=False)
    worker._conn = _Conn()

    prepared = _prepared()
    prepared._process_worker_generation = 4

    ensure_calls = []
    monkeypatch.setattr(
        worker,
        "_ensure_process_locked",
        lambda: ensure_calls.append("ensure"),
    )

    with pytest.raises(
        WorkerUnavailableError,
        match="previous camera worker generation",
    ):
        worker.trigger_prepared(prepared)

    # Critical invariant: an old token must not cause a new child generation
    # to be spawned just so it can reject that token.
    assert ensure_calls == []
    assert worker._generation == 4


def test_trigger_prepared_rejects_generation_mismatch_without_respawn(monkeypatch):
    worker = _worker()
    worker._generation = 5
    worker._process = _Proc(alive=True)
    worker._conn = _Conn()

    prepared = _prepared()
    prepared._process_worker_generation = 4

    ensure_calls = []
    monkeypatch.setattr(
        worker,
        "_ensure_process_locked",
        lambda: ensure_calls.append("ensure"),
    )

    with pytest.raises(
        WorkerUnavailableError,
        match="previous camera worker generation",
    ):
        worker.trigger_prepared(prepared)

    assert ensure_calls == []


def test_legacy_prepared_without_generation_uses_normal_remote_path(monkeypatch):
    worker = _worker()
    prepared = _prepared()

    calls = []

    def fake_remote(operation, *args, **kwargs):
        calls.append((operation, args, kwargs))
        return "ok"

    monkeypatch.setattr(worker, "_remote_call", fake_remote)

    assert worker.trigger_prepared(prepared, deadline=None) == "ok"
    assert calls == [
        ("trigger_prepared", (prepared,), {"deadline": None}),
    ]


def test_prepare_capture_stamps_generation_atomically(monkeypatch):
    worker = _worker()
    worker._generation = 9
    prepared = _prepared()

    def fake_remote(operation, *args, **kwargs):
        assert operation == "prepare_capture"
        # The explicit prepare_capture method holds worker._lock around this
        # call and the generation annotation.
        assert worker._lock._is_owned()
        return prepared

    monkeypatch.setattr(worker, "_remote_call", fake_remote)

    result = worker.prepare_capture("intent")

    assert result is prepared
    assert result._process_worker_generation == 9

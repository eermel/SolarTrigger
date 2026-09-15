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


def _prepared(generation=None):
    prepared = PreparedCapture(
        token="child-token",
        estimated_total_s=0.1,
        exposures_s=[0.01],
        planned_count=1,
        plugin_name="test",
    )
    if generation is not None:
        prepared._process_worker_generation = generation
    return prepared


def _worker():
    worker = ProcessCameraWorker(
        rig_id=1,
        process_target=lambda *_args, **_kwargs: None,
    )
    worker._started = True
    worker._camera_entry = {"manufacturer": "test", "model": "test"}
    return worker


def test_discard_prepared_dead_origin_generation_does_not_respawn(monkeypatch):
    worker = _worker()
    worker._generation = 4
    worker._process = _Proc(alive=False)
    worker._conn = _Conn()

    prepared = _prepared(4)

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
        worker.discard_prepared(prepared)

    assert ensure_calls == []
    assert worker._generation == 4


def test_discard_prepared_generation_mismatch_does_not_touch_new_child(monkeypatch):
    worker = _worker()
    worker._generation = 5
    new_process = _Proc(alive=True)
    worker._process = new_process
    worker._conn = _Conn()

    prepared = _prepared(4)

    remote_sends = []
    monkeypatch.setattr(
        worker._conn,
        "send",
        lambda payload: remote_sends.append(payload),
        raising=False,
    )

    with pytest.raises(
        WorkerUnavailableError,
        match="previous camera worker generation",
    ):
        worker.discard_prepared(prepared)

    assert remote_sends == []
    assert worker._process is new_process
    assert new_process.is_alive() is True
    assert worker._generation == 5


def test_discard_prepared_same_generation_uses_expected_generation(monkeypatch):
    worker = _worker()
    prepared = _prepared(7)

    calls = []

    def fake_remote(operation, *args, **kwargs):
        calls.append((operation, args, kwargs))
        return True

    monkeypatch.setattr(worker, "_remote_call", fake_remote)

    assert worker.discard_prepared(prepared) is True
    assert calls == [
        (
            "discard_prepared",
            (prepared,),
            {"_expected_generation": 7},
        )
    ]


def test_legacy_discard_without_generation_keeps_compatibility(monkeypatch):
    worker = _worker()
    prepared = _prepared()

    calls = []

    def fake_remote(operation, *args, **kwargs):
        calls.append((operation, args, kwargs))
        return True

    monkeypatch.setattr(worker, "_remote_call", fake_remote)

    assert worker.discard_prepared(prepared) is True
    assert calls == [
        ("discard_prepared", (prepared,), {})
    ]

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.camera_worker import CameraWorker
from backend.trigger_runtime import RuntimeClock
from scripts.camera_ipc_client import CameraIpcClient


class _FakeTime:
    def __init__(self):
        self.mono = 100.0
        self.wall = datetime(2027, 8, 2, 9, 0, tzinfo=timezone.utc)

    def monotonic(self):
        return self.mono

    def wall_clock(self):
        return self.wall


def test_ipc_deadline_uses_trigger_runtime_clock_not_wall_clock(monkeypatch):
    fake = _FakeTime()
    clock = RuntimeClock(
        wall_clock_fn=fake.wall_clock,
        monotonic_fn=fake.monotonic,
        sleep_fn=lambda _seconds: None,
    )
    clock.configure()

    client = CameraIpcClient("/tmp/unused.sock", "session", clock=clock)
    deadline = datetime(2027, 8, 2, 9, 0, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "scripts.camera_ipc_client.time.monotonic",
        fake.monotonic,
    )

    first = client._deadline_monotonic(
        deadline,
        "trigger_prepared",
        rig_id=1,
    )
    assert first == pytest.approx(130.0)

    fake.wall += timedelta(hours=6)
    fake.mono += 5.0

    second = client._deadline_monotonic(
        deadline,
        "trigger_prepared",
        rig_id=1,
    )
    assert second == pytest.approx(130.0)


def test_effective_timeout_uses_only_monotonic_deadline(monkeypatch):
    monkeypatch.setattr(
        "scripts.camera_ipc_client.time.monotonic",
        lambda: 200.0,
    )
    assert CameraIpcClient._effective_timeout(
        120.0,
        deadline_monotonic=212.5,
    ) == pytest.approx(
        12.5 + CameraIpcClient.DEADLINE_RESPONSE_GRACE_S
    )


def test_effective_timeout_rejects_expired_monotonic_deadline(monkeypatch):
    monkeypatch.setattr(
        "scripts.camera_ipc_client.time.monotonic",
        lambda: 200.0,
    )
    with pytest.raises(TimeoutError):
        CameraIpcClient._effective_timeout(
            120.0,
            deadline_monotonic=199.0,
        )


def test_camera_worker_does_not_recompute_supplied_monotonic_deadline():
    worker = object.__new__(CameraWorker)

    def fail_if_called(_deadline):
        raise AssertionError("UTC deadline must not be converted again")

    worker._capture_deadline = fail_if_called
    captured = {}

    def fake_call(method_name, *args, **kwargs):
        captured["method_name"] = method_name
        captured["kwargs"] = kwargs
        return "ok"

    worker._call = fake_call

    result = worker.trigger_prepared(
        object(),
        deadline=datetime(2027, 8, 2, 9, 0, tzinfo=timezone.utc),
        monotonic_deadline=321.5,
    )

    assert result == "ok"
    assert captured["method_name"] == "trigger_prepared"
    assert captured["kwargs"]["monotonic_deadline"] == pytest.approx(321.5)
    assert captured["kwargs"]["worker_deadline"] == pytest.approx(321.5)


def test_main_trigger_injects_anchored_clock_into_ipc_client():
    source = Path("scripts/eclipse_trigger.py").read_text(encoding="utf-8")
    assert "CameraIpcClient(" in source
    assert "socket_path, session" in source
    assert "clock=clock" in source

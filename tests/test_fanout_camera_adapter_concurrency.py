from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from scripts.fanout_camera_adapter import FanoutCameraAdapter
from services.camera_service import PreparedCapture


class CoordinatedRigClient:
    """IPC fake which keeps Sony slow without using wall-clock delays."""

    def __init__(self, rig_count: int) -> None:
        self.rig_ids = tuple(range(1, rig_count + 1))
        self.brands = {
            rig_id: "Sony" if rig_id == 1 else "Nikon" for rig_id in self.rig_ids
        }
        self.entered_barrier = threading.Barrier(rig_count + 1)
        self.allow_completion = threading.Event()
        self.allow_sony_completion = threading.Event()
        self.fast_completed = {
            rig_id: threading.Event()
            for rig_id in self.rig_ids
            if self.brands[rig_id] == "Nikon"
        }
        self.calls: list[tuple[str, str, int]] = []
        self._calls_lock = threading.Lock()

    def list_active_camera_rigs(self) -> dict[str, tuple[int, ...]]:
        return {"rig_ids": self.rig_ids}

    def prepare_capture(self, rig_id: int, _intent: Any) -> dict[str, Any]:
        self._coordinate("prepare", rig_id)
        return {
            "token_id": f"prepared-{rig_id}",
            "estimated_total_s": 1.0,
            "exposures_s": [1.0],
            "planned_count": 1,
        }

    def trigger_prepared(
        self, rig_id: int, _token_id: str, *, deadline: Any = None
    ) -> dict[str, int]:
        self._coordinate("trigger", rig_id)
        return {"frames": 1, "planned": 1}

    def shoot_speed_list(
        self,
        rig_id: int,
        _speeds: list[str],
        *,
        photo_num_start: int = 0,
        deadline: Any = None,
        slowest_override_seconds: float | None = None,
    ) -> dict[str, int]:
        self._coordinate("shoot", rig_id)
        return {"frames": 1, "planned": 1}

    def _coordinate(self, operation: str, rig_id: int) -> None:
        brand = self.brands[rig_id]
        self._record("entered", operation, rig_id)
        self.entered_barrier.wait(timeout=5.0)
        assert self.allow_completion.wait(timeout=5.0)
        if brand == "Sony":
            assert self.allow_sony_completion.wait(timeout=5.0)
        self._record("completed", operation, rig_id)
        if brand == "Nikon":
            self.fast_completed[rig_id].set()

    def _record(self, state: str, operation: str, rig_id: int) -> None:
        with self._calls_lock:
            self.calls.append((state, operation, rig_id))


class MaterializedRigClient:
    def list_active_camera_rigs(self) -> dict[str, tuple[int, int]]:
        return {"rig_ids": (1, 2)}

    def prepare_capture(self, rig_id: int, _intent: Any) -> dict[str, Any]:
        exposures_by_rig = {1: [0.25, 0.5], 2: [1.0, 2.0]}
        return {
            "token_id": f"prepared-{rig_id}",
            "plugin_name": f"camera-{rig_id}",
            "exposures_s": exposures_by_rig[rig_id],
            "request_id": "logical-request-1",
        }


def _invoke_prepare(adapter: FanoutCameraAdapter, rig_count: int) -> Any:
    return adapter.prepare_capture(object())


def _invoke_trigger(adapter: FanoutCameraAdapter, rig_count: int) -> Any:
    prepared = PreparedCapture(
        token=tuple(
            SimpleNamespace(rig_id=rig_id, token_id=f"prepared-{rig_id}")
            for rig_id in range(1, rig_count + 1)
        ),
        estimated_total_s=1.0,
        exposures_s=[1.0],
        planned_count=1,
        plugin_name="fanout",
    )
    return adapter.trigger_prepared(prepared)


def _invoke_shoot(adapter: FanoutCameraAdapter, rig_count: int) -> Any:
    return adapter.shoot_speed_list(["1/100"])


def test_prepare_capture_materializes_each_successful_rig() -> None:
    adapter = FanoutCameraAdapter(
        MaterializedRigClient(), log_fn=lambda _message: None
    )

    try:
        prepared = adapter.prepare_capture(object())
    finally:
        adapter.close()

    assert prepared.materialized is not None
    assert len(prepared.materialized) == 2
    assert [
        (
            exposure.rig_id,
            exposure.plugin_name,
            exposure.exposures_s,
            exposure.logical_request_id,
        )
        for exposure in prepared.materialized
    ] == [
        (1, "camera-1", [0.25, 0.5], "logical-request-1"),
        (2, "camera-2", [1.0, 2.0], "logical-request-1"),
    ]


@pytest.mark.parametrize("rig_count", range(1, 5))
@pytest.mark.parametrize(
    ("operation", "invoke"),
    [
        ("prepare", _invoke_prepare),
        ("trigger", _invoke_trigger),
        ("shoot", _invoke_shoot),
    ],
)
def test_fanout_submits_every_rig_before_waiting_for_slow_sony(
    rig_count: int,
    operation: str,
    invoke: Callable[[FanoutCameraAdapter, int], Any],
) -> None:
    client = CoordinatedRigClient(rig_count)
    adapter = FanoutCameraAdapter(client, log_fn=lambda _message: None)
    outcome: dict[str, Any] = {}

    def call_adapter() -> None:
        try:
            outcome["result"] = invoke(adapter, rig_count)
        except BaseException as exc:
            outcome["error"] = exc

    caller = threading.Thread(target=call_adapter, daemon=True)
    caller.start()

    try:
        client.entered_barrier.wait(timeout=5.0)
        assert set(client.calls) == {
            ("entered", operation, rig_id) for rig_id in range(1, rig_count + 1)
        }
        assert not any(state == "completed" for state, _, _ in client.calls)

        client.allow_completion.set()
        for completed in client.fast_completed.values():
            assert completed.wait(timeout=5.0)

        assert caller.is_alive(), "the slow Sony rig must still be outstanding"
        assert all(
            ("completed", operation, rig_id) in client.calls
            for rig_id in client.fast_completed
        )

        client.allow_sony_completion.set()
        caller.join(timeout=5.0)
        assert not caller.is_alive()
        assert "error" not in outcome
        assert "result" in outcome
    finally:
        client.allow_completion.set()
        client.allow_sony_completion.set()
        caller.join(timeout=5.0)
        adapter.close()

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from scripts.fanout_camera_adapter import FanoutCameraAdapter


class FakeIpcClient:
    def list_active_camera_rigs(self) -> dict[str, tuple[int, int]]:
        return {"rig_ids": (1, 2)}

    def prepare_capture(self, rig_id: int, _intent: Any) -> dict[str, Any]:
        return {
            1: {
                "token_id": "token-rig-1",
                "estimated_total_s": 0.5,
                "exposures_s": [0.01],
                "planned_count": 1,
                "plugin_name": "nikon",
                "request_id": "REQ-XYZ",
            },
            2: {
                "token_id": "token-rig-2",
                "estimated_total_s": 1.5,
                "exposures_s": [0.5, 1.0],
                "planned_count": 2,
                "plugin_name": "sony",
                "request_id": "REQ-XYZ",
            },
        }[rig_id]


def test_prepare_capture_materializes_distinct_plans_with_traceability() -> None:
    adapter = FanoutCameraAdapter(FakeIpcClient(), log_fn=lambda _message: None)
    intent = SimpleNamespace(request_id="REQ-XYZ")

    try:
        prepared = adapter.prepare_capture(intent)
    finally:
        adapter.close()

    assert prepared.materialized is not None
    assert len(prepared.materialized) == 2
    assert [
        (
            entry.rig_id,
            entry.plugin_name,
            entry.exposures_s,
            entry.logical_request_id,
        )
        for entry in prepared.materialized
    ] == [
        (1, "nikon", [0.01], "REQ-XYZ"),
        (2, "sony", [0.5, 1.0], "REQ-XYZ"),
    ]

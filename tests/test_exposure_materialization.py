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
                "iso_applied": "ISO 320",
                "corrections": ["rig-1 shutter correction"],
                "warnings": ["rig-1 warning"],
            },
            2: {
                "token_id": "token-rig-2",
                "estimated_total_s": 1.5,
                "exposures_s": [0.5, 1.0],
                "planned_count": 2,
                "plugin_name": "sony",
                "request_id": "REQ-XYZ",
                "iso_applied": "ISO 640",
                "corrections": ["rig-2 ISO correction", "rig-2 shutter correction"],
                "warnings": [],
            },
        }[rig_id]


class FakeSingleRigIpcClient:
    def list_active_camera_rigs(self) -> dict[str, tuple[int]]:
        return {"rig_ids": (1,)}

    def prepare_capture(self, rig_id: int, _intent: Any) -> dict[str, Any]:
        assert rig_id == 1
        return {
            "token_id": "token-rig-1",
            "estimated_total_s": 1.2,
            "exposures_s": [0.4, 0.8],
            "planned_count": 2,
            "plugin_name": "nikon",
            "request_id": "REQ-1",
        }


class FakeMaterializationDetailsIpcClient:
    def list_active_camera_rigs(self) -> dict[str, tuple[int]]:
        return {"rig_ids": (1,)}

    def prepare_capture(self, rig_id: int, _intent: Any) -> dict[str, Any]:
        assert rig_id == 1
        return {
            "token_id": "token-rig-1",
            "plugin_name": "nikon",
            "iso_applied": "ISO 400",
            "corrections": ["shutter rounded to 1/125"],
            "warnings": ["requested ISO unavailable"],
        }


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


def test_prepare_capture_propagates_per_rig_materialization_details() -> None:
    adapter = FanoutCameraAdapter(FakeIpcClient(), log_fn=lambda _message: None)

    try:
        prepared = adapter.prepare_capture(SimpleNamespace(request_id="REQ-XYZ"))
    finally:
        adapter.close()

    assert prepared.materialized is not None
    assert [
        (entry.rig_id, entry.iso_applied, entry.corrections, entry.warnings)
        for entry in prepared.materialized
    ] == [
        (1, "ISO 320", ["rig-1 shutter correction"], ["rig-1 warning"]),
        (
            2,
            "ISO 640",
            ["rig-2 ISO correction", "rig-2 shutter correction"],
            [],
        ),
    ]


def test_prepare_capture_single_rig_matches_representative_materialization() -> None:
    adapter = FanoutCameraAdapter(
        FakeSingleRigIpcClient(), log_fn=lambda _message: None
    )
    intent = SimpleNamespace(request_id="REQ-1")

    try:
        prepared = adapter.prepare_capture(intent)
    finally:
        adapter.close()

    assert prepared.exposures_s == [0.4, 0.8]
    assert prepared.materialized is not None
    assert len(prepared.materialized) == 1
    materialized = prepared.materialized[0]
    assert (
        materialized.rig_id,
        materialized.plugin_name,
        materialized.exposures_s,
        materialized.logical_request_id,
    ) == (1, "nikon", [0.4, 0.8], "REQ-1")


def test_prepare_capture_surfaces_materialization_details() -> None:
    adapter = FanoutCameraAdapter(
        FakeMaterializationDetailsIpcClient(), log_fn=lambda _message: None
    )

    try:
        prepared = adapter.prepare_capture(SimpleNamespace())
    finally:
        adapter.close()

    assert prepared.materialized is not None
    assert len(prepared.materialized) == 1
    materialized = prepared.materialized[0]
    assert materialized.iso_applied == "ISO 400"
    assert materialized.corrections == ["shutter rounded to 1/125"]
    assert materialized.warnings == ["requested ISO unavailable"]

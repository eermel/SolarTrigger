from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from scripts.fanout_camera_adapter import FanoutCameraAdapter


@dataclass(frozen=True)
class Intent:
    results: tuple[str, ...]
    origin: str


class RecordingIpcClient:
    def __init__(self) -> None:
        self.received_intents: dict[int, Intent] = {}

    def list_active_camera_rigs(self) -> dict[str, tuple[int, int]]:
        return {"rig_ids": (1, 2)}

    def prepare_capture(self, rig_id: int, intent: Intent) -> dict[str, Any]:
        self.received_intents[rig_id] = intent
        return {"token_id": f"prepared-{rig_id}"}


def test_prepare_capture_applies_atmos_transform_only_to_enabled_rig() -> None:
    client = RecordingIpcClient()
    original = Intent(results=("original",), origin="scheduler")

    def transform(rig_id: int, intent: Intent) -> tuple[Intent, bool]:
        assert rig_id == 1
        return replace(intent, results=(*intent.results, "atmos")), True

    adapter = FanoutCameraAdapter(
        client,
        log_fn=lambda _message: None,
        atmos_enabled_by_rig={1: True, 2: False},
        atmos_intent_transformer=transform,
    )

    try:
        adapter.prepare_capture(original)
    finally:
        adapter.close()

    assert client.received_intents[1] == Intent(
        results=("original", "atmos"), origin="atmos"
    )
    assert client.received_intents[2] == original
    assert client.received_intents[1] is not original
    assert client.received_intents[2] is not original
    assert original == Intent(results=("original",), origin="scheduler")

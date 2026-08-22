import sys
import types
from datetime import datetime, timezone

import pytest

from backend.gps_controller import GpsController
from backend.state_store import StateStore


@pytest.mark.parametrize(
    ("timezonefinder_module", "expected_name"),
    [
        (
            types.SimpleNamespace(
                TimezoneFinder=lambda: types.SimpleNamespace(
                    timezone_at=lambda **_: "Europe/Paris"
                )
            ),
            "Europe/Paris",
        ),
        (None, None),
    ],
)
def test_run_stores_timezone_fields_on_success(
    tmp_path, monkeypatch, timezonefinder_module, expected_name
):
    gps_time = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    position = types.SimpleNamespace(
        latitude=48.8566,
        longitude=2.3522,
        altitude_m=35.0,
        satellites=8,
        hdop=0.9,
    )
    snapshot = types.SimpleNamespace(position=position, gps_time=gps_time)

    class FakeGpsService:
        @classmethod
        def from_config(cls, config, *, log_fn):
            return cls()

        def initialize(self, timeout_s, *, require_gga):
            return snapshot

    monkeypatch.setattr("services.gps_service.GpsService", FakeGpsService)
    monkeypatch.setitem(sys.modules, "timezonefinder", timezonefinder_module)

    config_file = tmp_path / "gps.json"
    config_file.write_text("{}", encoding="utf-8")
    state = StateStore(tmp_path / "state.json")
    emitted = []
    controller = GpsController(
        state,
        config_file,
        timezone_fn=lambda *_args, **_kwargs: 2.0,
        time_sync_fn=lambda *_args, **_kwargs: True,
        log_fn=lambda *_args: None,
        emit_fn=lambda event, payload: emitted.append((event, payload)),
    )

    controller._run(timeout_s=1.0)

    gps = state.snapshot("gps")
    assert gps["timezone_name"] == expected_name
    assert gps["utc_offset_minutes"] == 120
    assert gps["timezone"] == "UTC+2"
    gps_update = next(payload for event, payload in emitted if event == "gps_update")
    assert gps_update["timezone_name"] == expected_name
    assert gps_update["utc_offset_minutes"] == 120
    assert gps_update["timezone"] == "UTC+2"

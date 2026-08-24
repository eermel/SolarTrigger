import sys
import types
from datetime import datetime, timezone

import pytest

from backend.gps_controller import GpsController
from backend.state_store import StateStore


GPS_TIME = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
GPS_POSITION = types.SimpleNamespace(
    latitude=48.8566123,
    longitude=2.3522345,
    altitude_m=35.04,
    satellites=8,
    hdop=0.876,
)


def _controller(tmp_path, monkeypatch, initial_gps, *, time_sync_fn, timezone_fn):
    snapshot = types.SimpleNamespace(position=GPS_POSITION, gps_time=GPS_TIME)

    class FakeGpsService:
        @classmethod
        def from_config(cls, config, *, log_fn):
            return cls()

        def initialize(self, timeout_s, *, require_gga):
            assert timeout_s == 1.0
            assert require_gga is True
            return snapshot

    monkeypatch.setattr("services.gps_service.GpsService", FakeGpsService)
    monkeypatch.setitem(
        sys.modules,
        "timezonefinder",
        types.SimpleNamespace(
            TimezoneFinder=lambda: types.SimpleNamespace(
                timezone_at=lambda **_: "Europe/Paris"
            )
        ),
    )
    config_file = tmp_path / "gps.json"
    config_file.write_text("{}", encoding="utf-8")
    state = StateStore(tmp_path / "state.json")
    state.update_section("gps", initial_gps)
    emitted = []
    controller = GpsController(
        state,
        config_file,
        timezone_fn=timezone_fn,
        time_sync_fn=time_sync_fn,
        log_fn=lambda *_args: None,
        emit_fn=lambda event, payload: emitted.append((event, payload)),
    )
    return controller, state, emitted


def _events(emitted, event_name):
    return [payload for event, payload in emitted if event == event_name]


def test_run_time_only_changes_only_time_fields(tmp_path, monkeypatch):
    initial = {
        "connected": True,
        "synced": False,
        "sync_time": "old-sync-time",
        "lat": 1.0,
        "lon": 2.0,
        "alt": 3.0,
        "satellites": 4,
        "hdop": 5.0,
        "date": "2020-01-02",
        "timezone": "UTC-3",
        "timezone_name": "America/Sao_Paulo",
        "utc_offset_minutes": -180,
        "gps_sync_running": True,
    }
    time_sync_calls = []
    timezone_calls = []
    controller, state, emitted = _controller(
        tmp_path,
        monkeypatch,
        initial,
        time_sync_fn=lambda gps_time, *, dry_run: time_sync_calls.append(
            (gps_time, dry_run)
        ) or True,
        timezone_fn=lambda *args, **kwargs: timezone_calls.append((args, kwargs)),
    )

    controller._run(timeout_s=1.0, mode="time_only")

    gps = state.snapshot("gps")
    unchanged = set(initial) - {"synced", "sync_time", "gps_sync_running"}
    assert {field: gps[field] for field in unchanged} == {
        field: initial[field] for field in unchanged
    }
    assert gps["synced"] is True
    assert gps["sync_time"] != initial["sync_time"]
    assert datetime.fromisoformat(gps["sync_time"]).tzinfo is not None
    assert gps["gps_sync_running"] is False
    assert time_sync_calls == [(GPS_TIME, False)]
    assert timezone_calls == []
    assert len(_events(emitted, "gps_update")) == 1
    assert _events(emitted, "gps_update")[0]["synced"] is True
    assert _events(emitted, "gps_sync_done") == [{"synced": True}]


def test_run_location_only_changes_only_location_fields(tmp_path, monkeypatch):
    initial = {
        "connected": True,
        "synced": True,
        "sync_time": "old-sync-time",
        "lat": 1.0,
        "lon": 2.0,
        "alt": 3.0,
        "satellites": 4,
        "hdop": 5.0,
        "date": "2020-01-02",
        "timezone": "UTC-3",
        "timezone_name": "America/Sao_Paulo",
        "utc_offset_minutes": -180,
        "gps_sync_running": True,
    }
    time_sync_calls = []
    timezone_calls = []
    controller, state, emitted = _controller(
        tmp_path,
        monkeypatch,
        initial,
        time_sync_fn=lambda *args, **kwargs: time_sync_calls.append((args, kwargs)),
        timezone_fn=lambda *args, **kwargs: timezone_calls.append((args, kwargs)) or 2.0,
    )

    controller._run(timeout_s=1.0, mode="location_only")

    gps = state.snapshot("gps")
    assert gps["connected"] is initial["connected"]
    assert gps["synced"] is initial["synced"]
    assert gps["sync_time"] == initial["sync_time"]
    assert gps["gps_sync_running"] is False
    assert gps["lat"] == round(GPS_POSITION.latitude, 6)
    assert gps["lon"] == round(GPS_POSITION.longitude, 6)
    assert gps["alt"] == 35.0
    assert gps["satellites"] == 8
    assert gps["hdop"] == 0.88
    assert gps["date"] == "2026-08-22"
    assert gps["timezone"] == "UTC+2"
    assert gps["timezone_name"] == "Europe/Paris"
    assert gps["utc_offset_minutes"] == 120
    assert time_sync_calls == []
    assert timezone_calls == [((GPS_POSITION.latitude, GPS_POSITION.longitude), {"eclipse_date": None})]
    assert len(_events(emitted, "gps_update")) == 1
    assert _events(emitted, "gps_sync_done") == [{"synced": True}]


def test_run_combined_mode_preserves_existing_behavior(tmp_path, monkeypatch):
    controller, state, emitted = _controller(
        tmp_path,
        monkeypatch,
        {"connected": True, "synced": False, "gps_sync_running": True},
        time_sync_fn=lambda *_args, **_kwargs: True,
        timezone_fn=lambda *_args, **_kwargs: 2.0,
    )

    controller._run(timeout_s=1.0, mode="time_location")

    gps = state.snapshot("gps")
    assert gps["connected"] is False
    assert gps["synced"] is True
    assert gps["lat"] == round(GPS_POSITION.latitude, 6)
    assert gps["timezone_name"] == "Europe/Paris"
    assert gps["gps_sync_running"] is False
    assert _events(emitted, "gps_update")[0]["synced"] is True
    assert _events(emitted, "gps_sync_done") == [{"synced": True}]


def test_run_time_sync_failure_keeps_restricted_fields(tmp_path, monkeypatch):
    initial = {
        "connected": True,
        "synced": False,
        "sync_time": "old-sync-time",
        "lat": 1.0,
        "lon": 2.0,
        "alt": 3.0,
        "satellites": 4,
        "hdop": 5.0,
        "date": "2020-01-02",
        "timezone": "UTC-3",
        "timezone_name": "America/Sao_Paulo",
        "utc_offset_minutes": -180,
        "gps_sync_running": True,
    }
    controller, state, emitted = _controller(
        tmp_path,
        monkeypatch,
        initial,
        time_sync_fn=lambda *_args, **_kwargs: False,
        timezone_fn=lambda *_args, **_kwargs: pytest.fail(
            "timezone lookup must not run after time sync failure"
        ),
    )

    controller._run(timeout_s=1.0, mode="time_location")

    gps = state.snapshot("gps")
    restricted = set(initial) - {"connected", "gps_sync_running"}
    assert {field: gps[field] for field in restricted} == {
        field: initial[field] for field in restricted
    }
    assert gps["connected"] is False
    assert gps["gps_sync_running"] is False
    assert len(_events(emitted, "gps_update")) == 1
    assert _events(emitted, "gps_sync_done") == [{"synced": False}]


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

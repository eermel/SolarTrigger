from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.gps_controller import GpsController
from backend.state_store import StateStore


SMARTPHONE_PAYLOAD = {
    "source": "smartphone",
    "client_epoch_ms": 1789387200123.0,
    "position_timestamp_ms": 1789387199123.0,
    "latitude": 48.8566123,
    "longitude": 2.3522345,
    "altitude_m": 35.4,
    "accuracy_m": 4.2,
    "altitude_accuracy_m": 7.5,
    "clock_offset_ms": 37.625,
    "clock_best_rtt_ms": 3.250,
    "clock_selected_rtt_ms": 4.125,
    "clock_probe_count": 12,
}


def _controller(tmp_path, initial_devices=None):
    state = StateStore(tmp_path / "state.json")
    state.update_section(
        "devices",
        initial_devices or {"gps": {"plugin": "smartphone", "active": True}},
    )
    emitted = []
    absolute_sync_calls = []
    relative_sync_calls = []
    controller = GpsController(
        state,
        tmp_path / "unused-gps.json",
        timezone_fn=lambda *_args, **_kwargs: 2.0,
        time_sync_fn=lambda gps_time, *, dry_run: absolute_sync_calls.append(
            (gps_time, dry_run)
        ) or True,
        time_adjust_fn=lambda offset_s, *, dry_run: relative_sync_calls.append(
            (offset_s, dry_run)
        ) or True,
        log_fn=lambda *_args: None,
        emit_fn=lambda event, payload: emitted.append((event, payload)),
    )
    return controller, state, emitted, absolute_sync_calls, relative_sync_calls


def test_selected_source_reads_persisted_smartphone_selection(tmp_path):
    controller, *_ = _controller(tmp_path)
    assert controller._selected_source() == "smartphone"


def test_smartphone_snapshot_validates_position_and_clock_metrics():
    snapshot = GpsController._smartphone_snapshot(dict(SMARTPHONE_PAYLOAD))

    assert snapshot.position.latitude == pytest.approx(48.8566123)
    assert snapshot.position.longitude == pytest.approx(2.3522345)
    assert snapshot.position.altitude_m == pytest.approx(35.4)
    assert snapshot.accuracy_m == pytest.approx(4.2)
    assert snapshot.gps_time.tzinfo is timezone.utc
    assert snapshot.clock_offset_ms == pytest.approx(37.625)
    assert snapshot.clock_best_rtt_ms == pytest.approx(3.250)
    assert snapshot.clock_selected_rtt_ms == pytest.approx(4.125)
    assert snapshot.clock_probe_count == 12


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latitude", 91.0),
        ("longitude", -181.0),
        ("accuracy_m", -1.0),
        ("client_epoch_ms", 0.0),
        ("clock_best_rtt_ms", -0.1),
        ("clock_probe_count", 2),
        ("clock_probe_count", 12.5),
    ],
)
def test_smartphone_snapshot_rejects_invalid_values(field, value):
    payload = dict(SMARTPHONE_PAYLOAD)
    payload[field] = value
    with pytest.raises(ValueError):
        GpsController._smartphone_snapshot(payload)


def test_smartphone_snapshot_rejects_stale_position():
    payload = dict(SMARTPHONE_PAYLOAD)
    payload["position_timestamp_ms"] = payload["client_epoch_ms"] - 121000
    with pytest.raises(ValueError, match="stale"):
        GpsController._smartphone_snapshot(payload)


def test_run_uses_relative_clock_adjustment_for_smartphone(tmp_path, monkeypatch):
    controller, state, emitted, absolute_sync_calls, relative_sync_calls = _controller(tmp_path)

    monkeypatch.setattr(
        "services.gps_service.GpsService.from_config",
        lambda *_args, **_kwargs: pytest.fail("hardware GPS must not be opened"),
    )

    controller._run(
        timeout_s=1.0,
        mode="time_location",
        source="smartphone",
        source_payload=dict(SMARTPHONE_PAYLOAD),
    )

    gps = state.snapshot("gps")
    assert gps["synced"] is True
    assert gps["source"] == "smartphone"
    assert gps["lat"] == round(SMARTPHONE_PAYLOAD["latitude"], 6)
    assert gps["lon"] == round(SMARTPHONE_PAYLOAD["longitude"], 6)
    assert gps["alt"] == 35.4
    assert gps["accuracy_m"] == 4.2
    assert gps["altitude_accuracy_m"] == 7.5
    assert gps["clock_offset_ms"] == pytest.approx(37.625)
    assert gps["clock_best_rtt_ms"] == pytest.approx(3.250)
    assert gps["clock_probe_count"] == 12
    assert gps["timezone"] == "UTC+2"
    assert gps["utc_offset_minutes"] == 120
    assert gps["gps_sync_running"] is False
    assert gps["connected"] is False

    assert absolute_sync_calls == []
    assert len(relative_sync_calls) == 1
    assert relative_sync_calls[0][0] == pytest.approx(0.037625)
    assert relative_sync_calls[0][1] is False
    assert any(event == "gps_update" for event, _ in emitted)
    assert ("gps_sync_done", {"synced": True}) in emitted


def test_dongle_keeps_absolute_utc_sync_path(tmp_path):
    controller, *_rest = _controller(
        tmp_path,
        {"gps": {"plugin": "serial_nmea", "active": True}},
    )
    absolute_calls = []
    relative_calls = []
    controller.time_sync_fn = lambda value, *, dry_run: absolute_calls.append(
        (value, dry_run)
    ) or True
    controller.time_adjust_fn = lambda value, *, dry_run: relative_calls.append(
        (value, dry_run)
    ) or True

    gps_time = datetime(2027, 8, 2, 11, 4, 32, tzinfo=timezone.utc)
    snap = type("Snap", (), {"gps_time": gps_time})()
    assert controller._sync_clock(snap, "serial_nmea") is True
    assert absolute_calls == [(gps_time, False)]
    assert relative_calls == []

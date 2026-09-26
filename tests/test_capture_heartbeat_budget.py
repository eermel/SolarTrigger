from types import SimpleNamespace

from scripts.eclipse_trigger import (
    CAPTURE_WATCHDOG_MARGIN_S,
    _capture_watchdog_timeout_s,
    _pulse_capture_begin,
)


def test_short_capture_keeps_existing_125_second_floor():
    prepared = SimpleNamespace(estimated_total_s=12.0)
    assert _capture_watchdog_timeout_s(prepared) == 125.0


def test_long_capture_budget_includes_target_wait_and_margin():
    prepared = SimpleNamespace(estimated_total_s=140.0)
    assert _capture_watchdog_timeout_s(
        prepared,
        wait_s=8.0,
    ) == 148.0 + CAPTURE_WATCHDOG_MARGIN_S


def test_invalid_capture_estimate_preserves_known_target_wait():
    prepared = SimpleNamespace(estimated_total_s=None)
    assert _capture_watchdog_timeout_s(
        prepared,
        wait_s=999.0,
    ) == 999.0 + CAPTURE_WATCHDOG_MARGIN_S


def test_capture_begin_publishes_dynamic_budget():
    calls = []

    def heartbeat(stage, *, timeout_s=None):
        calls.append((stage, timeout_s))

    prepared = SimpleNamespace(estimated_total_s=150.0)
    _pulse_capture_begin(heartbeat, prepared, wait_s=5.0)

    assert calls == [
        ("capture.begin", 155.0 + CAPTURE_WATCHDOG_MARGIN_S),
    ]


def test_capture_begin_keeps_legacy_callback_compatible():
    calls = []
    prepared = SimpleNamespace(estimated_total_s=150.0)

    _pulse_capture_begin(calls.append, prepared)

    assert calls == ["capture.begin"]

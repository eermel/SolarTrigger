from datetime import datetime
from pathlib import Path

from backend.phase_trigger import PhaseWindow
from backend.trigger_service import TriggerService
from scripts.eclipse_trigger import _phase_label


def test_phase_runtime_events_have_public_labels_levels_and_states():
    expected = {
        "partial_before": ("PHASE 1 — Partial", "partial"),
        "diamond_ring_c2": ("PHASE 2 — Diamond ring", "diamond_ring"),
        "totality": ("PHASE 3 — Totality", "totality"),
        "diamond_ring_c3": ("PHASE 4 — Diamond ring", "diamond_ring"),
        "partial_after": ("PHASE 5 — Partial", "partial"),
    }

    for internal_name, (label, public_phase) in expected.items():
        assert TriggerService._runtime_log_event(
            f"TRIGGER_PHASE {internal_name}"
        ) == (label, "phase", public_phase)


def test_audio_runtime_event_reports_the_sound_that_started():
    assert TriggerService._runtime_log_event(
        "TRIGGER_AUDIO filters_off.wav"
    ) == ("🔊 Sound played: filters_off.wav", "audio", None)


def test_regular_runtime_log_line_is_unchanged():
    assert TriggerService._runtime_log_event(
        "INFO phase=partial_before PHOTO frames=3"
    ) == ("INFO phase=partial_before PHOTO frames=3", None, None)


def test_photo_log_phase_labels_never_expose_internal_window_names():
    instant = datetime(2027, 8, 2, 10, 0)
    for internal_name, photo_phase, expected in (
        ("partial_before", "partial", "Partial"),
        ("diamond_ring_c2", "diamond_ring", "Diamond ring"),
        ("totality", "totality", "Totality"),
        ("diamond_ring_c3", "diamond_ring", "Diamond ring"),
        ("partial_after", "partial", "Partial"),
    ):
        window = PhaseWindow(
            internal_name, photo_phase, instant, instant, 0.0
        )
        assert _phase_label(window) == expected


def test_runtime_event_levels_have_the_required_log_colors():
    root = Path(__file__).resolve().parents[1]
    css = (root / "flask_app/static/css/solartrigger.css").read_text(
        encoding="utf-8"
    )
    assert ".log-line.audio   { color: var(--yellow); }" in css
    assert ".log-line.phase   { color: var(--red); }" in css

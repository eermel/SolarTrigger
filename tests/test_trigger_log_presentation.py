from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.phase_trigger import PhaseRuntime, PhaseSchedule, PhaseWindow
from backend.trigger_service import TriggerService


def test_runtime_log_event_presentation_contract():
    assert TriggerService._runtime_log_event("TRIGGER_PHASE_BORDER") == ("#" * 65, "phase", None)
    assert TriggerService._runtime_log_event("TRIGGER_PHASE totality") == ("### Phase 3 — Totality", "phase", "totality")
    assert TriggerService._runtime_log_event("TRIGGER_CONFIG SET aperture=f/8 ISO=100") == ("SET aperture=f/8 ISO=100", "gps", None)
    assert TriggerService._runtime_log_event("TRIGGER_PHOTO purple PHOTO frames=3 [1/500][1/1000][1/2000]") == ("PHOTO frames=3 [1/500][1/1000][1/2000]", "purple", None)
    assert TriggerService._runtime_log_event("TRIGGER_AUDIO contact.wav") == ("🔊 Sound played: contact.wav", "audio", None)
    assert TriggerService._runtime_log_event(
        'TRIGGER_SUMMARY phase="TOTALITY" photos=42'
    ) == ("TOTALITY — Photos: 42", "success", None)


def test_partial_next_capture_is_announced_once_per_target():
    start = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    window = PhaseWindow("partial_before", "partial", start, start + timedelta(seconds=4), 2.0)
    schedule = PhaseSchedule(tstart=start, tend=window.end, tmax=start, windows=(window,))
    now_value = [start]
    announcements = []
    captures = []

    def now():
        return now_value[0]

    def wait_until(target):
        now_value[0] = target

    def capture(_window, started):
        captures.append(started)
        now_value[0] = started + timedelta(milliseconds=100)
        return True

    PhaseRuntime(
        schedule, now=now, wait_until=wait_until,
        enter_phase=lambda _window: None,
        reconcile_phase=lambda _window: None,
        capture=capture, log_error=lambda _message: None,
        next_capture_log=lambda _window, target: announcements.append(target),
    ).run()

    assert captures[0] == start
    assert announcements == [start + timedelta(seconds=2)]



def test_trigger_summary_contains_photo_count_only():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "eclipse_trigger.py"
    ).read_text(encoding="utf-8")

    start = source.index('log("TRIGGER_SUMMARY_BEGIN")')
    end = source.index('log("TRIGGER_SUMMARY_END")', start)
    summary = source[start:end]

    assert 'photos={stats["photos"]}' in summary
    assert "errors=" not in summary

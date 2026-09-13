from datetime import timedelta

from backend.phase_trigger import build_phase_schedule
from scripts.eclipse_trigger import _phase_alerts
from tests.test_phase_trigger import BASE, _photo, _timeline


def test_audio_uses_runtime_phase_boundaries_and_all_contact_offsets():
    timeline = _timeline()
    schedule = build_phase_schedule(timeline, _photo())
    alerts = set(_phase_alerts(schedule, timeline))

    assert (schedule.tstart - timedelta(seconds=30), "filters_on.wav") in alerts
    assert (
        schedule.windows[1].start - timedelta(seconds=2),
        "filters_off.wav",
    ) in alerts
    assert (
        schedule.windows[3].end + timedelta(seconds=2),
        "filters_on.wav",
    ) in alerts

    for contact_name in ("C1", "C2", "C3", "C4"):
        contact = timeline[contact_name]
        assert (contact, "contact.wav") in alerts

    assert (
        timeline["C2"] - timedelta(minutes=2),
        "2minutes.wav",
    ) in alerts


def test_c3_long_announcements_do_not_overlap_the_pre_c2_phase():
    timeline = _timeline()
    schedule = build_phase_schedule(timeline, _photo())
    alerts = set(_phase_alerts(schedule, timeline))

    assert (
        timeline["C3"] - timedelta(minutes=5),
        "5minutes.wav",
    ) not in alerts
    assert (
        timeline["C3"] - timedelta(seconds=60),
        "60seconds.wav",
    ) in alerts

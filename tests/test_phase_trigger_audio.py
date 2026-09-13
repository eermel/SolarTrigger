from datetime import timedelta
from pathlib import Path

from backend.phase_trigger import build_phase_schedule
from scripts import eclipse_trigger
from scripts.eclipse_trigger import _phase_alerts
from tests.test_phase_trigger import _photo, _timeline


def test_audio_uses_human_phase_specific_wavs():
    timeline = _timeline()
    schedule = build_phase_schedule(timeline, _photo())
    alerts = set(_phase_alerts(schedule, timeline))

    assert (
        schedule.tstart,
        "human_wav/sequence_started.wav",
    ) in alerts
    assert (
        schedule.tend,
        "human_wav/sequence_ended.wav",
    ) not in alerts

    assert (
        timeline["C1"] - timedelta(minutes=5),
        "human_wav/first_contact_minus_5m.wav",
    ) in alerts
    assert (
        timeline["C1"] - timedelta(seconds=10),
        "human_wav/first_contact_minus_10s.wav",
    ) in alerts
    assert (
        timeline["C1"],
        "human_wav/first_contact.wav",
    ) in alerts
    assert (
        timeline["C1"],
        "human_wav/start_of_partiality.wav",
    ) in alerts

    assert (
        timeline["C2"] - timedelta(minutes=2),
        "human_wav/totality_minus_2m.wav",
    ) in alerts
    assert (
        timeline["C2"],
        "human_wav/totality.wav",
    ) in alerts

    assert (
        timeline["C3"],
        "human_wav/end_of_totality.wav",
    ) in alerts

    assert (
        timeline["C4"],
        "human_wav/last_contact.wav",
    ) in alerts
    assert (
        timeline["C4"],
        "human_wav/end_of_partiality.wav",
    ) in alerts


def test_final_five_second_countdown_uses_human_digit_wavs():
    timeline = _timeline()
    schedule = build_phase_schedule(timeline, _photo())
    alerts = set(_phase_alerts(schedule, timeline))

    for contact_name in ("C1", "C2", "C3", "C4"):
        contact = timeline[contact_name]

        for seconds in range(1, 6):
            assert (
                contact - timedelta(seconds=seconds),
                f"human_wav/{seconds}.wav",
            ) in alerts


def test_audio_filter_announcements_follow_runtime_boundaries():
    timeline = _timeline()
    schedule = build_phase_schedule(timeline, _photo())
    alerts = set(_phase_alerts(schedule, timeline))

    assert (
        schedule.tstart - timedelta(seconds=30),
        "human_wav/filters_on.wav",
    ) in alerts

    assert (
        schedule.windows[1].start - timedelta(seconds=2),
        "human_wav/filters_off.wav",
    ) in alerts

    assert (
        schedule.windows[3].end + timedelta(seconds=2),
        "human_wav/filters_on.wav",
    ) in alerts


def test_c3_long_announcements_do_not_overlap_the_pre_c2_phase():
    timeline = _timeline()
    schedule = build_phase_schedule(timeline, _photo())
    alerts = set(_phase_alerts(schedule, timeline))

    assert (
        timeline["C3"] - timedelta(minutes=5),
        "human_wav/end_totality_minus_5m.wav",
    ) not in alerts

    assert (
        timeline["C3"] - timedelta(seconds=60),
        "human_wav/end_totality_minus_1m.wav",
    ) in alerts


def test_missing_wav_is_warning_only_and_never_calls_player(
    tmp_path, monkeypatch
):
    logs = []
    played = []

    monkeypatch.setattr(eclipse_trigger, "SOUNDS_DIR", tmp_path)
    monkeypatch.setattr(eclipse_trigger, "log", logs.append)
    monkeypatch.setattr(
        eclipse_trigger.audio_service,
        "play",
        lambda filename: played.append(filename),
    )

    eclipse_trigger._play_audio_alert(
        "human_wav/missing.wav"
    )

    assert played == []
    assert len(logs) == 1
    assert "WARNING audio file missing:" in logs[0]
    assert "missing.wav" in logs[0]
    assert "trigger continues" in logs[0]


def test_audio_playback_exception_is_warning_only(
    tmp_path, monkeypatch
):
    logs = []

    sounds = tmp_path / "human_wav"
    sounds.mkdir()
    wav = sounds / "contact.wav"
    wav.write_bytes(b"RIFF")

    monkeypatch.setattr(eclipse_trigger, "SOUNDS_DIR", tmp_path)
    monkeypatch.setattr(eclipse_trigger, "log", logs.append)

    def fail(_filename):
        raise RuntimeError("audio device unavailable")

    monkeypatch.setattr(
        eclipse_trigger.audio_service,
        "play",
        fail,
    )

    eclipse_trigger._play_audio_alert(
        "human_wav/contact.wav"
    )

    assert len(logs) == 1
    assert "WARNING audio playback failed:" in logs[0]
    assert "audio device unavailable" in logs[0]
    assert "trigger continues" in logs[0]



def test_same_instant_contact_announcements_keep_declared_order():
    timeline = _timeline()
    schedule = build_phase_schedule(timeline, _photo())
    alerts = _phase_alerts(schedule, timeline)

    c1_files = [
        filename
        for instant, filename in alerts
        if instant == timeline["C1"]
    ]
    assert c1_files == [
        "human_wav/first_contact.wav",
        "human_wav/start_of_partiality.wav",
    ]

    c4_files = [
        filename
        for instant, filename in alerts
        if instant == timeline["C4"]
    ]
    assert c4_files == [
        "human_wav/last_contact.wav",
        "human_wav/end_of_partiality.wav",
    ]


def test_audio_sequence_plays_files_serially(monkeypatch):
    played = []

    monkeypatch.setattr(
        eclipse_trigger,
        "_play_audio_alert",
        lambda filename: played.append(filename),
    )

    eclipse_trigger._play_audio_sequence(
        ["first.wav", "second.wav"]
    )

    assert played == ["first.wav", "second.wav"]



def test_sequence_end_is_played_synchronously_after_phase_runtime():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "eclipse_trigger.py"
    ).read_text(encoding="utf-8")

    runtime_call = source.index("PhaseRuntime(")
    runtime_end = source.index(").run()", runtime_call)
    end_sound = source.index(
        '_play_audio_alert("human_wav/sequence_ended.wav")',
        runtime_end,
    )
    summary = source.index(
        'log("TRIGGER_SUMMARY_BEGIN")',
        runtime_end,
    )

    assert runtime_end < end_sound < summary

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOUNDS_DIR = ROOT / "Sounds"

STRICT_RUNTIME_FILES = (
    ROOT / "flask_app" / "templates" / "index.html",
)

SCHEDULED_RUNTIME_FILE = (
    ROOT / "scripts" / "eclipse_trigger.py"
)

WAV_LITERAL = re.compile(
    r"""['"]([^'"]+\.wav)['"]""",
    re.IGNORECASE,
)


def _referenced_wav_names(paths):
    names = set()

    for path in paths:
        text = path.read_text(encoding="utf-8")

        for match in WAV_LITERAL.finditer(text):
            names.add(Path(match.group(1)).name)

    return names


def _scheduled_wav_literals():
    text = SCHEDULED_RUNTIME_FILE.read_text(encoding="utf-8")
    return {
        match.group(1)
        for match in WAV_LITERAL.finditer(text)
    }


def test_strict_ui_runtime_sounds_exist():
    actual = {
        path.name
        for path in SOUNDS_DIR.glob("*.wav")
        if path.is_file()
    }

    referenced = _referenced_wav_names(
        STRICT_RUNTIME_FILES
    )

    missing = referenced - actual

    assert missing == set(), (
        "Strict UI references missing WAV assets: "
        + ", ".join(sorted(missing))
    )


def test_trigger_scheduled_audio_is_allowed_to_be_missing():
    """Missing timing WAVs are a supported degraded mode.

    scripts.eclipse_trigger._play_audio_alert() must log the missing
    announcement and allow the photographic trigger to continue.
    """
    source = SCHEDULED_RUNTIME_FILE.read_text(
        encoding="utf-8"
    )

    assert "def _play_audio_alert(" in source
    assert "WARNING audio file missing:" in source
    assert "trigger continues without this announcement" in source


def test_new_trigger_announcements_live_under_human_wav():
    source = SCHEDULED_RUNTIME_FILE.read_text(
        encoding="utf-8"
    )

    assert 'human = "human_wav"' in source
    assert 'f"{human}/first_contact' in source
    assert 'f"{human}/totality' in source
    assert 'f"{human}/end_totality' in source
    assert 'f"{human}/last_contact' in source
    assert 'f"{human}/sequence_started.wav"' in source
    assert '_play_audio_alert("human_wav/sequence_ended.wav")' in source
    assert 'f"{human}/filters_on.wav"' in source
    assert 'f"{human}/filters_off.wav"' in source


def test_scheduled_wav_paths_are_relative_and_safe():
    for value in _scheduled_wav_literals():
        path = Path(value)

        assert not path.is_absolute()
        assert ".." not in path.parts

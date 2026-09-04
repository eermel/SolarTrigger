from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOUNDS_DIR = ROOT / "Sounds"

RUNTIME_FILES = (
    ROOT / "scripts" / "eclipse_trigger.py",
    ROOT / "flask_app" / "templates" / "index.html",
)

WAV_LITERAL = re.compile(r"""['"]([^'"]+\.wav)['"]""", re.IGNORECASE)


def _referenced_wav_names():
    names = set()

    for path in RUNTIME_FILES:
        text = path.read_text(encoding="utf-8")
        for match in WAV_LITERAL.finditer(text):
            names.add(Path(match.group(1)).name)

    return names


def test_all_literal_runtime_sounds_exist():
    actual = {
        path.name
        for path in SOUNDS_DIR.glob("*.wav")
        if path.is_file()
    }

    missing = _referenced_wav_names() - actual

    assert missing == set(), (
        "Runtime references missing WAV assets: "
        + ", ".join(sorted(missing))
    )


def test_all_versioned_sounds_are_referenced():
    actual = {
        path.name
        for path in SOUNDS_DIR.glob("*.wav")
        if path.is_file()
    }

    unused = actual - _referenced_wav_names()

    assert unused == set(), (
        "Unreferenced WAV assets: "
        + ", ".join(sorted(unused))
    )

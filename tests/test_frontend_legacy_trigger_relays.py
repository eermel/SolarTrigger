from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "flask_app" / "templates" / "index.html"


def _html():
    return HTML_PATH.read_text(encoding="utf-8")


def test_legacy_trigger_relay_socket_listeners_are_absent():
    text = _html()

    assert "socket.on('play_sound'" not in text
    assert "socket.on('battery_update'" not in text
    assert "socket.on('battery_alert'" not in text


def test_local_audio_test_support_is_preserved():
    text = _html()

    assert "async function playSound(filename)" in text
    assert "function testSound(file) { playSound(file); }" in text


def test_battery_rendering_has_no_unreachable_duplicate_threshold():
    text = _html()

    start = text.index("function updateBattery(pct)")
    end = text.index("function populateOverrides", start)
    body = text[start:end]

    assert body.count("else if (pct > 20)") == 1
    assert "Low — prepare replacement" not in body

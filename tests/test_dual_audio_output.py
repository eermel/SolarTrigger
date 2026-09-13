from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")
JS = (
    ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
).read_text(encoding="utf-8")
TRIGGER_SERVICE = (
    ROOT / "backend" / "trigger_service.py"
).read_text(encoding="utf-8")


def test_runtime_audio_is_mirrored_to_browser():
    assert 'if line.startswith("TRIGGER_AUDIO "):' in TRIGGER_SERVICE
    assert '"audio_play"' in TRIGGER_SERVICE
    assert '"filename": filename' in TRIGGER_SERVICE


def test_browser_receives_audio_play_event():
    assert "socket.on('audio_play'" in JS
    assert "playSound(filename);" in JS


def test_test_button_uses_backend_dual_audio_route():
    assert "async function testSound(_file)" in JS
    assert "fetch('/api/audio/test'" in JS


def test_audio_test_route_is_fixed_to_contact_wav():
    assert '@app.route("/api/audio/test", methods=["POST"])' in APP
    assert 'filename = "contact.wav"' in APP
    assert 'target=_play_pi_test_sound' in APP
    assert '"outputs": ["pi", "browser"]' in APP


def test_pi_test_uses_existing_audio_service():
    assert "audio_service.init(" in APP
    assert "audio_service.set_sounds_dir(SOUNDS_DIR)" in APP
    assert "audio_service.play(filename)" in APP


AUDIO_SERVICE = (
    ROOT / "backend" / "audio_service.py"
).read_text(encoding="utf-8")


def test_audio_enabled_state_is_shared_between_processes():
    assert "AUDIO_ENABLED_STATE_FILE" in AUDIO_SERVICE
    assert "def is_enabled():" in AUDIO_SERVICE
    assert "def set_enabled(enabled):" in AUDIO_SERVICE
    assert "not is_enabled()" in AUDIO_SERVICE


def test_backend_exposes_global_audio_switch():
    assert '@app.route("/api/audio/enabled", methods=["GET", "POST"])' in APP
    assert "audio_service.set_enabled(enabled)" in APP
    assert '"audio_enabled"' in APP


def test_browser_global_audio_switch_uses_backend():
    assert "async function toggleSounds()" in JS
    assert "fetch('/api/audio/enabled'" in JS
    assert "function applySoundsEnabled(enabled)" in JS
    assert "socket.on('audio_enabled'" in JS


def test_audio_test_respects_global_mute():
    assert "if not audio_service.is_enabled():" in APP
    assert '"status": "muted"' in APP
    assert "flash('Sound is OFF', 'yellow')" in JS

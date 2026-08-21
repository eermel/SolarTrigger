"""Unit tests for the hardware-free audio service lifecycle."""

import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend import audio_service


@pytest.fixture(autouse=True)
def reset_audio_service():
    """Keep the module-level audio state independent between tests."""
    audio_service.shutdown(timeout=0.2)
    audio_service.SOUNDS_ENABLED = False
    audio_service.pygame = None
    audio_service._log_fn = None
    audio_service._colors = None
    audio_service._sounds_dir = ""
    audio_service._mixer_ready = False
    audio_service._stop_event.clear()
    audio_service._threads.clear()
    yield
    audio_service.shutdown(timeout=0.2)
    audio_service.SOUNDS_ENABLED = False
    audio_service.pygame = None
    audio_service._mixer_ready = False
    audio_service._stop_event.clear()
    audio_service._threads.clear()


def fake_pygame(get_busy=None, load_side_effect=None):
    music = SimpleNamespace(
        load=Mock(side_effect=load_side_effect),
        play=Mock(),
        get_busy=Mock(side_effect=get_busy) if get_busy else Mock(return_value=False),
        stop=Mock(),
    )
    mixer = SimpleNamespace(
        music=music,
        pre_init=Mock(),
        init=Mock(),
        quit=Mock(),
    )
    return SimpleNamespace(mixer=mixer)


def test_init_without_pygame_disables_audio_and_play_is_noop(monkeypatch):
    logs = []
    monkeypatch.setitem(sys.modules, "pygame", None)

    audio_service.init(logs.append)
    audio_service.play("anything.wav")

    assert logs == ["WARNING pygame non installé — sons désactivés."]
    assert audio_service.pygame is None
    assert audio_service.SOUNDS_ENABLED is False


def test_play_uses_mixer_until_track_is_finished(monkeypatch, tmp_path):
    pygame = fake_pygame(get_busy=[True, True, False])
    monkeypatch.setitem(sys.modules, "pygame", pygame)
    sound = tmp_path / "contact.wav"
    sound.touch()

    audio_service.init(lambda message: None)
    audio_service.set_sounds_dir(tmp_path)
    audio_service.play(sound.name)

    pygame.mixer.music.load.assert_called_once_with(str(sound))
    pygame.mixer.music.play.assert_called_once_with()
    assert pygame.mixer.music.get_busy.call_count == 3
    pygame.mixer.music.stop.assert_not_called()


def test_play_logs_missing_sound(monkeypatch, tmp_path):
    logs = []
    pygame = fake_pygame()
    monkeypatch.setitem(sys.modules, "pygame", pygame)
    audio_service.init(logs.append)
    audio_service.set_sounds_dir(tmp_path)

    audio_service.play("missing.wav")

    assert any(
        f"WARNING Son introuvable : {tmp_path / 'missing.wav'}" in message
        for message in logs
    )
    pygame.mixer.music.load.assert_not_called()


def test_load_error_logs_quits_and_reinitializes_mixer(monkeypatch, tmp_path):
    logs = []
    pygame = fake_pygame(load_side_effect=[RuntimeError("decoder failed"), None])
    monkeypatch.setitem(sys.modules, "pygame", pygame)
    sound = tmp_path / "broken.wav"
    sound.touch()
    audio_service.init(logs.append)
    audio_service.set_sounds_dir(tmp_path)

    audio_service.play(sound.name)

    assert any(
        message == "ERROR Erreur audio (broken.wav) : decoder failed"
        for message in logs
    )
    assert audio_service._mixer_ready is False
    assert pygame.mixer.quit.call_count == 2  # init probe, then error cleanup

    audio_service.play(sound.name)

    assert pygame.mixer.pre_init.call_count == 3
    assert pygame.mixer.init.call_count == 3
    assert pygame.mixer.music.play.call_count == 1


def test_shutdown_joins_registered_play_thread_and_stops_mixer(
    monkeypatch, tmp_path
):
    playing = threading.Event()

    def busy_until_shutdown():
        playing.set()
        return True

    pygame = fake_pygame(get_busy=busy_until_shutdown)
    monkeypatch.setitem(sys.modules, "pygame", pygame)
    sound = tmp_path / "long.wav"
    sound.touch()
    audio_service.init(lambda message: None)
    audio_service.set_sounds_dir(tmp_path)
    thread = threading.Thread(target=audio_service.play, args=(sound.name,))
    audio_service.register_thread(thread)
    thread.start()
    assert playing.wait(timeout=1.0)

    audio_service.shutdown(timeout=1.0)

    assert not thread.is_alive()
    assert pygame.mixer.music.stop.called

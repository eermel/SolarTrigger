"""Thread-safe pygame audio lifecycle for the eclipse trigger."""

import os
import threading
import time


SOUNDS_ENABLED = False

pygame = None
_log_fn = None
_colors = None
_sounds_dir = ""
_pygame_lock = threading.Lock()
_mixer_ready = False
_stop_event = threading.Event()
_threads = []
_threads_lock = threading.Lock()


def _color(name):
    return getattr(_colors, name, "") if _colors is not None else ""


def _log(message):
    if _log_fn is not None:
        _log_fn(message)


def init(log_fn, colors=None, driver="alsa"):
    """Configure pygame for local audio and probe the requested SDL driver."""
    global SOUNDS_ENABLED, pygame, _log_fn, _colors, _mixer_ready

    _log_fn = log_fn
    _colors = colors
    SOUNDS_ENABLED = False
    _mixer_ready = False
    _stop_event.clear()
    os.environ["SDL_AUDIODRIVER"] = driver

    try:
        import pygame as pygame_module
    except ImportError:
        pygame = None
        _log("WARNING pygame non installé — sons désactivés.")
        return

    pygame = pygame_module
    with _pygame_lock:
        try:
            pygame.mixer.pre_init(44100, -16, 1, 1024)
            pygame.mixer.init()
            pygame.mixer.quit()
            SOUNDS_ENABLED = True
            _log(f"Audio Jack OK (driver {driver.upper()} sur sortie jack)")
        except Exception as exc:
            SOUNDS_ENABLED = False
            _log(f"pygame.mixer {driver.upper()} échoué : {exc} — sons désactivés.")


def set_sounds_dir(path):
    """Set the directory from which WAV files are loaded."""
    global _sounds_dir
    _sounds_dir = os.fspath(path)


def _ensure_mixer():
    global _mixer_ready

    if _mixer_ready:
        return True
    try:
        pygame.mixer.pre_init(44100, -16, 1, 1024)
        pygame.mixer.init()
        _mixer_ready = True
        return True
    except Exception as exc:
        _log(f"{_color('YELLOW')}pygame.mixer init echouee : {exc}{_color('RESET')}")
        return False


def play(filename):
    """Play one WAV file synchronously, unless audio is disabled or stopped."""
    global _mixer_ready

    if not SOUNDS_ENABLED or pygame is None:
        return
    path = os.path.join(_sounds_dir, filename)
    if not os.path.isfile(path):
        _log(f"WARNING {_color('YELLOW')}Son introuvable : {path}{_color('RESET')}")
        return

    with _pygame_lock:
        try:
            if not _ensure_mixer():
                return
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy() and not _stop_event.is_set():
                time.sleep(0.1)
            if _stop_event.is_set():
                pygame.mixer.music.stop()
        except Exception as exc:
            _log(
                f"ERROR {_color('RED')}Erreur audio ({filename}) : "
                f"{exc}{_color('RESET')}"
            )
            _mixer_ready = False
            try:
                pygame.mixer.quit()
            except Exception:
                pass


def register_thread(th):
    """Register an audio thread to be joined during shutdown."""
    with _threads_lock:
        _threads.append(th)


def is_stopped():
    return _stop_event.is_set()


def wait_stop(timeout):
    return _stop_event.wait(timeout)


def shutdown(timeout=5.0):
    """Stop audio work and wait up to ``timeout`` seconds for its threads."""
    try:
        _stop_event.set()
        deadline = time.monotonic() + timeout
        with _threads_lock:
            threads = list(_threads)
        for thread in threads:
            if thread is threading.current_thread() or not thread.is_alive():
                continue
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)

        if pygame is not None and _mixer_ready:
            with _pygame_lock:
                try:
                    pygame.mixer.music.stop()
                except Exception:
                    pass
    except Exception:
        pass

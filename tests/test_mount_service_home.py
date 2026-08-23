import threading
import time

import pytest

from backend.state_store import StateStore
from services.mount_service import MountService


class BlockingHomePlugin:
    def __init__(self, home_releases):
        self.connected = False
        self.moving = False
        self.calls = []
        self.home_started = [threading.Event() for _ in home_releases]
        self.home_finished = [threading.Event() for _ in home_releases]
        self._home_releases = home_releases
        self._home_call = 0

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def move(self, direction):
        self.calls.append(("move", direction))
        self.moving = True

    def stop(self):
        self.calls.append(("stop", None))
        self.moving = False

    def go_home(self):
        call = self._home_call
        self._home_call += 1
        self.calls.append(("go_home", call))
        self.home_started[call].set()
        self._home_releases[call].wait()
        self.home_finished[call].set()

    def status(self):
        return {"moving": self.moving, "move_rate": None}

    def get_slew_speed_capabilities(self):
        return None


class CancellableHomePlugin(BlockingHomePlugin):
    def __init__(self, home_releases):
        super().__init__(home_releases)
        self.callback_used = threading.Event()

    def go_home(self, is_cancelled=None):
        self.calls.append(("go_home", 0))
        self.home_started[0].set()
        while not is_cancelled():
            self._home_releases[0].wait(timeout=0.01)
        self.callback_used.set()
        self.home_finished[0].set()


class TypeErrorHomePlugin(BlockingHomePlugin):
    def go_home(self, is_cancelled=None):
        self.calls.append(("go_home", 0))
        self.home_started[0].set()
        raise TypeError("internal home failure")


def make_service(tmp_path, home_count=1):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    releases = [threading.Event() for _ in range(home_count)]
    plugin = BlockingHomePlugin(releases)
    service = MountService(
        state_store,
        log_fn=lambda _message: None,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )
    return service, plugin, releases


def test_home_can_be_cancelled_immediately_and_slew_can_restart(tmp_path):
    service, plugin, releases = make_service(tmp_path)
    try:
        assert service.home_start()["homing"] is True
        assert plugin.home_started[0].wait(timeout=1)
        assert service.status()["homing"] is True
        with pytest.raises(RuntimeError, match="mount is homing"):
            service.start_slew("east")

        assert service.stop()["homing"] is False
        assert service.start_slew("east")["direction"] == "east"

        releases[0].set()
        assert plugin.home_finished[0].wait(timeout=1)
        status = service.status()
        assert status["homing"] is False
        assert status["moving"] is True
        assert status["direction"] == "east"
    finally:
        releases[0].set()
        service.close()


def test_home_stops_manual_motion_before_starting_worker(tmp_path):
    service, plugin, releases = make_service(tmp_path)
    try:
        service.start_slew("north")

        status = service.home_start()

        assert plugin.home_started[0].wait(timeout=1)
        assert status["homing"] is True
        assert status["moving"] is False
        assert status["direction"] is None
        assert plugin.calls[:3] == [
            ("move", "north"),
            ("stop", None),
            ("go_home", 0),
        ]
    finally:
        releases[0].set()
        service.close()


def test_obsolete_home_worker_cannot_clear_new_homing_state_or_stop(tmp_path):
    service, plugin, releases = make_service(tmp_path, home_count=2)
    try:
        service.home_start()
        assert plugin.home_started[0].wait(timeout=1)
        service.stop()
        service.home_start()
        assert plugin.home_started[1].wait(timeout=1)

        releases[0].set()
        assert plugin.home_finished[0].wait(timeout=1)

        assert service.status()["homing"] is True
        assert plugin.calls.count(("stop", None)) == 1
    finally:
        releases[0].set()
        releases[1].set()
        service.close()


def test_home_uses_plugin_cancellation_callback(tmp_path):
    service, _plugin, releases = make_service(tmp_path)
    plugin = CancellableHomePlugin(releases)
    service._plugin_loader = lambda *_args, **_kwargs: plugin
    try:
        service.home_start()
        assert plugin.home_started[0].wait(timeout=1)

        service.stop()

        assert plugin.callback_used.wait(timeout=1)
        assert plugin.home_finished[0].wait(timeout=1)
    finally:
        releases[0].set()
        service.close()


def test_stop_times_out_when_hardware_lock_is_held(tmp_path):
    service, plugin, releases = make_service(tmp_path)
    messages = []
    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_hardware_lock():
        with service._plugin_access_lock:
            lock_held.set()
            release_lock.wait(timeout=2)

    service._log = messages.append
    holder = threading.Thread(target=hold_hardware_lock)
    holder.start()
    try:
        service.home_start()
        assert plugin.home_started[0].wait(timeout=1)
        assert lock_held.wait(timeout=1)

        started = time.monotonic()
        status = service.stop()
        elapsed = time.monotonic() - started

        assert elapsed < 0.6
        assert status["homing"] is False
        assert plugin.calls.count(("stop", None)) == 0
        assert messages == [
            "mount stop could not acquire hardware lock within 0.5 s; "
            "STOP not sent"
        ]
    finally:
        release_lock.set()
        releases[0].set()
        holder.join(timeout=1)
        service.close()


def test_stop_calls_plugin_once_when_hardware_lock_is_available(tmp_path):
    service, plugin, releases = make_service(tmp_path)
    try:
        service.status()

        status = service.stop()

        assert plugin.calls.count(("stop", None)) == 1
        assert status["homing"] is False
        assert status["moving"] is False
    finally:
        releases[0].set()
        service.close()


def test_internal_home_type_error_is_logged_without_retry(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    plugin = TypeErrorHomePlugin([threading.Event()])
    messages = []
    logged = threading.Event()

    def log(message):
        messages.append(message)
        logged.set()

    service = MountService(
        state_store,
        log_fn=log,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )
    try:
        service.home_start()
        assert plugin.home_started[0].wait(timeout=1)
        assert logged.wait(timeout=1)

        assert plugin.calls.count(("go_home", 0)) == 1
        assert messages == ["mount home failed: internal home failure"]
    finally:
        service.close()

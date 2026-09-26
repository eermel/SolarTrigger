from datetime import datetime, timedelta, timezone

from services.focuser_service import FocuserService
from plugins.focuser.base import DIR_IN, DIR_OUT


class FakeStateStore:
    def __init__(self, settings=None):
        self.data = {
            "devices": {
                "focuser": {
                    "active": True,
                    "plugin": "fake",
                }
            }
        }
        if settings is not None:
            self.data["focuser_settings"] = dict(settings)

    def snapshot(self, section):
        value = self.data.get(section)
        return dict(value) if isinstance(value, dict) else value

    def update_section(self, section, value, persist=False):
        self.data[section] = dict(value)


class FakePlugin:
    def __init__(self):
        self.connected = False
        self.position = 100
        self.moving = False
        self.coarse = None
        self.fine = None
        self.jog_calls = []

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def status(self):
        return {
            "moving": self.moving,
            "holding": False,
            "step_coarse": self.coarse,
            "step_fine": self.fine,
        }

    def get_position(self):
        return self.position

    def set_step(self, coarse=None, fine=None):
        self.coarse = coarse
        self.fine = fine

    def start_continuous(self, direction, mode):
        self.moving = True
        self.jog_calls.append((direction, mode))

    def stop_continuous(self):
        self.moving = False

    def move_to(self, position, wait=False):
        self.position = position
        self.moving = not wait

    def stop(self):
        self.moving = False


def make_service(settings=None):
    store = FakeStateStore(settings)
    plugin = FakePlugin()

    def loader(*args, **kwargs):
        return plugin

    service = FocuserService(
        store,
        log_fn=lambda *_: None,
        plugin_loader=loader,
    )
    return service, store, plugin


def recent_settings(**overrides):
    data = {
        "mode": "fast",
        "slow_step": 33,
        "fast_step": 222,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    data.update(overrides)
    return data


def test_recent_settings_are_restored():
    service, store, plugin = make_service(recent_settings())

    assert service.active_step() == 222

    status = service.status()
    assert status["mode"] == "fast"
    assert status["slow_step"] == 33
    assert status["fast_step"] == 222
    assert status["active_step"] == 222


def test_expired_settings_reset_to_defaults_and_are_persisted():
    expired = recent_settings(
        updated_at=(
            datetime.now(timezone.utc) - timedelta(hours=73)
        ).isoformat()
    )

    service, store, plugin = make_service(expired)

    assert service.active_step() == 20

    saved = store.data["focuser_settings"]
    assert saved["mode"] == "slow"
    assert saved["slow_step"] == 20
    assert saved["fast_step"] == 150
    assert saved["updated_at"] != expired["updated_at"]


def test_set_mode_updates_state_and_persistence():
    service, store, plugin = make_service(recent_settings(mode="slow"))

    service.set_mode("fast")

    assert service.active_step() == 222
    saved = store.data["focuser_settings"]
    assert saved["mode"] == "fast"
    assert saved["slow_step"] == 33
    assert saved["fast_step"] == 222
    assert saved["updated_at"]


def test_set_step_updates_state_and_persistence():
    service, store, plugin = make_service(recent_settings())

    status = service.set_step(coarse=300, fine=40)

    assert status["slow_step"] == 40
    assert status["fast_step"] == 300
    assert status["active_step"] == 300

    saved = store.data["focuser_settings"]
    assert saved["slow_step"] == 40
    assert saved["fast_step"] == 300
    assert saved["updated_at"]


def test_bound_volatile_service_avoids_global_state_store():
    class InaccessibleStateStore:
        def snapshot(self, section):
            raise AssertionError(f"unexpected StateStore read: {section}")

        def update_section(self, section, value, persist=False):
            raise AssertionError(f"unexpected StateStore write: {section}")

    plugin = FakePlugin()
    loaded_plugins = []

    def loader(plugin_id, *_args, **_kwargs):
        loaded_plugins.append(plugin_id)
        return plugin

    service = FocuserService(
        InaccessibleStateStore(),
        log_fn=lambda *_: None,
        plugin_loader=loader,
        selected_plugin="fake",
        persist_policy="volatile",
    )

    assert service.status()["plugin"] == "fake"
    assert service.set_mode("fast")["mode"] == "fast"
    status = service.set_step(coarse=300, fine=40)

    assert loaded_plugins == ["fake"]
    assert status["slow_step"] == 40
    assert status["fast_step"] == 300


def test_jog_accepts_canonical_and_legacy_directions():
    service, store, plugin = make_service(recent_settings(mode="slow"))

    service.start_jog("increase")
    service.start_jog("decrease")
    service.start_jog("out")
    service.start_jog("in")

    assert plugin.jog_calls == [
        (DIR_OUT, "fine"),
        (DIR_IN, "fine"),
        (DIR_OUT, "fine"),
        (DIR_IN, "fine"),
    ]


def test_jog_mode_is_backend_authoritative():
    service, store, plugin = make_service(recent_settings(mode="fast"))

    # Legacy caller asks for fine, but backend mode remains authoritative.
    service.start_jog("out", mode="fine")

    assert plugin.jog_calls == [(DIR_OUT, "coarse")]


def test_transient_motion_state_tracks_go_home_and_jog():
    service, store, plugin = make_service(recent_settings())

    status = service.move_to(321)
    assert status["motion_command"] == "go"
    assert status["target_position"] == 321
    assert status["moving"] is True

    plugin.moving = False
    status = service.status()
    assert status["motion_command"] is None
    assert status["target_position"] is None

    status = service.home()
    assert status["motion_command"] == "home"
    assert status["target_position"] == 0
    assert status["moving"] is True

    for _ in range(2):
        status = service.stop()
        assert status["motion_command"] is None
        assert status["target_position"] is None

    status = service.start_jog("increase")
    assert status["motion_command"] == "jog"
    assert status["target_position"] is None
    assert status["moving"] is True

    for _ in range(2):
        status = service.stop_jog()
        assert status["motion_command"] is None
        assert status["target_position"] is None



def test_segmented_absolute_motion_advances_only_after_confirmed_stop():
    service, store, plugin = make_service(recent_settings())
    plugin.max_async_move_span = 2500
    calls = []

    def move_to(position, wait=False):
        calls.append((position, wait))
        plugin.moving = True

    plugin.position = 0
    plugin.move_to = move_to

    status = service.move_to(12813)
    assert calls == [(2500, False)]
    assert status["target_position"] == 12813

    # One false sample is treated as the known transient SDK condition.
    plugin.position = 2490
    plugin.moving = False
    service.status()
    assert calls == [(2500, False)]

    # A second stationary sample confirms that the segment has stopped.
    # Continue from the actual hardware position, not the nominal segment end.
    status = service.status()
    assert calls[-1] == (4990, False)
    assert status["motion_command"] == "go"
    assert status["target_position"] == 12813

    # Final target completion clears the tracked command without another move.
    plugin.position = 12813
    plugin.moving = False
    service.status()
    status = service.status()
    assert status["motion_command"] is None
    assert status["target_position"] is None
    assert calls[-1] == (4990, False)



def test_segmented_absolute_motion_retargets_before_segment_stop():
    service, store, plugin = make_service(recent_settings())
    plugin.max_async_move_span = 2500
    plugin.async_move_lookahead = 750
    calls = []

    def move_to(position, wait=False):
        calls.append((position, wait))
        plugin.moving = True

    plugin.position = 0
    plugin.move_to = move_to
    service.move_to(8000)
    assert calls == [(2500, False)]

    # Still moving and within lookahead of the current SDK target: extend the
    # absolute target before the motor can stop at the segment boundary.
    plugin.position = 1800
    plugin.moving = True
    status = service.status()

    assert calls[-1] == (4300, False)
    assert status["motion_command"] == "go"
    assert status["target_position"] == 8000
    assert status["moving"] is True


def test_segmented_home_moves_toward_existing_zero_without_resetting_counter():
    service, store, plugin = make_service(recent_settings())
    plugin.max_async_move_span = 2500
    plugin.position = 6000
    calls = []

    def move_to(position, wait=False):
        calls.append((position, wait))
        plugin.moving = True

    plugin.move_to = move_to
    status = service.home()

    assert calls == [(3500, False)]
    assert status["motion_command"] == "home"
    assert status["target_position"] == 0
    assert not hasattr(plugin, "set_current_position")


def test_absolute_motion_survives_transient_false_moving_until_target():
    service, store, plugin = make_service(recent_settings())

    # Simulate real EAF behaviour: the command starts moving, then one SDK
    # status sample reports moving=False before the requested position has
    # actually been reached.
    plugin.move_to = lambda position, wait=False: setattr(plugin, "moving", True)
    status = service.move_to(321)
    assert status["motion_command"] == "go"
    assert status["target_position"] == 321

    plugin.position = 250
    plugin.moving = False
    status = service.status()
    assert status["motion_command"] == "go"
    assert status["target_position"] == 321

    plugin.position = 321
    status = service.status()
    assert status["motion_command"] is None
    assert status["target_position"] is None

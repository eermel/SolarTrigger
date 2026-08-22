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
        self.coarse = None
        self.fine = None
        self.jog_calls = []

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def status(self):
        return {
            "moving": False,
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
        self.jog_calls.append((direction, mode))


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

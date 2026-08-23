import pytest

from backend.state_store import StateStore
from services.mount_service import MountService


class MountPlugin:
    def __init__(self):
        self.connected = False

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def status(self):
        return {"moving": False, "move_rate": None}

    def get_slew_speed_capabilities(self):
        return None


class TrackingMountPlugin(MountPlugin):
    def __init__(self, capabilities=None):
        super().__init__()
        self.capabilities = capabilities
        self.calls = []

    def get_tracking_capabilities(self):
        return self.capabilities

    def set_tracking_mode(self, mode):
        self.calls.append(("set_tracking_mode", mode))

    def start_tracking(self, mode):
        self.calls.append(("start_tracking", mode))

    def stop_tracking(self):
        self.calls.append(("stop_tracking",))


def make_service(tmp_path, plugin):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    return MountService(
        state_store,
        log_fn=lambda _message: None,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )


def test_default_tracking_state_and_missing_capabilities(tmp_path):
    service = make_service(tmp_path, MountPlugin())
    try:
        status = service.status()

        assert status["tracking_mode"] == "solar"
        assert status["tracking_enabled"] is False
        assert status["tracking_caps"] is None
    finally:
        service.close()


def test_tracking_capabilities_are_passed_through_unmodified(tmp_path):
    capabilities = {"toggle": True, "modes": ["solar", "sidereal"]}
    service = make_service(tmp_path, TrackingMountPlugin(capabilities))
    try:
        assert service.status()["tracking_caps"] is capabilities
    finally:
        service.close()


def test_set_tracking_mode_changes_mode_without_enabling(tmp_path):
    plugin = TrackingMountPlugin({"toggle": True})
    service = make_service(tmp_path, plugin)
    try:
        status = service.set_tracking_mode("sidereal")

        assert status["tracking_mode"] == "sidereal"
        assert status["tracking_enabled"] is False
        assert plugin.calls == [("set_tracking_mode", "sidereal")]
    finally:
        service.close()


def test_set_tracking_mode_without_plugin_setter_preserves_state(tmp_path):
    service = make_service(tmp_path, MountPlugin())
    try:
        status = service.set_tracking_mode("sidereal")

        assert status["tracking_mode"] == "solar"
        assert status["tracking_enabled"] is False
    finally:
        service.close()


def test_start_and_stop_tracking_call_plugin_and_update_state(tmp_path):
    plugin = TrackingMountPlugin({"toggle": True})
    service = make_service(tmp_path, plugin)
    try:
        service.set_tracking_mode("sidereal")

        started = service.start_tracking()
        assert started["tracking_enabled"] is True
        assert plugin.calls[-1] == ("start_tracking", "sidereal")

        stopped = service.stop_tracking()
        assert stopped["tracking_enabled"] is False
        assert plugin.calls[-1] == ("stop_tracking",)
    finally:
        service.close()


@pytest.mark.parametrize("operation", ["start_tracking", "stop_tracking"])
def test_tracking_toggle_requires_plugin_capability(tmp_path, operation):
    plugin = TrackingMountPlugin({"toggle": False})
    service = make_service(tmp_path, plugin)
    try:
        with pytest.raises(RuntimeError, match="tracking toggle is unsupported"):
            getattr(service, operation)()

        assert service.status()["tracking_enabled"] is False
        assert plugin.calls == []
    finally:
        service.close()

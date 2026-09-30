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
    plugin = TrackingMountPlugin(capabilities)
    service = make_service(tmp_path, plugin)
    try:
        status = service.status()
        service.status()

        assert status["tracking_caps"] is capabilities
        assert plugin.calls == []
    finally:
        service.close()


def test_connect_and_reconnect_preserve_running_tracking(tmp_path):
    class AlreadyTrackingMountPlugin(TrackingMountPlugin):
        @property
        def tracking(self):
            return True

        def status(self):
            status = super().status()
            status["tracking"] = True
            return status

    plugin = AlreadyTrackingMountPlugin({"toggle": True})
    service = make_service(tmp_path, plugin)
    try:
        first = service.status()

        assert first["tracking_enabled"] is True
        assert plugin.calls == []

        # Force a real service reconnect.  Re-opening the physical mount must
        # observe the existing tracking state without issuing stop_tracking().
        service.close()
        second = service.status()

        assert second["tracking_enabled"] is True
        assert plugin.calls == []
    finally:
        service.close()


def test_set_tracking_mode_changes_mode_without_enabling(tmp_path):
    plugin = TrackingMountPlugin({"toggle": True})
    service = make_service(tmp_path, plugin)
    try:
        status = service.set_tracking_mode("sidereal")

        assert status["tracking_mode"] == "sidereal"
        assert status["tracking_enabled"] is False
        assert plugin.calls == [
            ("set_tracking_mode", "sidereal"),
        ]
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

        stopped = service.stop_tracking()
        assert stopped["tracking_enabled"] is False
        assert plugin.calls == [
            ("set_tracking_mode", "sidereal"),
            ("start_tracking", "sidereal"),
            ("stop_tracking",),
        ]
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


def test_fast_trigger_preflight_operations_skip_full_status(tmp_path):
    class FastPlugin(TrackingMountPlugin):
        def __init__(self):
            super().__init__({"toggle": True})
            self.status_calls = 0

        @property
        def tracking(self):
            return False

        def status(self):
            self.status_calls += 1
            raise AssertionError("full status must not be used by fast preflight")

        def sync_site_time(self, lat, lon, elev, utc_iso, offset):
            self.calls.append(
                ("sync_site_time", lat, lon, elev, utc_iso, offset)
            )
            return {
                "latitude": lat,
                "longitude": lon,
                "elevation": elev,
                "utc": utc_iso,
                "utc_offset_hours": offset,
            }

    plugin = FastPlugin()
    service = make_service(tmp_path, plugin)
    try:
        synced = service.sync_site_time_fast(
            48.0,
            2.0,
            100.0,
            "2026-09-30T09:00:00",
            2.0,
        )
        mode = service.set_tracking_mode_fast("solar")
        started = service.start_tracking_fast()

        assert synced["synchronization"]["latitude"] == 48.0
        assert mode["tracking_mode"] == "solar"
        assert started["tracking_enabled"] is True
        assert plugin.status_calls == 0
        assert plugin.calls == [
            (
                "sync_site_time",
                48.0,
                2.0,
                100.0,
                "2026-09-30T09:00:00",
                2.0,
            ),
            ("set_tracking_mode", "solar"),
            ("start_tracking", "solar"),
        ]
    finally:
        service.close()

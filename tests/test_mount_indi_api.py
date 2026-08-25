import pytest

from backend.state_store import StateStore
import plugins.mount as mount_plugins
from services.mount_service import MountService
from test_mount_api import FakeMountPlugin, flask_module


class FakeIndiMount(FakeMountPlugin):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.tracking = False
        self.tracking_mode = "sidereal"

    def status(self):
        status = super().status()
        status.update({
            "tracking": self.tracking,
            "tracking_rate": self.tracking_mode,
            "device": {
                "driver": "fake_indi_telescope",
                "device": "Fake INDI Mount",
                "model": "Test Mount",
            },
        })
        return status

    def get_tracking_capabilities(self):
        return {"toggle": True, "modes": ["sidereal", "solar"]}

    def set_tracking_mode(self, mode):
        assert mode in self.get_tracking_capabilities()["modes"]
        self.calls.append(("set_tracking_mode", mode))
        self.tracking_mode = mode

    def start_tracking(self, mode):
        assert mode in self.get_tracking_capabilities()["modes"]
        self.calls.append(("start_tracking", mode))
        self.tracking_mode = mode
        self.tracking = True

    def stop_tracking(self):
        self.calls.append(("stop_tracking",))
        self.tracking = False

    def move(self, direction):
        self.calls.append(("move", direction))
        super().move(direction)

    def stop(self):
        self.calls.append(("stop",))
        super().stop()


@pytest.fixture
def mount_indi_api(tmp_path, monkeypatch):
    plugin = FakeIndiMount()

    def load_mount(plugin_id, log_fn=print, config=None):
        assert plugin_id == "indi"
        return plugin

    monkeypatch.setattr(mount_plugins, "load_mount", load_mount)
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "indi", "active": True}}
    )
    service = MountService(
        state_store,
        log_fn=lambda _message: None,
        plugin_loader=mount_plugins.load_mount,
    )
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_mount_service", service)
    flask_module.app.config.update(TESTING=True)

    yield flask_module.app.test_client(), plugin

    service.close()


def test_mount_endpoints_pass_through_to_fake_indi(mount_indi_api):
    client, plugin = mount_indi_api

    status = client.get("/api/mount/status")

    assert status.status_code == 200
    assert status.get_json()["plugin"] == "indi"
    assert status.get_json()["device"] == {
        "driver": "fake_indi_telescope",
        "device": "Fake INDI Mount",
        "model": "Test Mount",
    }

    mode = client.post("/api/mount/tracking/mode", json={"mode": "solar"})
    assert mode.status_code == 200
    assert mode.get_json()["tracking_mode"] == "solar"
    assert ("set_tracking_mode", "solar") in plugin.calls

    started_tracking = client.post("/api/mount/tracking/start")
    assert started_tracking.status_code == 200
    assert started_tracking.get_json()["tracking_enabled"] is True
    assert plugin.calls[-1] == ("start_tracking", "solar")

    stopped_tracking = client.post("/api/mount/tracking/stop")
    assert stopped_tracking.status_code == 200
    assert stopped_tracking.get_json()["tracking_enabled"] is False
    assert plugin.calls[-1] == ("stop_tracking",)

    started_slew = client.post(
        "/api/mount/slew/start", json={"direction": "east"}
    )
    assert started_slew.status_code == 200
    assert started_slew.get_json()["moving"] is True
    assert started_slew.get_json()["direction"] == "east"
    assert plugin.calls[-1] == ("move", "east")

    stopped_slew = client.post("/api/mount/slew/stop")
    assert stopped_slew.status_code == 200
    assert stopped_slew.get_json()["moving"] is False
    assert plugin.calls[-1] == ("stop",)

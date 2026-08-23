import threading

import pytest

from backend.state_store import StateStore
from services.mount_service import MountService
from test_mount_api import FakeMountPlugin, flask_module


class BlockingHomeMountPlugin(FakeMountPlugin):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.home_started = threading.Event()
        self.home_release = threading.Event()

    def move(self, direction):
        self.calls.append(("move", direction))
        super().move(direction)

    def stop(self):
        self.calls.append(("stop", None))
        super().stop()

    def go_home(self):
        self.calls.append(("go_home", None))
        self.home_started.set()
        self.home_release.wait()


@pytest.fixture
def mount_home_api(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    plugin = BlockingHomeMountPlugin()
    service = MountService(
        state_store,
        log_fn=lambda _message: None,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_mount_service", service)
    flask_module.app.config.update(TESTING=True)
    try:
        yield flask_module.app.test_client(), plugin
    finally:
        plugin.home_release.set()
        service.close()


def test_home_start_returns_homing_true(mount_home_api):
    client, plugin = mount_home_api

    response = client.post("/api/mount/home")

    assert response.status_code == 200
    assert response.get_json()["homing"] is True
    assert plugin.home_started.wait(timeout=1)


def test_slew_stop_cancels_home_immediately(mount_home_api):
    client, plugin = mount_home_api
    client.post("/api/mount/home")
    assert plugin.home_started.wait(timeout=1)

    response = client.post("/api/mount/slew/stop")

    assert response.status_code == 200
    assert response.get_json()["homing"] is False


def test_slew_start_during_home_returns_homing_conflict(mount_home_api):
    client, plugin = mount_home_api
    client.post("/api/mount/home")
    assert plugin.home_started.wait(timeout=1)

    response = client.post(
        "/api/mount/slew/start", json={"direction": "east"}
    )

    assert response.status_code == 409
    assert response.get_json()["code"] == "MOUNT_HOMING"


def test_home_stops_active_slew_before_starting(mount_home_api):
    client, plugin = mount_home_api
    started = client.post(
        "/api/mount/slew/start", json={"direction": "north"}
    )
    assert started.status_code == 200

    response = client.post("/api/mount/home")

    assert response.status_code == 200
    assert response.get_json()["homing"] is True
    assert response.get_json()["moving"] is False
    assert plugin.home_started.wait(timeout=1)
    assert plugin.calls[:3] == [
        ("move", "north"),
        ("stop", None),
        ("go_home", None),
    ]

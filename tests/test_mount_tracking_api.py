import pytest

from backend.state_store import StateStore
from services.mount_service import MountService
from test_mount_api import FakeMountPlugin, flask_module


class TrackingMountPlugin(FakeMountPlugin):
    def __init__(self, capabilities):
        super().__init__()
        self.tracking_capabilities = capabilities
        self.tracking_calls = []

    def get_tracking_capabilities(self):
        return self.tracking_capabilities

    def set_tracking_mode(self, mode):
        self.tracking_calls.append(("set_tracking_mode", mode))

    def start_tracking(self, mode):
        self.tracking_calls.append(("start_tracking", mode))

    def stop_tracking(self):
        self.tracking_calls.append(("stop_tracking",))


@pytest.fixture
def mount_tracking_api(tmp_path, monkeypatch):
    services = []

    def make_client(plugin):
        state_store = StateStore(tmp_path / f"state-{len(services)}.json")
        state_store.update_section(
            "devices", {"mount": {"plugin": "fake", "active": True}}
        )
        service = MountService(
            state_store,
            log_fn=lambda _message: None,
            plugin_loader=lambda *_args, **_kwargs: plugin,
        )
        services.append(service)
        monkeypatch.setattr(flask_module, "_state_store", state_store)
        monkeypatch.setattr(flask_module, "_mount_service", service)
        flask_module.app.config.update(TESTING=True)
        return flask_module.app.test_client(), state_store

    yield make_client

    for service in services:
        service.close()


def test_status_defaults_and_has_no_tracking_command_side_effects(
    mount_tracking_api,
):
    plugin = FakeMountPlugin()
    client, _state_store = mount_tracking_api(plugin)

    response = client.get("/api/mount/status")

    assert response.status_code == 200
    assert response.get_json()["tracking_mode"] == "solar"
    assert response.get_json()["tracking_enabled"] is False
    assert response.get_json()["tracking_caps"] is None
    assert response.get_json()["plugin"] == "fake"
    assert plugin.moving is False
    assert plugin.move_rate is None


def test_status_exposes_plugin_tracking_capabilities(mount_tracking_api):
    capabilities = {"toggle": True, "modes": ["solar", "sidereal"]}
    plugin = TrackingMountPlugin(capabilities)
    client, _state_store = mount_tracking_api(plugin)

    response = client.get("/api/mount/status")

    assert response.status_code == 200
    assert response.get_json()["tracking_caps"] == capabilities
    assert plugin.tracking_calls == [("stop_tracking",)]

    second_response = client.get("/api/mount/status")

    assert second_response.status_code == 200
    assert plugin.tracking_calls == [("stop_tracking",)]


@pytest.mark.parametrize(
    "payload", [None, [], {}, {"mode": "lunar"}, {"mode": True}]
)
def test_tracking_mode_rejects_invalid_payload(mount_tracking_api, payload):
    plugin = TrackingMountPlugin({"toggle": True})
    client, _state_store = mount_tracking_api(plugin)

    response = client.post("/api/mount/tracking/mode", json=payload)

    assert response.status_code == 400
    assert plugin.tracking_calls == []


def test_tracking_mode_change_does_not_enable_tracking(mount_tracking_api):
    plugin = TrackingMountPlugin({"toggle": True})
    client, _state_store = mount_tracking_api(plugin)

    response = client.post(
        "/api/mount/tracking/mode", json={"mode": "sidereal"}
    )

    assert response.status_code == 200
    assert response.get_json()["tracking_mode"] == "sidereal"
    assert response.get_json()["tracking_enabled"] is False
    assert plugin.tracking_calls == [
        ("stop_tracking",),
        ("set_tracking_mode", "sidereal"),
    ]


def test_tracking_start_and_stop_with_toggle_capability(mount_tracking_api):
    plugin = TrackingMountPlugin({"toggle": True})
    client, _state_store = mount_tracking_api(plugin)

    started = client.post("/api/mount/tracking/start")
    stopped = client.post("/api/mount/tracking/stop")

    assert started.status_code == 200
    assert started.get_json()["tracking_enabled"] is True
    assert stopped.status_code == 200
    assert stopped.get_json()["tracking_enabled"] is False
    assert plugin.tracking_calls == [
        ("stop_tracking",),
        ("start_tracking", "solar"),
        ("stop_tracking",),
    ]


@pytest.mark.parametrize("capabilities", [None, {}, {"toggle": False}])
@pytest.mark.parametrize(
    "path", ["/api/mount/tracking/start", "/api/mount/tracking/stop"]
)
def test_tracking_toggle_rejected_without_capability(
    mount_tracking_api, capabilities, path
):
    plugin = TrackingMountPlugin(capabilities)
    client, _state_store = mount_tracking_api(plugin)

    response = client.post(path)

    assert response.status_code == 400
    assert "tracking toggle is unsupported" in response.get_json()["error"]
    assert plugin.tracking_calls == []


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/mount/tracking/mode", {"mode": "sidereal"}),
        ("/api/mount/tracking/start", None),
        ("/api/mount/tracking/stop", None),
    ],
)
def test_tracking_changes_rejected_while_trigger_runs(
    mount_tracking_api, path, payload
):
    plugin = TrackingMountPlugin({"toggle": True})
    client, state_store = mount_tracking_api(plugin)
    state_store.update_trigger_rig(1, {"running": True})

    response = client.post(path, json=payload)

    assert response.status_code == 409
    assert response.get_json()["code"] == "TRIGGER_RUNNING"
    assert plugin.tracking_calls == []


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/mount/tracking/mode", {"mode": "sidereal"}),
        ("/api/mount/tracking/start", None),
        ("/api/mount/tracking/stop", None),
    ],
)
def test_tracking_changes_rejected_for_inactive_mount(
    mount_tracking_api, path, payload
):
    plugin = TrackingMountPlugin({"toggle": True})
    client, state_store = mount_tracking_api(plugin)
    state_store.update_section(
        "devices", {"mount": {"plugin": "none", "active": False}}
    )

    response = client.post(path, json=payload)

    assert response.status_code == 409
    assert response.get_json()["code"] == "DEVICE_INACTIVE"
    assert plugin.tracking_calls == []

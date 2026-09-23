from pathlib import Path

import pytest

from backend import device_inventory
from plugins.mount import indi_plugin
from plugins.mount.indi_client import IndiSubprocessClient
from plugins.mount.indi_plugin import IndiMount


def _onstep_classes():
    pytest.importorskip("serial")
    from plugins.mount import onstep_plugin
    from plugins.mount.onstep_plugin import OnStepMount
    return onstep_plugin, OnStepMount


def test_onstep_inventory_uses_stable_serial_path(monkeypatch):
    _onstep_plugin, OnStepMount = _onstep_classes()
    port = "/dev/serial/by-id/usb-OnStep-controller"

    monkeypatch.setattr(
        OnStepMount,
        "_probe_identity",
        classmethod(
            lambda cls, config=None: (
                {
                    "product": "Tessek Mini 11",
                    "firmware": "4.24",
                }
                if config.get("port") == port
                else None
            )
        ),
    )

    devices = OnStepMount.inventory({"port": port})

    assert devices == [{
        "category": "mount",
        "backend": "onstep",
        "manufacturer": "OnStep",
        "model": "Tessek Mini 11",
        "firmware": "4.24",
        "fallback_physical_path": port,
    }]


def test_onstep_binding_fallback_is_used_as_connection_port(monkeypatch):
    onstep_plugin, OnStepMount = _onstep_classes()
    captured = {}

    class FakeOnStep:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(onstep_plugin, "OnStep", FakeOnStep)

    port = "/dev/serial/by-id/usb-OnStep-controller"
    OnStepMount(config={
        "backend": "onstep",
        "fallback_physical_path": port,
    })

    assert captured["port"] == port


def test_indi_client_default_timeout_allows_indi_getprop_default_wait():
    assert IndiSubprocessClient().timeout_s == 4.0


def test_indi_probe_forwards_configured_client_timeout(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def ensure_device_present(self, device_name):
            captured["device_name"] = device_name

    monkeypatch.setattr(indi_plugin, "IndiSubprocessClient", FakeClient)

    assert IndiMount.probe({
        "host": "127.0.0.1",
        "port": 7624,
        "device": "EQMod Mount",
        "client_timeout": 4.5,
    })

    assert captured["timeout_s"] == 4.5
    assert captured["device_name"] == "EQMod Mount"


def test_indi_inventory_uses_serial_by_id_as_physical_identity(monkeypatch):
    port = "/dev/serial/by-id/usb-FTDI_EQMOD"

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def ensure_device_present(self, device_name):
            assert device_name == "EQMod Mount"

        def get_props(self, patterns):
            assert "DEVICE_PORT.PORT" in patterns
            return {
                "DEVICE_PORT": {"PORT": port},
                "DRIVER_INFO": {"DRIVER_EXEC": "indi_eqmod_telescope"},
            }

    monkeypatch.setattr(indi_plugin, "IndiSubprocessClient", FakeClient)

    devices = IndiMount.inventory({
        "device": "EQMod Mount",
        "client_timeout": 4.0,
    })

    assert devices == [{
        "category": "mount",
        "backend": "indi",
        "model": "EQMod Mount",
        "device_name": "EQMod Mount",
        "fallback_physical_path": port,
    }]


def test_indi_nonstable_tty_is_resolved_to_serial_by_id(monkeypatch):
    monkeypatch.setattr(
        indi_plugin.os.path,
        "realpath",
        lambda value: (
            "/dev/ttyUSB1"
            if value in {
                "/dev/ttyUSB1",
                "/dev/serial/by-id/usb-FTDI_EQMOD",
            }
            else value
        ),
    )
    monkeypatch.setattr(
        indi_plugin.os,
        "listdir",
        lambda root: ["usb-FTDI_EQMOD"],
    )

    assert IndiMount._stable_serial_path("/dev/ttyUSB1") == (
        "/dev/serial/by-id/usb-FTDI_EQMOD"
    )


def test_device_name_without_physical_identity_is_not_bindable(monkeypatch):
    monkeypatch.setattr(device_inventory, "_discover_cameras", lambda: [])
    monkeypatch.setattr(device_inventory, "_discover_focusers", lambda: [])
    monkeypatch.setattr(
        device_inventory,
        "_discover_mounts",
        lambda: [{
            "category": "mount",
            "backend": "indi",
            "device_name": "EQMod Mount",
        }],
    )

    mount = device_inventory.refresh_inventory()["mount"][0]

    assert mount["device_name"] == "EQMod Mount"
    assert mount["bindable"] is False


def test_mount_with_serial_by_id_fallback_is_bindable(monkeypatch):
    monkeypatch.setattr(device_inventory, "_discover_cameras", lambda: [])
    monkeypatch.setattr(device_inventory, "_discover_focusers", lambda: [])
    monkeypatch.setattr(
        device_inventory,
        "_discover_mounts",
        lambda: [{
            "category": "mount",
            "backend": "onstep",
            "fallback_physical_path":
                "/dev/serial/by-id/usb-OnStep-controller",
        }],
    )

    mount = device_inventory.refresh_inventory()["mount"][0]

    assert mount["bindable"] is True
    assert mount["fallback_physical_path"] == (
        "/dev/serial/by-id/usb-OnStep-controller"
    )



def test_indi_inventory_prefers_exposed_model_and_serial(monkeypatch):
    port = "/dev/serial/by-id/usb-FTDI_EQMOD"

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def ensure_device_present(self, device_name):
            assert device_name == "EQMod Mount"

        def get_props(self, patterns):
            assert "DEVICE_INFO.*" in patterns
            assert "MOUNTINFORMATION.*" in patterns
            return {
                "DEVICE_PORT": {"PORT": port},
                "DRIVER_INFO": {"DRIVER_EXEC": "indi_eqmod_telescope"},
                "DEVICE_INFO": {
                    "MANUFACTURER": "Sky-Watcher",
                    "SERIAL_NUMBER": "MOUNT12345678",
                },
                "MOUNTINFORMATION": {
                    "MOUNT_MODEL": "EQ6-R Pro",
                },
            }

    monkeypatch.setattr(indi_plugin, "IndiSubprocessClient", FakeClient)

    devices = IndiMount.inventory({
        "device": "EQMod Mount",
        "client_timeout": 4.0,
    })

    assert devices == [{
        "category": "mount",
        "backend": "indi",
        "manufacturer": "Sky-Watcher",
        "model": "EQ6-R Pro",
        "serial": "MOUNT12345678",
        "device_name": "EQMod Mount",
        "fallback_physical_path": port,
    }]

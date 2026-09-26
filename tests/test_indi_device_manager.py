from backend.indi_device_manager import IndiDeviceManager


class FakeClient:
    def __init__(self, devices):
        self.devices = devices

    def get_all_devices(self):
        return self.devices


def test_catalog_classifies_indi_devices_and_excludes_gphoto():
    devices = {
        "LX200 OnStep": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_lx200_OnStep",
                "DRIVER_NAME": "LX200 OnStep",
                "DRIVER_INTERFACE": "1",
            },
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB2"},
            "TELESCOPE_MOTION_NS": {
                "MOTION_NORTH": "Off",
                "MOTION_SOUTH": "Off",
            },
        },
        "ZWO EAF": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_asi_focuser",
                "DRIVER_INTERFACE": "8",
            },
            "CONNECTION": {"CONNECT": "Off"},
            "ABS_FOCUS_POSITION": {"FOCUS_ABSOLUTE_POSITION": "7333"},
        },
        "ASI2600MC Pro": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_asi_ccd",
                "DRIVER_INTERFACE": "2",
            },
            "CCD_EXPOSURE": {"CCD_EXPOSURE_VALUE": "0"},
        },
        "Nikon DSLR": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_gphoto_ccd",
                "DRIVER_INTERFACE": "2",
            },
            "CCD_EXPOSURE": {"CCD_EXPOSURE_VALUE": "0"},
        },
    }

    manager = IndiDeviceManager(client=FakeClient(devices))
    catalog = manager.discover()

    names = {entry["device_name"] for entry in catalog}
    assert names == {"LX200 OnStep", "ZWO EAF", "ASI2600MC Pro"}

    by_name = {entry["device_name"]: entry for entry in catalog}
    assert "mount" in by_name["LX200 OnStep"]["categories"]
    assert "focuser" in by_name["ZWO EAF"]["categories"]
    assert "astro_camera" in by_name["ASI2600MC Pro"]["categories"]

    assert by_name["LX200 OnStep"]["backend"] == "indi"
    assert by_name["LX200 OnStep"]["device_id"] == (
        "indi:127.0.0.1:7624:LX200 OnStep"
    )
    assert by_name["LX200 OnStep"]["fallback_physical_path"] is None


def test_catalog_projects_only_current_rig_categories():
    catalog = [
        {
            "backend": "indi",
            "device_name": "Mount A",
            "device_id": "indi:127.0.0.1:7624:Mount A",
            "categories": ["mount"],
            "present": True,
        },
        {
            "backend": "indi",
            "device_name": "Focuser A",
            "device_id": "indi:127.0.0.1:7624:Focuser A",
            "categories": ["focuser"],
            "present": True,
        },
        {
            "backend": "indi",
            "device_name": "CCD A",
            "device_id": "indi:127.0.0.1:7624:CCD A",
            "categories": ["astro_camera"],
            "present": True,
        },
    ]

    mounts = IndiDeviceManager.inventory_entries(catalog, "mount")
    focusers = IndiDeviceManager.inventory_entries(catalog, "focuser")

    assert [entry["device_name"] for entry in mounts] == ["Mount A"]
    assert [entry["device_name"] for entry in focusers] == ["Focuser A"]
    assert all(entry["pilotable"] is True for entry in mounts + focusers)
    assert IndiDeviceManager.inventory_entries(catalog, "camera") == []


def test_inventory_excludes_loaded_driver_without_live_transport(monkeypatch):
    devices = {
        "EQMod Mount": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_eqmod_telescope",
                "DRIVER_INTERFACE": "1",
            },
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB99"},
        },
    }
    monkeypatch.setattr("backend.indi_device_manager.os.path.exists", lambda _p: False)

    manager = IndiDeviceManager(client=FakeClient(devices))
    catalog = manager.discover()

    assert catalog[0]["present"] is False
    assert IndiDeviceManager.inventory_entries(catalog, "mount") == []


def test_disconnected_serial_default_is_not_physical_presence(monkeypatch):
    devices = {
        "EQMod Mount": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_eqmod_telescope",
                "DRIVER_INTERFACE": "1",
            },
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB7"},
        },
    }
    monkeypatch.setattr("backend.indi_device_manager.os.path.exists", lambda _p: True)
    monkeypatch.setattr(
        "backend.indi_device_manager._stable_serial_path",
        lambda _p: "/dev/serial/by-id/usb-arbitrary-controller",
    )

    manager = IndiDeviceManager(client=FakeClient(devices))
    catalog = manager.discover()

    assert catalog[0]["present"] is False
    assert catalog[0]["fallback_physical_path"] is None
    assert IndiDeviceManager.inventory_entries(catalog, "mount") == []


def test_connected_mount_keeps_stable_transport_without_chipset_assumption(
    monkeypatch,
):
    devices = {
        "EQMod Mount": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_eqmod_telescope",
                "DRIVER_INTERFACE": "1",
            },
            "CONNECTION": {"CONNECT": "On"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB7"},
        },
    }
    monkeypatch.setattr(
        "backend.indi_device_manager._stable_serial_path",
        lambda _p: "/dev/serial/by-id/usb-arbitrary-controller",
    )

    manager = IndiDeviceManager(client=FakeClient(devices))
    catalog = manager.discover()
    mounts = IndiDeviceManager.inventory_entries(catalog, "mount")

    assert catalog[0]["present"] is True
    assert mounts[0]["device_name"] == "EQMod Mount"
    assert mounts[0]["fallback_physical_path"] == (
        "/dev/serial/by-id/usb-arbitrary-controller"
    )



def test_mount_autoconnect_tries_stable_transports_and_stops_on_success(
    monkeypatch,
):
    devices = {
        "EQMod Mount": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_eqmod_telescope",
                "DRIVER_INTERFACE": "1",
            },
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB0"},
        },
    }
    manager = IndiDeviceManager(client=FakeClient(devices))
    monkeypatch.setattr(
        manager,
        "_serial_candidates",
        lambda: ["/dev/serial/by-id/A", "/dev/serial/by-id/B"],
    )
    attempts = []

    def fake_probe(device_name, candidate, **_kwargs):
        attempts.append((device_name, candidate))
        return candidate.endswith("/B")

    monkeypatch.setattr(manager, "_probe_mount_transport", fake_probe)

    assert manager._autoconnect_mounts(devices) is True
    assert attempts == [
        ("EQMod Mount", "/dev/serial/by-id/A"),
        ("EQMod Mount", "/dev/serial/by-id/B"),
    ]


def test_mount_autoconnect_never_reuses_transport_claimed_by_connected_mount(
    monkeypatch,
):
    devices = {
        "Mount A": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "On"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB1"},
        },
        "Mount B": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB0"},
        },
    }
    manager = IndiDeviceManager(client=FakeClient(devices))
    monkeypatch.setattr(
        manager,
        "_serial_candidates",
        lambda: ["/dev/serial/by-id/A", "/dev/serial/by-id/B"],
    )
    monkeypatch.setattr(
        "backend.indi_device_manager._stable_serial_path",
        lambda port: "/dev/serial/by-id/A" if port == "/dev/ttyUSB1" else None,
    )
    attempts = []
    monkeypatch.setattr(
        manager,
        "_probe_mount_transport",
        lambda device, candidate, **_kwargs: attempts.append(
            (device, candidate)
        ) or False,
    )

    assert manager._autoconnect_mounts(devices) is False
    assert attempts == [("Mount B", "/dev/serial/by-id/B")]


def test_mount_transport_probe_disconnects_failed_candidate(monkeypatch):
    calls = []

    class ProbeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs["device"]))

        def start_monitor(self):
            calls.append(("monitor", "start"))

        def stop_monitor(self):
            calls.append(("monitor", "stop"))

        def set_props(self, assignments):
            calls.append(("set", assignments))

        def get_props(self, patterns):
            calls.append(("get", tuple(patterns)))
            return {"CONNECTION": {"CONNECT": "Off", "DISCONNECT": "On"}}

    monkeypatch.setattr(
        "backend.indi_device_manager.IndiSubprocessClient",
        ProbeClient,
    )
    monkeypatch.setattr(
        "backend.indi_device_manager.time.monotonic",
        iter([0.0, 4.0]).__next__,
    )

    manager = IndiDeviceManager(client=FakeClient({}))
    assert manager._probe_mount_transport(
        "EQMod Mount",
        "/dev/serial/by-id/test",
        timeout_s=3.0,
    ) is False

    assert ("monitor", "start") in calls
    assert ("set", {
        "CONNECTION": {"CONNECT": "Off", "DISCONNECT": "On"}
    }) in calls
    assert calls[-1] == ("monitor", "stop")


def test_mount_transport_probe_keeps_success_connected_and_stops_monitor(
    monkeypatch,
):
    calls = []

    class ProbeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs["device"]))

        def start_monitor(self):
            calls.append(("monitor", "start"))

        def stop_monitor(self):
            calls.append(("monitor", "stop"))

        def set_props(self, assignments):
            calls.append(("set", assignments))

        def get_props(self, patterns):
            calls.append(("get", tuple(patterns)))
            return {"CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"}}

    monkeypatch.setattr(
        "backend.indi_device_manager.IndiSubprocessClient",
        ProbeClient,
    )
    monkeypatch.setattr(
        "backend.indi_device_manager.time.monotonic",
        iter([0.0, 0.1]).__next__,
    )

    manager = IndiDeviceManager(client=FakeClient({}))
    assert manager._probe_mount_transport(
        "EQMod Mount",
        "/dev/serial/by-id/test",
        timeout_s=3.0,
    ) is True

    disconnect = ("set", {
        "CONNECTION": {"CONNECT": "Off", "DISCONNECT": "On"}
    })
    assert disconnect not in calls
    assert calls[-1] == ("monitor", "stop")

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
        },
        {
            "backend": "indi",
            "device_name": "Focuser A",
            "device_id": "indi:127.0.0.1:7624:Focuser A",
            "categories": ["focuser"],
        },
        {
            "backend": "indi",
            "device_name": "CCD A",
            "device_id": "indi:127.0.0.1:7624:CCD A",
            "categories": ["astro_camera"],
        },
    ]

    mounts = IndiDeviceManager.inventory_entries(catalog, "mount")
    focusers = IndiDeviceManager.inventory_entries(catalog, "focuser")

    assert [entry["device_name"] for entry in mounts] == ["Mount A"]
    assert [entry["device_name"] for entry in focusers] == ["Focuser A"]
    assert all(entry["pilotable"] is True for entry in mounts + focusers)
    assert IndiDeviceManager.inventory_entries(catalog, "camera") == []

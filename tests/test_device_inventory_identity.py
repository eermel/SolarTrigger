from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from backend import device_inventory
from backend.rig_manager import RigManager


class _ConfigNode:
    def __init__(self, values):
        self.values = values

    def get_child_by_name(self, name):
        if name not in self.values:
            raise KeyError(name)
        return SimpleNamespace(get_value=lambda: self.values[name])


class _Camera:
    metadata_by_port = {}

    @staticmethod
    def autodetect():
        return [("Nikon D850", port) for port in _Camera.metadata_by_port]

    def set_port_info(self, port):
        self.port = port

    def init(self):
        pass

    def get_config(self):
        return _ConfigNode(self.metadata_by_port[self.port])

    def exit(self):
        pass


class _PortInfoList:
    def load(self):
        pass

    def lookup_path(self, port):
        return port

    def __getitem__(self, port):
        return port


def _write_usb_device(
    root: Path, topology: str, bus: int, device: int, serial=None
):
    path = root / topology
    path.mkdir()
    (path / "busnum").write_text(f"{bus}\n", encoding="utf-8")
    (path / "devnum").write_text(f"{device}\n", encoding="utf-8")
    if serial is not None:
        (path / "serial").write_text(f"{serial}\n", encoding="utf-8")
    return path


def _mock_gphoto(monkeypatch, tmp_path, metadata_by_port):
    _Camera.metadata_by_port = metadata_by_port
    gp = SimpleNamespace(Camera=_Camera, PortInfoList=_PortInfoList)
    monkeypatch.setitem(__import__("sys").modules, "gphoto2", gp)
    monkeypatch.setattr(device_inventory, "SYSFS_USB_DEVICES", tmp_path)
    monkeypatch.setattr(device_inventory, "_camera_backend", lambda _model: "nikon")
    monkeypatch.setattr(device_inventory, "_discover_mounts", lambda: [])
    monkeypatch.setattr(device_inventory, "_discover_focusers", lambda: [])


def _config_for_camera(camera):
    return {
        "schema_version": 2,
        "eclipse": {
            "date": "2027-08-02",
            "reference_site": {"lat": 43.6, "lon": 1.44, "alt_m": 150},
            "circumstances": {
                "C1": "08:00:00",
                "C2": "09:00:00",
                "TMAX": "09:01:00",
                "C3": "09:02:00",
                "C4": "10:00:00",
            },
        },
        "sequence": {"common": {}},
        "rigs": [{
            "rig_id": 1,
            "enabled": True,
            "name": "RIG 1",
            "devices": {"camera": camera, "mount": None, "focuser": None},
            "optics": {},
            "photo": {},
        }],
    }


def test_usb_serial_is_preferred_over_protocol_serial(monkeypatch, tmp_path):
    _write_usb_device(tmp_path, "1-2", 1, 6, "USB-A")
    _mock_gphoto(monkeypatch, tmp_path, {
        "usb:001,006": {"cameramodel": "Nikon D850", "serialnumber": "PROTO-A"}
    })

    camera = device_inventory.refresh_inventory()["camera"][0]

    assert camera["serial"] == "USB-A"
    assert camera["transport_locator"] == "usb:001,006"


def test_usb_serial_is_used_without_protocol_serial(monkeypatch, tmp_path):
    _write_usb_device(tmp_path, "1-2", 1, 6, "USB-A")
    _mock_gphoto(monkeypatch, tmp_path, {
        "usb:001,006": {"cameramodel": "Nikon D850"}
    })

    camera = device_inventory.refresh_inventory()["camera"][0]

    assert camera["serial"] == "USB-A"
    assert camera["fallback_physical_path"] is None


def test_physical_path_fallback_is_used_with_identity_warning(monkeypatch, tmp_path):
    _write_usb_device(tmp_path, "1-2.3", 1, 6)
    _mock_gphoto(monkeypatch, tmp_path, {
        "usb:001,006": {"cameramodel": "Nikon D850"}
    })

    camera = device_inventory.refresh_inventory()["camera"][0]
    persisted_binding = {
        key: camera[key]
        for key in ("backend", "model", "serial", "fallback_physical_path")
    }
    manager = RigManager.from_config(_config_for_camera(persisted_binding))

    assert camera["serial"] is None
    assert camera["fallback_physical_path"] == "sysfs-usb:1-2.3"
    assert manager.identity_warnings == [
        "RIG 1 camera: using fallback physical path as identity; "
        "prefer a stable serial"
    ]


def test_two_d850_cameras_have_distinct_stable_entries(monkeypatch, tmp_path):
    _write_usb_device(tmp_path, "1-2", 1, 6, "USB-A")
    _write_usb_device(tmp_path, "1-3", 1, 7, "USB-B")
    _mock_gphoto(monkeypatch, tmp_path, {
        "usb:001,006": {"cameramodel": "Nikon D850"},
        "usb:001,007": {"cameramodel": "Nikon D850"},
    })

    cameras = device_inventory.refresh_inventory()["camera"]

    assert [(camera["model"], camera["serial"]) for camera in cameras] == [
        ("Nikon D850", "USB-A"),
        ("Nikon D850", "USB-B"),
    ]
    assert cameras[0]["display_label"] != cameras[1]["display_label"]


def test_reconnect_at_new_usb_address_keeps_camera_identity(monkeypatch, tmp_path):
    device = _write_usb_device(tmp_path, "2-4.1", 1, 6, "USB-STABLE")
    _mock_gphoto(monkeypatch, tmp_path, {
        "usb:001,006": {"cameramodel": "Nikon D850"}
    })
    first = device_inventory.refresh_inventory()["camera"][0]

    (device / "busnum").write_text("3\n", encoding="utf-8")
    (device / "devnum").write_text("12\n", encoding="utf-8")
    _Camera.metadata_by_port = {
        "usb:003,012": {"cameramodel": "Nikon D850"}
    }
    second = device_inventory.refresh_inventory()["camera"][0]

    assert first["serial"] == second["serial"] == "USB-STABLE"
    assert first["transport_locator"] == "usb:001,006"
    assert second["transport_locator"] == "usb:003,012"
    assert {**deepcopy(first), "transport_locator": None} == {
        **deepcopy(second), "transport_locator": None
    }


def test_camera_without_supported_plugin_is_not_bindable():
    cameras = device_inventory._normalize_entries(
        "camera",
        [
            {
                "backend": "sony",
                "model": "Sony ILCE-7M5 (PC Control)",
                "serial": "A7V-SERIAL",
            },
            {
                "backend": "nikon-dslr",
                "model": "Nikon DSC D850",
                "serial": "D850-SERIAL",
            },
            {
                "backend": "gphoto2",
                "model": "Sony Alpha-A6600 (PC Control)",
                "serial": "A6600-SERIAL",
            },
        ],
    )

    assert cameras[0]["bindable"] is True
    assert cameras[1]["bindable"] is True
    assert cameras[2]["bindable"] is False

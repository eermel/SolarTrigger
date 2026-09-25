from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import device_inventory
from backend import devices


def _mock_discovery(monkeypatch):
    gphoto = SimpleNamespace(
        Camera=SimpleNamespace(
            autodetect=lambda: [
                ("Canon EOS R", "usb:001,006"),
                ("Canon EOS R", "usb:001,007"),
                ("Canon EOS R", "usb:001,008"),
                ("Canon EOS R", "usb:001,009"),
            ]
        )
    )
    metadata = {
        "usb:001,006": {"manufacturer": "Canon", "model": "EOS R", "serial": "ABCD1234"},
        "usb:001,007": {"manufacturer": "Canon", "model": "EOS R", "serial": "WXYZ1234"},
        "usb:001,008": {"manufacturer": "Canon", "model": "EOS R", "serial": None},
        "usb:001,009": {"manufacturer": "Canon", "model": "EOS R", "serial": None},
    }
    usb_identity = {
        "usb:001,006": {"serial": None, "physical_path": None},
        "usb:001,007": {"serial": None, "physical_path": None},
        "usb:001,008": {"serial": None, "physical_path": "sysfs-usb:1-2.3"},
        "usb:001,009": {"serial": None, "physical_path": None},
    }
    monkeypatch.setitem(__import__("sys").modules, "gphoto2", gphoto)
    monkeypatch.setattr(
        device_inventory,
        "_read_gphoto_metadata",
        lambda _provider, port: metadata[port],
    )
    monkeypatch.setattr(device_inventory, "_usb_identity", lambda port: usb_identity[port])
    monkeypatch.setattr(device_inventory, "_camera_backend", lambda _model: "gphoto2")
    monkeypatch.setattr(device_inventory, "_discover_mounts", lambda: [])
    monkeypatch.setattr(device_inventory, "_discover_focusers", lambda: [])


def test_refresh_inventory_disambiguates_stable_serials_and_bindable(monkeypatch):
    _mock_discovery(monkeypatch)

    cameras = device_inventory.refresh_inventory()["camera"]

    assert [camera["display_label"] for camera in cameras] == [
        "Canon EOS R · D1234",
        "Canon EOS R · Z1234",
        "Canon EOS R",
        "Canon EOS R",
    ]
    assert [camera["bindable"] for camera in cameras] == [True, True, True, False]
    assert cameras[2]["fallback_physical_path"] == "sysfs-usb:1-2.3"
    assert all("usb:" not in camera["display_label"] for camera in cameras)


def test_reserved_mount_is_kept_without_reprobing_owned_serial_path(
    monkeypatch, tmp_path
):
    stable_path = tmp_path / "usb-OnStep-controller"
    stable_path.touch()

    monkeypatch.setattr(device_inventory, "_discover_cameras", lambda: [])
    monkeypatch.setattr(device_inventory, "_discover_focusers", lambda: [])

    captured = {}
    from plugins import mount as mount_registry

    def fake_inventory_mounts(*, log_fn, exclude_physical_paths):
        captured["excluded"] = set(exclude_physical_paths)
        return []

    monkeypatch.setattr(
        mount_registry,
        "inventory_mounts",
        fake_inventory_mounts,
    )

    binding = {
        "category": "mount",
        "backend": "onstep",
        "manufacturer": "OnStep",
        "model": "On-Step",
        "fallback_physical_path": str(stable_path),
    }

    inventory = device_inventory.refresh_inventory(
        reserved_mounts=[binding],
    )

    assert captured["excluded"] == {str(stable_path)}
    assert len(inventory["mount"]) == 1
    assert inventory["mount"][0]["backend"] == "onstep"
    assert inventory["mount"][0]["fallback_physical_path"] == str(stable_path)
    assert inventory["mount"][0]["present"] is True
    assert inventory["mount"][0]["bindable"] is True


def test_reserved_indi_binding_blocks_direct_fallback_on_catalog_gap(
    monkeypatch,
):
    from plugins import mount as mount_registry
    from plugins import focuser as focuser_registry

    def unexpected_mount_probe(**_kwargs):
        raise AssertionError("direct mount fallback must not probe")

    def unexpected_focuser_probe(**_kwargs):
        raise AssertionError("direct focuser fallback must not probe")

    monkeypatch.setattr(
        mount_registry,
        "inventory_mounts",
        unexpected_mount_probe,
    )
    monkeypatch.setattr(
        focuser_registry,
        "inventory_focusers",
        unexpected_focuser_probe,
    )

    mount_binding = {
        "category": "mount",
        "backend": "indi",
        "device_name": "LX200 OnStep",
        "device_id": "indi:127.0.0.1:7624:LX200 OnStep",
    }
    focuser_binding = {
        "category": "focuser",
        "backend": "indi",
        "device_name": "ZWO EAF",
        "device_id": "indi:127.0.0.1:7624:ZWO EAF",
    }

    assert device_inventory._discover_mounts(
        reserved_mounts=[mount_binding],
        indi_catalog=[],
    ) == []
    assert device_inventory._discover_focusers(
        reserved_focusers=[focuser_binding],
        indi_catalog=[],
    ) == []


def test_reserved_indi_binding_presence_comes_from_current_catalog():
    mount_binding = {
        "category": "mount",
        "backend": "indi",
        "device_name": "LX200 OnStep",
        "device_id": "indi:127.0.0.1:7624:LX200 OnStep",
    }
    catalog_entry = {
        "backend": "indi",
        "device_name": "LX200 OnStep",
        "device_id": "indi:127.0.0.1:7624:LX200 OnStep",
        "model": "LX200 OnStep",
        "categories": ["mount"],
        "present": True,
    }

    discovered = device_inventory._discover_mounts(
        reserved_mounts=[mount_binding],
        indi_catalog=[catalog_entry],
    )

    assert len(discovered) == 1
    assert discovered[0]["device_id"] == mount_binding["device_id"]
    assert discovered[0]["present"] is True


def test_cached_inventory_returns_last_refresh_without_probing(monkeypatch):
    _mock_discovery(monkeypatch)
    refreshed = device_inventory.refresh_inventory()
    monkeypatch.setattr(
        device_inventory,
        "_discover_cameras",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected probe")),
    )

    cached = device_inventory.get_cached_inventory()

    assert cached == refreshed
    cached["camera"].clear()
    assert device_inventory.get_cached_inventory() == refreshed


class _ConfigNode:
    def __init__(self, values):
        self.values = values

    def get_child_by_name(self, name):
        if name not in self.values:
            raise KeyError(name)
        return SimpleNamespace(get_value=lambda: self.values[name])


class _MockCamera:
    metadata_by_port = {}

    def set_port_info(self, port):
        self.port = port

    def init(self):
        pass

    def get_config(self):
        return _ConfigNode(self.metadata_by_port[self.port])

    def exit(self):
        pass


class _MockPortInfoList:
    def load(self):
        pass

    def lookup_path(self, port):
        return port

    def __getitem__(self, port):
        return port


def _write_usb_device(root: Path, topology: str, bus: int, device: int, serial=None):
    usb_device = root / topology
    usb_device.mkdir()
    (usb_device / "busnum").write_text(f"{bus}\n", encoding="utf-8")
    (usb_device / "devnum").write_text(f"{device}\n", encoding="utf-8")
    if serial is not None:
        (usb_device / "serial").write_text(f"{serial}\n", encoding="utf-8")


def test_camera_usb_serial_precedes_protocol_serial(monkeypatch, tmp_path):
    _write_usb_device(tmp_path, "1-2", 1, 6, "USB-SERIAL-A")
    _write_usb_device(tmp_path, "1-3", 1, 7, "USB-SERIAL-B")
    _MockCamera.metadata_by_port = {
        "usb:001,006": {"manufacturer": "Nikon", "cameramodel": "Nikon D850", "serialnumber": "SDK-A"},
        "usb:001,007": {"manufacturer": "Nikon", "cameramodel": "Nikon D850", "serialnumber": "SDK-B"},
    }
    gp = SimpleNamespace(
        Camera=_MockCamera,
        PortInfoList=_MockPortInfoList,
    )
    gp.Camera.autodetect = lambda: [
        ("Nikon D850", "usb:001,006"),
        ("Nikon D850", "usb:001,007"),
    ]
    monkeypatch.setitem(__import__("sys").modules, "gphoto2", gp)
    monkeypatch.setattr(device_inventory, "SYSFS_USB_DEVICES", tmp_path)
    monkeypatch.setattr(device_inventory, "_camera_backend", lambda _model: "nikon")
    monkeypatch.setattr(device_inventory, "_discover_mounts", lambda: [])
    monkeypatch.setattr(device_inventory, "_discover_focusers", lambda: [])

    cameras = device_inventory.refresh_inventory()["camera"]

    assert [camera["serial"] for camera in cameras] == [
        "USB-SERIAL-A",
        "USB-SERIAL-B",
    ]
    assert [camera["transport_locator"] for camera in cameras] == [
        "usb:001,006",
        "usb:001,007",
    ]


def test_sysfs_topology_identity_survives_usb_address_change(monkeypatch, tmp_path):
    _write_usb_device(tmp_path, "2-4.1", 2, 9)
    monkeypatch.setattr(device_inventory, "SYSFS_USB_DEVICES", tmp_path)

    first = device_inventory._usb_identity("usb:002,009")
    (tmp_path / "2-4.1" / "busnum").write_text("3\n", encoding="utf-8")
    (tmp_path / "2-4.1" / "devnum").write_text("12\n", encoding="utf-8")
    second = device_inventory._usb_identity("usb:003,012")

    assert first == {"serial": None, "physical_path": "sysfs-usb:2-4.1"}
    assert second == first


@pytest.mark.parametrize("detected_info", [None, "", []])
def test_presence_only_mount_and_absent_focuser_return_one_and_zero(
    monkeypatch, detected_info
):
    monkeypatch.setattr(device_inventory, "_discover_cameras", lambda: [])
    monkeypatch.setattr(
        devices,
        "detect_mount",
        lambda: {
            "detected": True,
            "detected_info": detected_info,
            "suggested_plugin": "indi_mount",
        },
    )
    monkeypatch.setattr(
        devices,
        "detect_focuser",
        lambda: {"detected": False, "detected_info": "camera-only"},
    )

    inventory = device_inventory.refresh_inventory()

    assert inventory["camera"] == []
    assert inventory["focuser"] == []
    assert len(inventory["mount"]) == 1
    assert inventory["mount"][0]["backend"] == "indi_mount"
    assert inventory["mount"][0]["bindable"] is False


def test_mount_and_focuser_identities_are_normalized_and_separated(monkeypatch):
    monkeypatch.setattr(
        device_inventory,
        "_discover_cameras",
        lambda: [{"category": "camera", "backend": "gphoto2", "model": "EOS R"}],
    )
    monkeypatch.setattr(
        devices,
        "detect_mount",
        lambda: {
            "detected": True,
            "suggested_plugin": "indi_mount",
            "detected_info": [
                {"category": "camera", "usb_serial": "CAMERA-SERIAL"},
                {
                    "category": "mount",
                    "usb_serial": "MOUNT-SERIAL",
                    "physical_path": "pci-0000:00:14.0-usb-0:2",
                    "indi_device_name": "EQMod Mount",
                },
            ],
        },
    )
    from plugins import focuser as focuser_registry

    monkeypatch.setattr(
        focuser_registry,
        "inventory_focusers",
        lambda log_fn=None: [
            {
                "category": "focuser",
                "backend": "indi_focuser",
                "device_name": "MoonLite Focuser",
            }
        ],
    )

    inventory = device_inventory.refresh_inventory()

    assert [entry["category"] for entry in inventory["camera"]] == ["camera"]
    assert [entry["category"] for entry in inventory["mount"]] == ["mount"]
    assert [entry["category"] for entry in inventory["focuser"]] == ["focuser"]
    mount = inventory["mount"][0]
    assert mount["serial"] == "MOUNT-SERIAL"
    assert mount["fallback_physical_path"] == "pci-0000:00:14.0-usb-0:2"
    assert mount["device_name"] == "EQMod Mount"
    assert mount["bindable"] is True
    assert inventory["focuser"][0]["device_name"] == "MoonLite Focuser"
    assert inventory["focuser"][0]["bindable"] is False


def test_non_mapping_provider_value_and_transient_usb_are_not_stable(monkeypatch):
    monkeypatch.setattr(device_inventory, "_discover_cameras", lambda: [])
    monkeypatch.setattr(
        devices,
        "detect_mount",
        lambda: {
            "detected": True,
            "detected_info": "lx200",
            "suggested_plugin": None,
        },
    )
    from plugins import focuser as focuser_registry

    monkeypatch.setattr(
        focuser_registry,
        "inventory_focusers",
        lambda log_fn=None: [
            {
                "category": "focuser",
                "backend": "indi_focuser",
                "usb_serial": "usb:002,009",
            }
        ],
    )

    inventory = device_inventory.refresh_inventory()

    assert inventory["mount"][0]["backend"] == "lx200"
    assert inventory["mount"][0]["model"] == "lx200"
    assert inventory["mount"][0]["bindable"] is False
    assert inventory["focuser"][0]["serial"] is None
    assert inventory["focuser"][0]["bindable"] is False


def test_focuser_device_id_is_normalized_and_bindable(monkeypatch):
    monkeypatch.setattr(device_inventory, "_discover_cameras", lambda: [])
    monkeypatch.setattr(device_inventory, "_discover_mounts", lambda: [])
    monkeypatch.setattr(
        device_inventory,
        "_discover_focusers",
        lambda: [{
            "backend": "zwo_eaf",
            "manufacturer": "ZWO",
            "model": "EAF",
            "device_id": "zwo_eaf:0",
        }],
    )

    inventory = device_inventory.refresh_inventory()

    focuser = inventory["focuser"][0]
    assert focuser["device_id"] == "zwo_eaf:0"
    assert focuser["serial"] is None
    assert focuser["bindable"] is True


def test_refresh_with_usb_serial_does_not_probe_camera_protocol(
    monkeypatch, tmp_path
):
    _write_usb_device(tmp_path, "1-2", 1, 6, "USB-STABLE")

    gp = SimpleNamespace(
        Camera=SimpleNamespace(
            autodetect=lambda: [
                ("Sony ILCE-7M5 (PC Control)", "usb:001,006"),
            ]
        )
    )

    monkeypatch.setitem(__import__("sys").modules, "gphoto2", gp)
    monkeypatch.setattr(device_inventory, "SYSFS_USB_DEVICES", tmp_path)
    monkeypatch.setattr(
        device_inventory,
        "_read_gphoto_metadata",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("protocol metadata must not be probed")
        ),
    )
    monkeypatch.setattr(
        device_inventory,
        "_camera_backend",
        lambda _model: "sony",
    )
    monkeypatch.setattr(device_inventory, "_discover_mounts", lambda: [])
    monkeypatch.setattr(device_inventory, "_discover_focusers", lambda: [])

    camera = device_inventory.refresh_inventory()["camera"][0]

    assert camera["serial"] == "USB-STABLE"
    assert camera["model"] == "Sony ILCE-7M5 (PC Control)"
    assert camera["transport_locator"] == "usb:001,006"



def test_mount_and_focuser_labels_always_include_serial_suffix():
    entries = [
        {
            "category": "mount",
            "manufacturer": "Sky-Watcher",
            "model": "EQ6-R Pro",
            "serial": "MOUNT12345678",
        },
        {
            "category": "focuser",
            "manufacturer": "ZWO",
            "model": "EAF",
            "serial": "EAFABC9876",
        },
    ]

    device_inventory.build_display_labels(entries)

    assert entries[0]["display_label"] == "Sky-Watcher EQ6-R Pro - 5678"
    assert entries[1]["display_label"] == "ZWO EAF - 9876"


def test_reclassify_cached_camera_after_characterization_does_not_probe(monkeypatch):
    original = {
        "camera": [{
            "category": "camera",
            "backend": "gphoto2",
            "pilotable": False,
            "manufacturer": "Sony",
            "model": "Sony Test Camera",
            "serial": "SERIAL-1",
            "device_id": None,
            "fallback_physical_path": None,
            "present": True,
            "transport_locator": "usb:001,002",
            "bindable": True,
            "display_label": "Sony Test Camera",
        }],
        "mount": [],
        "focuser": [],
    }
    with device_inventory._cache_lock:
        device_inventory._cache.clear()
        device_inventory._cache.update(original)

    monkeypatch.setattr(
        device_inventory,
        "_discover_cameras",
        lambda: (_ for _ in ()).throw(AssertionError("unexpected camera probe")),
    )
    from backend import camera_profiles
    monkeypatch.setattr(
        camera_profiles,
        "profile_for_model",
        lambda model: (
            {"backend": "profile-sony-test-camera"}
            if model == "Sony Test Camera"
            else None
        ),
    )

    updated = device_inventory.reclassify_cached_cameras()

    assert updated["camera"][0]["backend"] == "profile-sony-test-camera"
    assert updated["camera"][0]["pilotable"] is True
    assert updated["camera"][0]["transport_locator"] == "usb:001,002"

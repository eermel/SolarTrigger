from types import SimpleNamespace

from backend import device_inventory


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
        "usb:001,008": {"serial": None, "physical_path": "1-2.3"},
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
    assert [camera["bindable"] for camera in cameras] == [True, True, False, False]
    assert cameras[2]["fallback_physical_path"] == "1-2.3"
    assert all("usb:" not in camera["display_label"] for camera in cameras)


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

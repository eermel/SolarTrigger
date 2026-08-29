from plugins.focuser import zwo_eaf


class FakeSdk:
    def __init__(self, ids=(), *, serial_supported=False):
        self.ids = list(ids)
        self.serial_supported = serial_supported
        self.open_calls = []
        self.close_calls = []

    def EAFGetNum(self):
        return len(self.ids)

    def EAFGetID(self, index, output):
        output._obj.value = self.ids[index]
        return 0

    def EAFOpen(self, sdk_id):
        if sdk_id not in self.ids:
            return 1
        self.open_calls.append(sdk_id)
        return 0

    def EAFClose(self, sdk_id):
        self.close_calls.append(sdk_id)
        return 0

    def EAFStop(self, _sdk_id):
        return 0

    def EAFGetProperty(self, sdk_id, output):
        output._obj.ID = sdk_id
        output._obj.Name = b"EAF"
        output._obj.MaxStep = 60000
        return 0

    def EAFGetSerialNumber(self, sdk_id, output):
        if not self.serial_supported:
            return 8

        value = sdk_id + 1
        for index in range(8):
            output._obj.id[index] = value
        return 0


def make_driver(monkeypatch, sdk):
    monkeypatch.setattr(zwo_eaf, "_load_lib", lambda: sdk)
    monkeypatch.setattr(
        zwo_eaf.ZwoEaf,
        "_setup_prototypes",
        lambda self: None,
    )
    return zwo_eaf.ZwoEaf()


def test_no_eaf_returns_empty_inventory(monkeypatch):
    sdk = FakeSdk()
    driver = make_driver(monkeypatch, sdk)

    assert driver.enumerate_devices() == []
    assert sdk.open_calls == []


def test_old_eaf_without_serial_uses_device_id(monkeypatch):
    sdk = FakeSdk(ids=(0,))
    driver = make_driver(monkeypatch, sdk)

    devices = driver.enumerate_devices()

    assert len(devices) == 1
    device = devices[0]

    assert device["backend"] == "zwo_eaf"
    assert device["manufacturer"] == "ZWO"
    assert device["model"] == "EAF"
    assert device["serial"] is None
    assert device["device_id"] == "zwo_eaf:0"
    assert device["sdk_id"] == 0
    assert device["max_step"] == 60000

    assert sdk.open_calls == [0]
    assert sdk.close_calls == [0]


def test_four_eafs_have_distinct_device_ids(monkeypatch):
    sdk = FakeSdk(ids=(0, 3, 7, 12))
    driver = make_driver(monkeypatch, sdk)

    devices = driver.enumerate_devices()

    assert [device["device_id"] for device in devices] == [
        "zwo_eaf:0",
        "zwo_eaf:3",
        "zwo_eaf:7",
        "zwo_eaf:12",
    ]

    assert sdk.open_calls == [0, 3, 7, 12]
    assert sdk.close_calls == [0, 3, 7, 12]


def test_supported_serial_is_exposed(monkeypatch):
    sdk = FakeSdk(ids=(3,), serial_supported=True)
    driver = make_driver(monkeypatch, sdk)

    devices = driver.enumerate_devices()

    assert len(devices) == 1
    assert devices[0]["serial"] == "0404040404040404"
    assert devices[0]["device_id"] == "zwo_eaf:3"


def test_connect_explicit_device_id_does_not_use_index_zero(monkeypatch):
    sdk = FakeSdk(ids=(0, 7))
    driver = make_driver(monkeypatch, sdk)

    info = driver.connect(device_id="zwo_eaf:7")

    assert driver.id == 7
    assert info["device_id"] == "zwo_eaf:7"
    assert sdk.open_calls == [7]

    driver.disconnect()

    assert sdk.close_calls == [7]


def test_plugin_passes_bound_device_id_to_driver(monkeypatch):
    from plugins.focuser import zwo_plugin

    calls = []

    class FakeEaf:
        def __init__(self):
            self.connected = False

        def connect(self, index=0, device_id=None):
            calls.append(device_id)
            self.connected = True
            return {
                "id": 7,
                "device_id": "zwo_eaf:7",
                "serial": None,
                "name": "EAF",
                "max_step": 60000,
            }

        def disconnect(self):
            self.connected = False

    monkeypatch.setattr(zwo_plugin, "ZwoEaf", FakeEaf)

    plugin = zwo_plugin.ZwoFocuser(
        log_fn=lambda *_args: None,
        config={"device_id": "zwo_eaf:7"},
    )

    plugin.connect()

    assert calls == ["zwo_eaf:7"]


def test_focuser_registry_returns_all_zwo_instances(monkeypatch):
    from plugins import focuser as focuser_registry
    from plugins.focuser import zwo_plugin

    expected = [
        {
            "category": "focuser",
            "backend": "zwo_eaf",
            "manufacturer": "ZWO",
            "model": "EAF",
            "serial": None,
            "device_id": "zwo_eaf:0",
            "sdk_id": 0,
            "max_step": 60000,
        },
        {
            "category": "focuser",
            "backend": "zwo_eaf",
            "manufacturer": "ZWO",
            "model": "EAF",
            "serial": None,
            "device_id": "zwo_eaf:7",
            "sdk_id": 7,
            "max_step": 60000,
        },
    ]

    monkeypatch.setattr(
        zwo_plugin.ZwoFocuser,
        "inventory",
        staticmethod(lambda config=None: expected),
    )

    inventory = focuser_registry.inventory_focusers(
        candidates=["zwo_eaf"],
        log_fn=lambda *_args: None,
    )

    assert inventory == expected


def test_device_inventory_refresh_uses_multi_instance_focuser_inventory(monkeypatch):
    from backend import device_inventory
    from plugins import focuser as focuser_registry

    monkeypatch.setattr(device_inventory, "_discover_cameras", lambda: [])
    monkeypatch.setattr(device_inventory, "_discover_mounts", lambda: [])
    monkeypatch.setattr(
        focuser_registry,
        "inventory_focusers",
        lambda log_fn=None: [
            {
                "backend": "zwo_eaf",
                "manufacturer": "ZWO",
                "model": "EAF",
                "device_id": "zwo_eaf:0",
            },
            {
                "backend": "zwo_eaf",
                "manufacturer": "ZWO",
                "model": "EAF",
                "device_id": "zwo_eaf:7",
            },
        ],
    )

    inventory = device_inventory.refresh_inventory()

    assert len(inventory["focuser"]) == 2
    assert [item["device_id"] for item in inventory["focuser"]] == [
        "zwo_eaf:0",
        "zwo_eaf:7",
    ]
    assert all(item["bindable"] for item in inventory["focuser"])

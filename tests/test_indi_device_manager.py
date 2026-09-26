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



def test_stale_connected_by_id_transport_is_not_present(monkeypatch):
    devices = {
        "LX200 OnStep": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_lx200_OnStep",
                "DRIVER_INTERFACE": "1",
            },
            "CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"},
            "DEVICE_PORT": {
                "PORT": "/dev/serial/by-id/usb-unplugged-controller"
            },
        },
    }
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.exists",
        lambda path: path != "/dev/serial/by-id/usb-unplugged-controller",
    )

    manager = IndiDeviceManager(client=FakeClient(devices))
    catalog = manager.discover()

    assert catalog[0]["connected"] is True
    assert catalog[0]["present"] is False
    assert catalog[0]["fallback_physical_path"] is None
    assert IndiDeviceManager.inventory_entries(catalog, "mount") == []


def test_connected_existing_by_id_transport_remains_present(monkeypatch):
    devices = {
        "EQMod Mount": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_eqmod_telescope",
                "DRIVER_INTERFACE": "1",
            },
            "CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"},
            "DEVICE_PORT": {
                "PORT": "/dev/serial/by-id/usb-existing-controller"
            },
        },
    }
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.exists",
        lambda _path: True,
    )

    manager = IndiDeviceManager(client=FakeClient(devices))
    catalog = manager.discover()

    assert catalog[0]["connected"] is True
    assert catalog[0]["present"] is True
    assert catalog[0]["fallback_physical_path"] == (
        "/dev/serial/by-id/usb-existing-controller"
    )



def test_heuristic_serial_switch_value_is_not_physical_identity(monkeypatch):
    devices = {
        "EQMod Mount": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_eqmod_telescope",
                "DRIVER_INTERFACE": "1",
            },
            "CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/serial/by-id/usb-controller"},
            "SERIAL_CONNECTION": {"SERIAL": "On"},
        },
    }
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.exists",
        lambda _path: True,
    )

    entry = IndiDeviceManager(client=FakeClient(devices)).discover()[0]

    assert entry["serial"] is None


def test_explicit_hardware_serial_rejects_switch_like_value(monkeypatch):
    devices = {
        "EQMod Mount": {
            "DRIVER_INFO": {
                "DRIVER_EXEC": "indi_eqmod_telescope",
                "DRIVER_INTERFACE": "1",
            },
            "CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/serial/by-id/usb-controller"},
            "DEVICE_INFO": {"SERIAL": "On"},
        },
    }
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.exists",
        lambda _path: True,
    )

    entry = IndiDeviceManager(client=FakeClient(devices)).discover()[0]

    assert entry["serial"] is None



def test_serial_candidates_exclude_os_reserved_gps_transport(monkeypatch):
    manager = IndiDeviceManager(client=FakeClient({}))
    root = "/dev/serial/by-id"
    gps = f"{root}/usb-gps"
    mount = f"{root}/usb-mount"

    monkeypatch.setattr(
        "backend.indi_device_manager.os.listdir",
        lambda path: ["usb-gps", "usb-mount"] if path == root else [],
    )
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.exists",
        lambda path: path in {"/dev/gps0", gps, mount},
    )

    realpaths = {
        "/dev/gps0": "/dev/ttyUSB0",
        gps: "/dev/ttyUSB0",
        mount: "/dev/ttyUSB1",
    }
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.realpath",
        lambda path: realpaths.get(path, path),
    )

    assert manager._serial_candidates() == [mount]


def test_serial_candidates_do_not_guess_roles_from_usb_chipset(monkeypatch):
    manager = IndiDeviceManager(client=FakeClient({}))
    root = "/dev/serial/by-id"
    candidates = [
        f"{root}/usb-FTDI-controller",
        f"{root}/usb-1a86-controller",
        f"{root}/usb-Prolific-controller",
    ]

    monkeypatch.setattr(
        "backend.indi_device_manager.os.listdir",
        lambda path: [path.rsplit("/", 1)[-1] for path in candidates]
        if path == root
        else [],
    )
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.exists",
        lambda path: path in candidates,
    )
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.realpath",
        lambda path: path,
    )

    # Candidate order is deterministic (sorted by stable by-id name), but
    # no USB chipset/vendor token is used to include or exclude a transport.
    assert manager._serial_candidates() == sorted(candidates)


def test_mount_autoconnect_never_probes_reserved_gps_candidate(monkeypatch):
    devices = {
        "Mount A": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB0"},
        },
    }
    manager = IndiDeviceManager(client=FakeClient(devices))
    root = "/dev/serial/by-id"
    gps = f"{root}/usb-gps"
    mount = f"{root}/usb-mount"

    monkeypatch.setattr(
        "backend.indi_device_manager.os.listdir",
        lambda path: ["usb-gps", "usb-mount"] if path == root else [],
    )
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.exists",
        lambda path: path in {"/dev/gps0", gps, mount},
    )
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.realpath",
        lambda path: {
            "/dev/gps0": "/dev/ttyUSB0",
            gps: "/dev/ttyUSB0",
            mount: "/dev/ttyUSB1",
        }.get(path, path),
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
    assert attempts == [("Mount A", mount)]



def test_learned_mount_binding_is_tried_before_other_candidates(
    monkeypatch,
    tmp_path,
):
    bindings_file = tmp_path / "indi_mount_bindings.json"
    bindings_file.write_text(
        '{"version":1,"bindings":{"Mount A":"/dev/serial/by-id/B"}}\n',
        encoding="utf-8",
    )
    devices = {
        "Mount A": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB0"},
        },
    }
    manager = IndiDeviceManager(
        client=FakeClient(devices),
        bindings_file=bindings_file,
    )
    monkeypatch.setattr(
        manager,
        "_serial_candidates",
        lambda: ["/dev/serial/by-id/A", "/dev/serial/by-id/B"],
    )
    attempts = []
    monkeypatch.setattr(
        manager,
        "_probe_mount_transport",
        lambda device, candidate, **_kwargs: attempts.append(
            (device, candidate)
        ) or candidate.endswith("/B"),
    )
    monkeypatch.setattr(
        "backend.indi_device_manager._stable_serial_path",
        lambda path: path,
    )

    assert manager._autoconnect_mounts(devices) is True
    assert attempts == [("Mount A", "/dev/serial/by-id/B")]


def test_successful_mount_probe_persists_stable_binding(monkeypatch, tmp_path):
    bindings_file = tmp_path / "indi_mount_bindings.json"
    devices = {
        "Mount A": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB0"},
        },
    }
    manager = IndiDeviceManager(
        client=FakeClient(devices),
        bindings_file=bindings_file,
    )
    monkeypatch.setattr(
        manager,
        "_serial_candidates",
        lambda: ["/dev/serial/by-id/MOUNT-A"],
    )
    monkeypatch.setattr(
        manager,
        "_probe_mount_transport",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "backend.indi_device_manager._stable_serial_path",
        lambda path: path,
    )

    assert manager._autoconnect_mounts(devices) is True

    import json
    payload = json.loads(bindings_file.read_text(encoding="utf-8"))
    assert payload == {
        "version": 1,
        "bindings": {"Mount A": "/dev/serial/by-id/MOUNT-A"},
    }


def test_missing_learned_transport_fails_closed_without_scanning_others(
    monkeypatch,
    tmp_path,
):
    bindings_file = tmp_path / "indi_mount_bindings.json"
    bindings_file.write_text(
        '{"version":1,"bindings":{"Mount A":"/dev/serial/by-id/OLD"}}\n',
        encoding="utf-8",
    )
    devices = {
        "Mount A": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB0"},
        },
    }
    manager = IndiDeviceManager(
        client=FakeClient(devices),
        bindings_file=bindings_file,
    )
    monkeypatch.setattr(
        manager,
        "_serial_candidates",
        lambda: ["/dev/serial/by-id/UNRELATED"],
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
    assert attempts == []


def test_failed_learned_transport_does_not_probe_other_serial_devices(
    monkeypatch,
    tmp_path,
):
    bindings_file = tmp_path / "indi_mount_bindings.json"
    learned = "/dev/serial/by-id/MOUNT-A"
    unrelated = "/dev/serial/by-id/UNRELATED"
    bindings_file.write_text(
        '{"version":1,"bindings":{"Mount A":"/dev/serial/by-id/MOUNT-A"}}\n',
        encoding="utf-8",
    )
    devices = {
        "Mount A": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB0"},
        },
    }
    manager = IndiDeviceManager(
        client=FakeClient(devices),
        bindings_file=bindings_file,
    )
    monkeypatch.setattr(
        manager,
        "_serial_candidates",
        lambda: [learned, unrelated],
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
    assert attempts == [("Mount A", learned)]


def test_corrupt_mount_binding_file_is_ignored(tmp_path):
    bindings_file = tmp_path / "indi_mount_bindings.json"
    bindings_file.write_text("{not-json", encoding="utf-8")
    manager = IndiDeviceManager(
        client=FakeClient({}),
        bindings_file=bindings_file,
    )

    assert manager._load_mount_bindings() == {}



def test_already_connected_mounts_are_learned_without_probing(
    monkeypatch,
    tmp_path,
):
    bindings_file = tmp_path / "indi_mount_bindings.json"
    devices = {
        "EQMod Mount": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/serial/by-id/EQMOD"},
        },
        "LX200 OnStep": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/serial/by-id/ONSTEP"},
        },
    }
    manager = IndiDeviceManager(
        client=FakeClient(devices),
        bindings_file=bindings_file,
    )
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.exists",
        lambda path: path in {
            "/dev/serial/by-id/EQMOD",
            "/dev/serial/by-id/ONSTEP",
        },
    )
    monkeypatch.setattr(manager, "_serial_candidates", lambda: [])
    attempts = []
    monkeypatch.setattr(
        manager,
        "_probe_mount_transport",
        lambda *args, **kwargs: attempts.append((args, kwargs)) or False,
    )

    assert manager._autoconnect_mounts(devices) is False
    assert attempts == []

    import json
    payload = json.loads(bindings_file.read_text(encoding="utf-8"))
    assert payload["bindings"] == {
        "EQMod Mount": "/dev/serial/by-id/EQMOD",
        "LX200 OnStep": "/dev/serial/by-id/ONSTEP",
    }


def test_stale_connected_mount_transport_is_not_learned(monkeypatch, tmp_path):
    bindings_file = tmp_path / "indi_mount_bindings.json"
    devices = {
        "Mount A": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"},
            "DEVICE_PORT": { "PORT": "/dev/serial/by-id/UNPLUGGED" },
        },
    }
    manager = IndiDeviceManager(
        client=FakeClient(devices),
        bindings_file=bindings_file,
    )
    monkeypatch.setattr(
        "backend.indi_device_manager.os.path.exists",
        lambda _path: False,
    )
    monkeypatch.setattr(manager, "_serial_candidates", lambda: [])

    assert manager._autoconnect_mounts(devices) is False
    assert not bindings_file.exists()



def test_unwritable_binding_state_does_not_block_mount_probe(
    monkeypatch,
    tmp_path,
):
    devices = {
        "Mount A": {
            "DRIVER_INFO": {"DRIVER_INTERFACE": "1"},
            "CONNECTION": {"CONNECT": "Off"},
            "DEVICE_PORT": {"PORT": "/dev/ttyUSB0"},
        },
    }
    manager = IndiDeviceManager(
        client=FakeClient(devices),
        bindings_file=tmp_path / "state" / "bindings.json",
    )
    monkeypatch.setattr(
        manager,
        "_serial_candidates",
        lambda: ["/dev/serial/by-id/A"],
    )
    monkeypatch.setattr(
        manager,
        "_save_mount_bindings",
        lambda _bindings: (_ for _ in ()).throw(OSError("read-only")),
    )
    attempts = []
    monkeypatch.setattr(
        manager,
        "_probe_mount_transport",
        lambda device, candidate, **_kwargs: attempts.append(
            (device, candidate)
        ) or True,
    )
    monkeypatch.setattr(
        "backend.indi_device_manager._stable_serial_path",
        lambda path: path,
    )

    assert manager._autoconnect_mounts(devices) is True
    assert attempts == [("Mount A", "/dev/serial/by-id/A")]

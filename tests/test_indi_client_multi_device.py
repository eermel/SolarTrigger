from types import SimpleNamespace

import pytest

from plugins.mount.indi_client import IndiClientError, IndiSubprocessClient


HOST = "indi.local"
PORT = 8765
DEVICE_1 = "Mount D1"
DEVICE_2 = "Mount D2"


def completed(*, stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_get_props_isolates_devices_sharing_host_and_port(monkeypatch):
    commands = []
    mixed_output = (
        f"{DEVICE_1}.CONNECTION.CONNECT=On\n"
        f"{DEVICE_2}.CONNECTION.CONNECT=Off\n"
        f"{DEVICE_1}.EQUATORIAL_COORD.RA=12.5\n"
        f"{DEVICE_2}.EQUATORIAL_COORD.RA=3.25\n"
    )

    def fake_run(command, **kwargs):
        commands.append(command)
        return completed(stdout=mixed_output)

    monkeypatch.setattr("plugins.mount.indi_client.subprocess.run", fake_run)
    client_1 = IndiSubprocessClient(host=HOST, port=PORT, device=DEVICE_1)
    client_2 = IndiSubprocessClient(host=HOST, port=PORT, device=DEVICE_2)

    assert client_1.get_props(["*.*"]) == {
        "CONNECTION": {"CONNECT": "On"},
        "EQUATORIAL_COORD": {"RA": "12.5"},
    }
    assert client_2.get_props(["*.*"]) == {
        "CONNECTION": {"CONNECT": "Off"},
        "EQUATORIAL_COORD": {"RA": "3.25"},
    }
    assert commands == [
        ["indi_getprop", "-h", HOST, "-p", str(PORT), f"{DEVICE_1}.*.*"],
        ["indi_getprop", "-h", HOST, "-p", str(PORT), f"{DEVICE_2}.*.*"],
    ]


def test_set_props_qualifies_assignments_for_each_device(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: commands.append(command) or completed(),
    )
    client_1 = IndiSubprocessClient(host=HOST, port=PORT, device=DEVICE_1)
    client_2 = IndiSubprocessClient(host=HOST, port=PORT, device=DEVICE_2)

    client_1.set_props({"CONNECTION": {"CONNECT": "On"}})
    client_2.set_props({"TRACK_STATE": {"TRACK_ON": "On"}})

    assert commands == [
        [
            "indi_setprop",
            "-h",
            HOST,
            "-p",
            str(PORT),
            f"{DEVICE_1}.CONNECTION.CONNECT=On",
        ],
        [
            "indi_setprop",
            "-h",
            HOST,
            "-p",
            str(PORT),
            f"{DEVICE_2}.TRACK_STATE.TRACK_ON=On",
        ],
    ]


def test_ensure_device_present_targets_requested_device_and_maps_absence(monkeypatch):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[-1] == f"{DEVICE_2}.*.*":
            return completed(
                stdout=(
                    f"{DEVICE_1}.CONNECTION.CONNECT=On\n"
                    f"{DEVICE_2}.CONNECTION.CONNECT=On\n"
                )
            )
        return completed(stdout=f"{DEVICE_1}.CONNECTION.CONNECT=On\n")

    monkeypatch.setattr("plugins.mount.indi_client.subprocess.run", fake_run)
    client = IndiSubprocessClient(host=HOST, port=PORT, device=DEVICE_1)

    client.ensure_device_present(DEVICE_2)
    with pytest.raises(IndiClientError) as raised:
        client.ensure_device_present("Absent Mount")

    assert commands == [
        ["indi_getprop", "-h", HOST, "-p", str(PORT), f"{DEVICE_2}.*.*"],
        ["indi_getprop", "-h", HOST, "-p", str(PORT), "Absent Mount.*.*"],
    ]
    assert raised.value.code == "DEVICE_NOT_FOUND"

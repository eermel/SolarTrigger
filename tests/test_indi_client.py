import subprocess
from types import SimpleNamespace

import pytest

from plugins.mount.indi_client import IndiClientError, IndiSubprocessClient


def completed(*, stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_get_props_filters_selected_device_and_builds_patterns(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return completed(
            stdout=(
                "EQMod Mount.EQUATORIAL_EOD_COORD.RA=12.5\n"
                "Other Mount.EQUATORIAL_EOD_COORD.RA=1.0\n"
                "EQMod Mount.EQUATORIAL_EOD_COORD.DEC=-4.25\n"
                "malformed output\n"
            )
        )

    monkeypatch.setattr("plugins.mount.indi_client.subprocess.run", fake_run)
    client = IndiSubprocessClient(host="indi.local", port=8765, timeout_s=2.0)

    props = client.get_props(
        ["EQUATORIAL_EOD_COORD.*", "EQMod Mount.ON_COORD_SET.TRACK"]
    )

    assert props == {
        "EQUATORIAL_EOD_COORD": {"RA": "12.5", "DEC": "-4.25"}
    }
    assert calls == [
        (
            [
                "indi_getprop",
                "-h",
                "indi.local",
                "-p",
                "8765",
                "EQMod Mount.EQUATORIAL_EOD_COORD.*",
                "EQMod Mount.ON_COORD_SET.TRACK",
            ],
            {
                "capture_output": True,
                "text": True,
                "timeout": 2.0,
                "check": False,
            },
        )
    ]


def test_get_props_without_patterns_adds_no_filter(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: commands.append(command) or completed(),
    )

    assert IndiSubprocessClient().get_props(None) == {}
    assert commands == [["indi_getprop", "-h", "127.0.0.1", "-p", "7624"]]


def test_set_props_builds_assignment_arguments(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: commands.append(command) or completed(),
    )

    IndiSubprocessClient(device="Telescope").set_props(
        {"CONNECTION": {"CONNECT": "On"}, "TRACK_RATE": {"RATE": 1.25}}
    )

    assert commands == [
        [
            "indi_setprop",
            "-h",
            "127.0.0.1",
            "-p",
            "7624",
            "Telescope.CONNECTION.CONNECT=On",
            "Telescope.TRACK_RATE.RATE=1.25",
        ]
    ]


def test_timeout_is_structured(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], stderr=b"late")

    monkeypatch.setattr("plugins.mount.indi_client.subprocess.run", fake_run)

    with pytest.raises(IndiClientError) as raised:
        IndiSubprocessClient(timeout_s=0.25).get_props(None)

    assert raised.value.code == "TIMEOUT"
    assert raised.value.command[0] == "indi_getprop"
    assert raised.value.stderr == "late"


def test_os_error_is_indi_unavailable(monkeypatch):
    def fake_run(command, **kwargs):
        raise OSError("executable unavailable")

    monkeypatch.setattr("plugins.mount.indi_client.subprocess.run", fake_run)

    with pytest.raises(IndiClientError) as raised:
        IndiSubprocessClient().get_props(None)

    assert raised.value.code == "INDI_UNAVAILABLE"
    assert raised.value.stderr == "executable unavailable"


@pytest.mark.parametrize("stderr", ["Connection refused", "Connection timed out"])
def test_connection_error_is_indi_unavailable(monkeypatch, stderr):
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: completed(returncode=1, stderr=stderr),
    )

    with pytest.raises(IndiClientError) as raised:
        IndiSubprocessClient().get_props(None)

    assert raised.value.code == "INDI_UNAVAILABLE"
    assert raised.value.returncode == 1
    assert raised.value.stderr == stderr


@pytest.mark.parametrize("stderr", ["connection lost", "write failed: broken pipe"])
def test_mid_sequence_disconnect_is_connection_lost(monkeypatch, stderr):
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: completed(returncode=2, stderr=stderr),
    )

    with pytest.raises(IndiClientError) as raised:
        IndiSubprocessClient().set_props({"CONNECTION": {"CONNECT": "On"}})

    assert raised.value.code == "CONNECTION_LOST"


@pytest.mark.parametrize(
    "stderr", ["unknown property CONNECTION", "element TRACK not found"]
)
def test_setprop_unsupported_property_is_structured(monkeypatch, stderr):
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: completed(returncode=1, stderr=stderr),
    )

    with pytest.raises(IndiClientError) as raised:
        IndiSubprocessClient().set_props({"CONNECTION": {"CONNECT": "On"}})

    assert raised.value.code == "PROPERTY_UNSUPPORTED"


def test_other_nonzero_exit_is_connection_failed(monkeypatch):
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: completed(returncode=7, stderr="unexpected failure"),
    )

    with pytest.raises(IndiClientError) as raised:
        IndiSubprocessClient().get_props(None)

    assert raised.value.code == "CONNECTION_FAILED"
    assert raised.value.returncode == 7


def test_ensure_device_present_uses_requested_device(monkeypatch):
    commands = []
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: commands.append(command)
        or completed(stdout="Aux Mount.CONNECTION.CONNECT=On\n"),
    )

    IndiSubprocessClient().ensure_device_present("Aux Mount")

    assert commands[0][-1] == "Aux Mount.*"


def test_ensure_device_present_raises_when_missing(monkeypatch):
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: completed(
            stdout="Different Mount.CONNECTION.CONNECT=On\n"
        ),
    )

    with pytest.raises(IndiClientError) as raised:
        IndiSubprocessClient().ensure_device_present("Aux Mount")

    assert raised.value.code == "DEVICE_NOT_FOUND"


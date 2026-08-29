import io
from types import SimpleNamespace

from plugins.mount.indi_client import IndiSubprocessClient


DEVICE = "EQMod Mount"


def completed(*, stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class FakeMonitorProcess:
    def __init__(self, output=""):
        self.stdout = io.StringIO(output)
        self.stderr = io.StringIO("")
        self.terminated = False
        self.killed = False

    def poll(self):
        return 0 if self.terminated or self.killed else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


def test_monitor_uses_existing_snapshot_without_relaunching_getprop(monkeypatch):
    run_calls = []
    popen_calls = []

    def fake_run(command, **kwargs):
        run_calls.append(command)
        return completed(
            stdout=(
                f"{DEVICE}.CONNECTION.CONNECT=On\n"
                f"{DEVICE}.TELESCOPE_SLEW_RATE.8x=Off\n"
                f"{DEVICE}.TELESCOPE_SLEW_RATE.9x=On\n"
            )
        )

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return FakeMonitorProcess()

    monkeypatch.setattr("plugins.mount.indi_client.subprocess.run", fake_run)
    monkeypatch.setattr("plugins.mount.indi_client.subprocess.Popen", fake_popen)

    client = IndiSubprocessClient(device=DEVICE)

    # Prime the cache exactly as the normal connect path does.
    assert client.get_props(["*.*"])["CONNECTION"]["CONNECT"] == "On"
    assert len(run_calls) == 1

    client.start_monitor()

    props = client.get_props(["TELESCOPE_SLEW_RATE.*"])

    assert props == {
        "TELESCOPE_SLEW_RATE": {
            "8x": "Off",
            "9x": "On",
        }
    }
    # Once the monitor is active, get_props() must not launch indi_getprop.
    assert len(run_calls) == 1

    assert popen_calls[0][0] == [
        "stdbuf",
        "-oL",
        "indi_getprop",
        "-m",
        "-t",
        "0",
        "-h",
        "127.0.0.1",
        "-p",
        "7624",
        f"{DEVICE}.*.*",
    ]

    client.stop_monitor()


def test_monitor_updates_cached_properties(monkeypatch):
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: completed(
            stdout=(
                f"{DEVICE}.CONNECTION.CONNECT=On\n"
                f"{DEVICE}.TELESCOPE_SLEW_RATE.8x=Off\n"
                f"{DEVICE}.TELESCOPE_SLEW_RATE.9x=On\n"
            )
        ),
    )

    process = FakeMonitorProcess(
        output=(
            f"{DEVICE}.TELESCOPE_SLEW_RATE.8x=On\n"
            f"{DEVICE}.TELESCOPE_SLEW_RATE.9x=Off\n"
        )
    )
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.Popen",
        lambda command, **kwargs: process,
    )

    client = IndiSubprocessClient(device=DEVICE)
    client.get_props(["*.*"])
    client.start_monitor()

    # Fake stdout reaches EOF immediately, therefore the reader thread
    # can be joined deterministically.
    client._monitor_thread.join(timeout=1.0)

    assert client.get_props(["TELESCOPE_SLEW_RATE.*"]) == {
        "TELESCOPE_SLEW_RATE": {
            "8x": "On",
            "9x": "Off",
        }
    }

    client.stop_monitor()


def test_monitor_cache_preserves_device_scoping(monkeypatch):
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: completed(
            stdout=(
                "Mount A.CONNECTION.CONNECT=On\n"
                "Mount B.CONNECTION.CONNECT=Off\n"
            )
        ),
    )

    process = FakeMonitorProcess()
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.Popen",
        lambda command, **kwargs: process,
    )

    client = IndiSubprocessClient(device="Mount A")
    client.get_props(["*.*"])
    client.start_monitor()

    assert client.get_props(["CONNECTION.*"]) == {
        "CONNECTION": {"CONNECT": "On"}
    }

    client.stop_monitor()


def test_stop_monitor_terminates_persistent_process(monkeypatch):
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.run",
        lambda command, **kwargs: completed(
            stdout=f"{DEVICE}.CONNECTION.CONNECT=On\n"
        ),
    )

    process = FakeMonitorProcess()
    monkeypatch.setattr(
        "plugins.mount.indi_client.subprocess.Popen",
        lambda command, **kwargs: process,
    )

    client = IndiSubprocessClient(device=DEVICE)
    client.get_props(["*.*"])
    client.start_monitor()

    client.stop_monitor()

    assert process.terminated is True


def test_indi_mount_starts_and_stops_runtime_monitor(tmp_path):
    from copy import deepcopy

    from plugins.mount.indi_plugin import IndiMount

    class MonitorClient:
        def __init__(self):
            self.props = {
                "CONNECTION": {
                    "CONNECT": "Off",
                    "DISCONNECT": "On",
                },
                "TELESCOPE_TRACK_STATE": {
                    "TRACK_ON": "Off",
                    "TRACK_OFF": "On",
                },
            }
            self.monitor_started = False
            self.monitor_stopped = False

        def ensure_device_present(self, device):
            return None

        def get_props(self, patterns=None):
            return deepcopy(self.props)

        def set_props(self, assignments):
            for prop, elements in assignments.items():
                self.props.setdefault(prop, {}).update(elements)

        def start_monitor(self):
            self.monitor_started = True

        def stop_monitor(self):
            self.monitor_stopped = True

    serial_port = tmp_path / "ttyUSB0"
    serial_port.touch(mode=0o600)

    client = MonitorClient()
    plugin = IndiMount(
        log_fn=lambda *_: None,
        config={
            "device": "Test Mount",
            "serial_port": str(serial_port),
            "timeout": 0,
            "poll_interval": 0,
        },
        client=client,
    )

    plugin.connect()

    assert client.monitor_started is True
    assert plugin.connected is True

    plugin.disconnect()

    assert client.monitor_stopped is True
    assert plugin.connected is False

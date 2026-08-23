import importlib.util
import sys
import threading
import time
from types import ModuleType

import pytest


serial_stub_module = sys.modules.get("serial")
if serial_stub_module is None and importlib.util.find_spec("serial") is None:
    serial_stub_module = ModuleType("serial")
    sys.modules["serial"] = serial_stub_module

if serial_stub_module is not None:
    serial_stub_module.EIGHTBITS = getattr(serial_stub_module, "EIGHTBITS", 8)
    serial_stub_module.PARITY_NONE = getattr(serial_stub_module, "PARITY_NONE", "N")
    serial_stub_module.STOPBITS_ONE = getattr(serial_stub_module, "STOPBITS_ONE", 1)
    serial_stub_module.SerialException = getattr(
        serial_stub_module, "SerialException", Exception
    )

from plugins.mount.onstep import OnStep, OnStepError


class QuerySerialStub:
    def __init__(self, timeout):
        self.is_open = True
        self.timeout = timeout
        self.events = []
        self.first_write_started = threading.Event()
        self.release_first_write = threading.Event()
        self._write_count = 0

    def reset_input_buffer(self):
        self.events.append("reset_input")

    def write(self, command):
        self._write_count += 1
        self.events.append(("write_start", command, self.timeout))
        if self._write_count == 1:
            self.first_write_started.set()
            assert self.release_first_write.wait(timeout=1)
        self.events.append(("write_end", command))

    def flush(self):
        self.events.append("flush")

    def read_until(self, terminator):
        self.events.append(("read_until", terminator))
        return b"N#"


def test_query_text_calls_are_serialized_and_restore_timeout():
    mount = OnStep(timeout=1.0)
    serial_stub = QuerySerialStub(timeout=mount.timeout)
    mount.serial = serial_stub
    results = []

    first = threading.Thread(
        target=lambda: results.append(mount._query_text(b":GU#", timeout=0.1))
    )
    second = threading.Thread(
        target=lambda: results.append(mount._query_text(b":GU#", timeout=0.2))
    )

    first.start()
    assert serial_stub.first_write_started.wait(timeout=1)
    second.start()
    time.sleep(0.05)

    assert serial_stub.events == [
        "reset_input",
        ("write_start", b":GU#", 0.1),
    ]
    serial_stub.release_first_write.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results == ["N", "N"]
    assert serial_stub.timeout == mount.timeout
    assert serial_stub.events == [
        "reset_input",
        ("write_start", b":GU#", 0.1),
        ("write_end", b":GU#"),
        "flush",
        ("read_until", b"#"),
        "reset_input",
        ("write_start", b":GU#", 0.2),
        ("write_end", b":GU#"),
        "flush",
        ("read_until", b"#"),
    ]


def test_query_text_restores_timeout_after_serial_exception():
    mount = OnStep(timeout=1.0)
    serial_stub = QuerySerialStub(timeout=mount.timeout)
    serial_stub.release_first_write.set()
    serial_stub.read_until = lambda _terminator: (_ for _ in ()).throw(
        sys.modules["serial"].SerialException("read failed")
    )
    mount.serial = serial_stub

    with pytest.raises(OnStepError, match="Erreur de communication"):
        mount._query_text(b":GU#", timeout=0.1)

    assert serial_stub.timeout == mount.timeout


class ConnectionSerialStub:
    def __init__(self, *args, **kwargs):
        self.is_open = True
        self.timeout = kwargs["timeout"]
        self.write_started = threading.Event()
        self.release_write = threading.Event()

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def write(self, command):
        self.write_started.set()
        assert self.release_write.wait(timeout=1)

    def flush(self):
        pass

    def close(self):
        self.is_open = False


def test_disconnect_waits_for_send_and_leaves_consistent_state(monkeypatch):
    serial_stub = ConnectionSerialStub(timeout=1.0)
    monkeypatch.setattr(
        "plugins.mount.onstep.serial.Serial",
        lambda *args, **kwargs: serial_stub,
        raising=False,
    )
    mount = OnStep(timeout=1.0)
    mount.connect()
    errors = []

    def capture_errors(operation):
        try:
            operation()
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    sender = threading.Thread(
        target=capture_errors,
        args=(lambda: mount._send(b":Q#"),),
    )
    disconnector = threading.Thread(
        target=capture_errors,
        args=(mount.disconnect,),
    )

    sender.start()
    assert serial_stub.write_started.wait(timeout=1)
    disconnector.start()
    time.sleep(0.05)

    assert mount.serial is serial_stub
    assert serial_stub.is_open
    serial_stub.release_write.set()
    sender.join(timeout=1)
    disconnector.join(timeout=1)

    assert not sender.is_alive()
    assert not disconnector.is_alive()
    assert errors == []
    assert mount.serial is None
    assert not serial_stub.is_open

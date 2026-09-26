import socket
import xml.etree.ElementTree as ET

import pytest

from plugins.mount.indi_client import IndiClientError, IndiTcpSession


class FakeSocket:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.sent = []
        self.timeouts = []
        self.closed = False
    def settimeout(self, value):
        self.timeouts.append(value)
    def sendall(self, payload):
        self.sent.append(payload)
    def recv(self, _size):
        if not self.chunks:
            raise socket.timeout()
        item = self.chunks.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    def shutdown(self, _how):
        pass
    def close(self):
        self.closed = True


def test_tcp_session_uses_one_socket_for_get_set_and_wait(monkeypatch):
    sock = FakeSocket([
        b'<defSwitchVector device="Mount A" name="CONNECTION"><defSwitch name="CONNECT">O',
        b'n</defSwitch><defSwitch name="DISCONNECT">Off</defSwitch></defSwitchVector>',
    ])
    monkeypatch.setattr(
        "plugins.mount.indi_client.socket.create_connection",
        lambda *args, **kwargs: sock,
    )
    with IndiTcpSession(device="Mount A", timeout_s=1.0) as session:
        session.set_text("DEVICE_PORT", {"PORT": "/dev/serial/by-id/A"})
        session.set_switch("CONNECTION", {"CONNECT": "On", "DISCONNECT": "Off"})
        assert session.wait_for("CONNECTION", "CONNECT", {"On"}, 1.0) is True

    assert sock.closed is True
    assert len(sock.sent) == 3
    assert b"getProperties" in sock.sent[0]
    assert b"newTextVector" in sock.sent[1]
    assert b"/dev/serial/by-id/A" in sock.sent[1]
    assert b"newSwitchVector" in sock.sent[2]


def test_tcp_session_filters_other_device(monkeypatch):
    sock = FakeSocket([
        b'<setSwitchVector device="Other" name="CONNECTION"><oneSwitch name="CONNECT">On</oneSwitch></setSwitchVector>'
        b'<setSwitchVector device="Mount A" name="CONNECTION"><oneSwitch name="CONNECT">On</oneSwitch></setSwitchVector>'
    ])
    monkeypatch.setattr(
        "plugins.mount.indi_client.socket.create_connection",
        lambda *args, **kwargs: sock,
    )
    with IndiTcpSession(device="Mount A") as session:
        assert session.wait_for("CONNECTION", "CONNECT", {"On"}, 1.0) is True
        assert "Other" not in session.props


def test_tcp_session_eof_is_connection_lost(monkeypatch):
    sock = FakeSocket([b""])
    monkeypatch.setattr(
        "plugins.mount.indi_client.socket.create_connection",
        lambda *args, **kwargs: sock,
    )
    with IndiTcpSession(device="Mount A") as session:
        with pytest.raises(IndiClientError) as raised:
            session.wait_for("CONNECTION", "CONNECT", {"On"}, 1.0)
    assert raised.value.code == "CONNECTION_LOST"


def test_tcp_session_connect_failure_is_structured(monkeypatch):
    def fail(*_args, **_kwargs):
        raise OSError("refused")
    monkeypatch.setattr("plugins.mount.indi_client.socket.create_connection", fail)
    with pytest.raises(IndiClientError) as raised:
        with IndiTcpSession(device="Mount A"):
            pass
    assert raised.value.code == "INDI_UNAVAILABLE"


def test_tcp_session_emits_valid_indi_xml(monkeypatch):
    sock = FakeSocket()
    monkeypatch.setattr(
        "plugins.mount.indi_client.socket.create_connection",
        lambda *args, **kwargs: sock,
    )
    with IndiTcpSession(device="Mount & A") as session:
        session.set_text("DEVICE_PORT", {"PORT": "/dev/a&b"})
    root = ET.fromstring(sock.sent[1])
    assert root.attrib["device"] == "Mount & A"
    assert root.find("oneText").text == "/dev/a&b"

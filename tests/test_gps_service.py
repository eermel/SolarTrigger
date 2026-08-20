#!/usr/bin/env python3
"""Tests hors materiel de GpsService."""
from datetime import datetime, timezone

from plugins.gps.base import GpsPlugin, GpsPosition, GpsStatus
from services.gps_service import GpsService, GpsServiceState


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class FakeGps(GpsPlugin):
    plugin_id = "fake"

    def __init__(self):
        super().__init__(log_fn=lambda *_: None, config={})
        self._connected = False
        self.position = None
        self.gps_time = None
        self.satellites = None
        self.message = None

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    @property
    def connected(self):
        return self._connected

    def status(self):
        return GpsStatus(self._connected, self.position is not None,
                         self.satellites, self.plugin_id, "/dev/fake", self.message)

    def get_position(self):
        return self.position

    def get_time(self):
        return self.gps_time


def make_position(second=0):
    dt = datetime(2026, 8, 19, 20, 20, second, tzinfo=timezone.utc)
    return GpsPosition(48.873645, 2.379665, 88.6, dt, 20, 0.77, 0.1)


def test_no_fix_then_ready():
    clock = FakeClock()
    plugin = FakeGps()
    plugin.connect()
    service = GpsService(plugin, stale_after=3.0, monotonic_fn=clock,
                         log_fn=lambda *_: None)

    service._refresh_once()
    assert service.snapshot().state == GpsServiceState.NO_FIX

    plugin.position = make_position()
    plugin.gps_time = plugin.position.timestamp
    plugin.satellites = 20
    service._refresh_once()
    snap = service.snapshot()
    assert snap.state == GpsServiceState.READY
    assert snap.usable
    assert snap.position.latitude == 48.873645
    assert snap.satellites == 20


def test_stale_when_position_stops_updating():
    clock = FakeClock()
    plugin = FakeGps()
    plugin.connect()
    plugin.position = make_position()
    plugin.gps_time = plugin.position.timestamp
    service = GpsService(plugin, stale_after=3.0, monotonic_fn=clock,
                         log_fn=lambda *_: None)

    service._refresh_once()
    assert service.snapshot().state == GpsServiceState.READY
    clock.advance(3.1)
    assert service.snapshot().state == GpsServiceState.STALE
    assert service.get_position() is None
    assert service.get_position(require_usable=False) is not None


def test_new_position_clears_stale():
    clock = FakeClock()
    plugin = FakeGps()
    plugin.connect()
    plugin.position = make_position(0)
    plugin.gps_time = plugin.position.timestamp
    service = GpsService(plugin, stale_after=3.0, monotonic_fn=clock,
                         log_fn=lambda *_: None)

    service._refresh_once()
    clock.advance(4.0)
    assert service.snapshot().state == GpsServiceState.STALE

    plugin.position = make_position(1)
    plugin.gps_time = plugin.position.timestamp
    service._refresh_once()
    assert service.snapshot().state == GpsServiceState.READY
    assert service.snapshot().age_seconds == 0.0


def test_plugin_error_is_exposed():
    clock = FakeClock()
    plugin = FakeGps()
    plugin.connect()
    plugin.message = "serial read failed"
    service = GpsService(plugin, monotonic_fn=clock, log_fn=lambda *_: None)

    service._refresh_once()
    snap = service.snapshot()
    assert snap.state == GpsServiceState.ERROR
    assert snap.message == "serial read failed"


def test_stop_keeps_last_position_but_makes_it_unusable():
    clock = FakeClock()
    plugin = FakeGps()
    plugin.connect()
    plugin.position = make_position()
    plugin.gps_time = plugin.position.timestamp
    service = GpsService(plugin, monotonic_fn=clock, log_fn=lambda *_: None)
    service._refresh_once()

    service.stop()
    snap = service.snapshot()
    assert snap.state == GpsServiceState.DISCONNECTED
    assert not snap.usable
    assert snap.position is not None


def test_initialize_is_oneshot_and_stops_plugin():
    p = FakeGps()
    svc = GpsService(p, poll_interval=0.01, stale_after=1.0, log_fn=lambda *_: None)

    def feed():
        import time
        time.sleep(0.03)
        now = datetime.now(timezone.utc)
        p.position = GpsPosition(48.0, 2.0, 100.0, now, 8, 0.8, 0.0)
        p.gps_time = now
        p.satellites = 8

    import threading
    t = threading.Thread(target=feed)
    t.start()
    snap = svc.initialize(timeout_s=0.5, require_gga=True)
    t.join()
    assert snap.state == GpsServiceState.READY
    assert snap.position.latitude == 48.0
    assert svc.running is False
    assert p.connected is False

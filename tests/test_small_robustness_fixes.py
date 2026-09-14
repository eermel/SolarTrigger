import logging
import threading
import time
from types import SimpleNamespace

from backend.generic_worker import GenericWorker
from plugins.gps.serial_nmea import SerialNmeaGps
from scripts import gps_sync


def _nmea(body: str) -> str:
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    return f"${body}*{checksum:02X}"


def test_standalone_rmc_parser_preserves_fractional_seconds():
    sentence = _nmea(
        "GPRMC,092115.682,A,4852.4184,N,00222.7794,E,0.0,0.0,020827,,,A"
    )
    parsed = gps_sync.parse_gprmc(sentence)
    assert parsed is not None
    dt_utc = parsed[0]
    assert dt_utc.hour == 9
    assert dt_utc.minute == 21
    assert dt_utc.second == 15
    assert dt_utc.microsecond == 682000


def test_serial_nmea_plugin_preserves_fractional_seconds():
    sentence = _nmea(
        "GNRMC,092115.682,A,4852.4184,N,00222.7794,E,0.0,0.0,020827,,,A"
    )
    parsed = SerialNmeaGps.parse_sentence(sentence)
    assert parsed is not None
    assert parsed["timestamp"].microsecond == 682000


def test_hardware_clock_failure_is_reported_without_false_success(monkeypatch, caplog):
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=1, stderr="RTC unavailable")

    monkeypatch.setattr(gps_sync.subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO):
        ok = gps_sync._update_hardware_clock("/sbin/hwclock", [])

    assert ok is False
    text = caplog.text
    assert "Hardware RTC update failed" in text
    assert "Hardware RTC updated" not in text


def test_hardware_clock_success_is_reported(monkeypatch, caplog):
    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(gps_sync.subprocess, "run", fake_run)

    with caplog.at_level(logging.INFO):
        ok = gps_sync._update_hardware_clock("/sbin/hwclock", [])

    assert ok is True
    assert "Hardware RTC updated" in caplog.text


def test_worker_stop_drains_full_bounded_queue_without_losing_shutdown():
    entered = threading.Event()
    release = threading.Event()
    executed = []

    def first_job():
        entered.set()
        assert release.wait(1.0)
        executed.append("first")

    def second_job():
        executed.append("second")

    worker = GenericWorker(
        rig_id=1,
        device_kind="test",
        max_queue_size=1,
        shutdown_policy="drain",
    )
    worker.start()
    first = worker.submit(first_job)
    assert entered.wait(1.0)

    second = worker.submit(second_job)

    releaser = threading.Thread(
        target=lambda: (time.sleep(0.05), release.set()),
        daemon=True,
    )
    releaser.start()

    assert worker.stop(timeout=1.0) is True
    assert first.result(timeout=0.1) is None
    assert second.result(timeout=0.1) is None
    assert executed == ["first", "second"]
    assert worker.running is False

#!/usr/bin/env python3
"""Tests hors materiel du contrat et du parser GPS NMEA."""
from datetime import timezone
from plugins.gps import GpsPosition, available_plugins
from plugins.gps.serial_nmea import SerialNmeaGps


def checksum(payload):
    value = 0
    for c in payload:
        value ^= ord(c)
    return f"${payload}*{value:02X}"


def test_plugins():
    p = available_plugins()
    assert "serial_nmea" in p
    assert "gpsd" in p


def test_rmc():
    s = checksum("GPRMC,123519,A,4807.038,N,01131.000,E,0.0,0.0,230394,,,A")
    d = SerialNmeaGps.parse_sentence(s)
    assert d["type"] == "RMC"
    assert abs(d["latitude"] - 48.1173) < 1e-4
    assert abs(d["longitude"] - 11.5166667) < 1e-4
    assert d["timestamp"].tzinfo == timezone.utc


def test_gga():
    s = checksum("GPGGA,123520,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
    d = SerialNmeaGps.parse_sentence(s)
    assert d["type"] == "GGA"
    assert d["satellites"] == 8
    assert abs(d["altitude_m"] - 545.4) < 1e-6


def test_bad_checksum():
    assert SerialNmeaGps.parse_sentence("$GPRMC,123519,A,4807.038,N,01131.000,E,0,0,230394,,,A*00") is None


def test_position_dataclass():
    p = GpsPosition(48.0, 2.0, 100.0, __import__("datetime").datetime.now(timezone.utc))
    assert p.latitude == 48.0


def test_gpsd_merges_sky_and_tpv_and_prefers_msl_altitude():
    from plugins.gps.gpsd import GpsdPlugin

    gps = GpsdPlugin(log_fn=lambda *_: None, config={})
    gps._handle_report({
        "class": "SKY",
        "uSat": 16,
        "hdop": 1.37,
    })
    gps._handle_report({
        "class": "TPV",
        "mode": 3,
        "time": "2026-08-19T20:44:11.000Z",
        "lat": 48.873703333,
        "lon": 2.379486667,
        "altHAE": 137.3,
        "altMSL": 90.0,
        "alt": 90.0,
        "speed": 0.082,
    })

    status = gps.status()
    pos = gps.get_position()
    assert status.satellites == 16
    assert pos.satellites == 16
    assert abs(pos.hdop - 1.37) < 1e-9
    assert abs(pos.altitude_m - 90.0) < 1e-9
    assert pos.timestamp.tzinfo == timezone.utc


def test_gpsd_sky_after_tpv_updates_existing_position():
    from plugins.gps.gpsd import GpsdPlugin

    gps = GpsdPlugin(log_fn=lambda *_: None, config={})
    gps._handle_report({
        "class": "TPV",
        "mode": 3,
        "time": "2026-08-19T20:44:11.000Z",
        "lat": 48.0,
        "lon": 2.0,
        "altMSL": 100.0,
    })
    assert gps.get_position().satellites is None
    assert gps.get_position().hdop is None

    gps._handle_report({
        "class": "SKY",
        "uSat": 12,
        "hdop": 0.8,
    })
    pos = gps.get_position()
    assert pos.satellites == 12
    assert abs(pos.hdop - 0.8) < 1e-9


def test_gpsd_satellite_array_fallback():
    from plugins.gps.gpsd import GpsdPlugin

    gps = GpsdPlugin(log_fn=lambda *_: None, config={})
    gps._handle_report({
        "class": "SKY",
        "satellites": [
            {"used": True},
            {"used": False},
            {"used": True},
        ],
        "hdop": 0.9,
    })
    assert gps.status().satellites == 2

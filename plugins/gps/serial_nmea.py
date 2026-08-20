#!/usr/bin/env python3
"""Plugin GPS serie NMEA, independant du modele de dongle.
Version : 1.0.00

Supporte GGA/RMC et recherche optionnelle par VID/PID USB.
Le BU-353N5 est donc une configuration de ce plugin, pas une dependance du
contrat GPS.
"""
import glob
import os
import threading
from datetime import datetime, timezone

from .base import GpsPlugin, GpsPosition, GpsStatus

try:
    import serial
except ImportError:  # permet d'importer/tester le plugin sans pyserial
    serial = None


class SerialNmeaGps(GpsPlugin):
    plugin_id = "serial_nmea"
    display_name = "GPS serie NMEA (USB/TTL)"

    def __init__(self, log_fn=print, config=None):
        super().__init__(log_fn, config)
        self._serial = None
        self._thread = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._position = None
        self._gps_time = None
        self._satellites = None
        self._last_error = None

    @staticmethod
    def _checksum_valid(sentence):
        try:
            if not sentence.startswith("$") or "*" not in sentence:
                return False
            data, checksum = sentence[1:].split("*", 1)
            calc = 0
            for char in data:
                calc ^= ord(char)
            return calc == int(checksum[:2], 16)
        except (ValueError, IndexError):
            return False

    @staticmethod
    def _coord(raw, direction, degrees_digits):
        if not raw:
            return None
        value = float(raw[:degrees_digits]) + float(raw[degrees_digits:]) / 60.0
        if direction in ("S", "W"):
            value = -value
        return value

    @classmethod
    def parse_sentence(cls, sentence):
        """Retourne un dict de donnees partielles a partir de GGA/RMC."""
        sentence = sentence.strip()
        if not cls._checksum_valid(sentence):
            return None
        p = sentence.split(",")
        kind = p[0][3:] if len(p[0]) >= 6 else ""
        try:
            if kind == "RMC" and len(p) >= 10 and p[2] == "A":
                hhmmss = p[1].split(".")[0]
                ddmmyy = p[9]
                dt = datetime(2000 + int(ddmmyy[4:6]), int(ddmmyy[2:4]), int(ddmmyy[:2]),
                              int(hhmmss[:2]), int(hhmmss[2:4]), int(hhmmss[4:6]),
                              tzinfo=timezone.utc)
                return {
                    "type": "RMC", "timestamp": dt,
                    "latitude": cls._coord(p[3], p[4], 2),
                    "longitude": cls._coord(p[5], p[6], 3),
                    "speed_knots": float(p[7]) if p[7] else 0.0,
                }
            if kind == "GGA" and len(p) >= 10 and p[6] and int(p[6]) > 0:
                return {
                    "type": "GGA",
                    "latitude": cls._coord(p[2], p[3], 2),
                    "longitude": cls._coord(p[4], p[5], 3),
                    "satellites": int(p[7]) if p[7] else None,
                    "hdop": float(p[8]) if p[8] else None,
                    "altitude_m": float(p[9]) if p[9] else None,
                }
        except (ValueError, IndexError):
            return None
        return None

    @staticmethod
    def find_port(vid=None, pid=None):
        """Recherche un ttyUSB par VID/PID, puis fallback ttyUSB* si demande."""
        if vid and pid:
            vid = str(vid).lower().replace("0x", "")
            pid = str(pid).lower().replace("0x", "")
            for tty_path in sorted(glob.glob("/sys/bus/usb-serial/devices/ttyUSB*")):
                real = os.path.realpath(tty_path)
                current = real
                for _ in range(8):
                    current = os.path.dirname(current)
                    vf, pf = os.path.join(current, "idVendor"), os.path.join(current, "idProduct")
                    if os.path.exists(vf) and os.path.exists(pf):
                        try:
                            with open(vf) as vendor_file, open(pf) as product_file:
                                if (vendor_file.read().strip().lower() == vid and
                                        product_file.read().strip().lower() == pid):
                                    return "/dev/" + os.path.basename(tty_path)
                        except OSError:
                            pass
                        break
            # Une identite USB explicite ne doit jamais retomber sur un autre
            # tty arbitraire (monture, console serie, etc.).
            return None
        for port in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2", "/dev/ttyACM0"):
            if os.path.exists(port):
                return port
        return None

    @staticmethod
    def _find_configured_port(config):
        port = config.get("port")
        if port:
            return port
        return SerialNmeaGps.find_port(config.get("vid"), config.get("pid"))

    @staticmethod
    def probe(config=None):
        config = config or {}
        port = config.get("port") or SerialNmeaGps.find_port(config.get("vid"), config.get("pid"))
        return bool(port and serial is not None)

    def connect(self):
        if serial is None:
            raise RuntimeError("pyserial est requis pour le plugin serial_nmea")
        if self.connected:
            return
        port = self._find_configured_port(self.config)
        if not port:
            raise RuntimeError("Aucun port GPS serie detecte")
        baudrate = int(self.config.get("baudrate", 4800))
        timeout = float(self.config.get("timeout", 1.0))
        self._serial = serial.Serial(port, baudrate=baudrate, timeout=timeout)
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._reader_loop, name="gps-nmea", daemon=True)
        self._thread.start()
        self.log(f"GPS NMEA connecte sur {port} @ {baudrate} baud")

    def disconnect(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None

    @property
    def connected(self):
        return bool(self._serial and getattr(self._serial, "is_open", False))

    def _reader_loop(self):
        while not self._stop_event.is_set():
            try:
                raw = self._serial.readline()
                if not raw:
                    continue
                parsed = self.parse_sentence(raw.decode("ascii", errors="ignore"))
                if not parsed:
                    continue
                with self._lock:
                    if parsed["type"] == "RMC":
                        self._gps_time = parsed["timestamp"]
                        if parsed["latitude"] is not None and parsed["longitude"] is not None:
                            old = self._position
                            self._position = GpsPosition(
                                parsed["latitude"], parsed["longitude"],
                                old.altitude_m if old else None,
                                parsed["timestamp"], self._satellites,
                                old.hdop if old else None, parsed["speed_knots"])
                    elif parsed["type"] == "GGA":
                        self._satellites = parsed["satellites"]
                        if parsed["latitude"] is not None and parsed["longitude"] is not None:
                            old = self._position
                            ts = self._gps_time or datetime.now(timezone.utc)
                            self._position = GpsPosition(
                                parsed["latitude"], parsed["longitude"], parsed["altitude_m"],
                                ts, parsed["satellites"], parsed["hdop"],
                                old.speed_knots if old else None)
            except Exception as exc:
                self._last_error = str(exc)
                if self._stop_event.wait(1.0):
                    break

    def status(self):
        with self._lock:
            return GpsStatus(self.connected, self._position is not None,
                             self._satellites, self.plugin_id,
                             getattr(self._serial, "port", None), self._last_error)

    def get_position(self):
        with self._lock:
            return self._position

    def get_time(self):
        with self._lock:
            return self._gps_time

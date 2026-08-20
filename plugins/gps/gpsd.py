#!/usr/bin/env python3
"""Plugin GPS via gpsd, sans dependance Python externe.
Version : 1.2.00

Dialogue directement avec l'API JSON TCP de gpsd (port 2947). Cela evite
l'acces concurrent au port serie lorsque gpsd est deja utilise par le systeme.
"""
import json
import socket
import threading
from dataclasses import replace
from datetime import datetime, timezone

from .base import GpsPlugin, GpsPosition, GpsStatus


class GpsdPlugin(GpsPlugin):
    plugin_id = "gpsd"
    display_name = "gpsd"

    def __init__(self, log_fn=print, config=None):
        super().__init__(log_fn, config)
        self._sock = None
        self._file = None
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._position = None
        self._gps_time = None
        self._satellites = None
        self._hdop = None
        self._mode = 0
        self._error = None

    @staticmethod
    def probe(config=None):
        config = config or {}
        try:
            s = socket.create_connection((config.get("host", "127.0.0.1"),
                                          int(config.get("port", 2947))), timeout=1.0)
            s.close()
            return True
        except OSError:
            return False

    def connect(self):
        if self.connected:
            return
        host = self.config.get("host", "127.0.0.1")
        port = int(self.config.get("port", 2947))
        timeout = float(self.config.get("timeout", 3.0))
        try:
            self._sock = socket.create_connection((host, port), timeout=timeout)
            self._sock.settimeout(1.0)
            self._file = self._sock.makefile("r", encoding="ascii", errors="ignore", newline="\n")
            self._sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
        except Exception:
            self.disconnect()
            raise
        self._stop.clear()
        self._error = None
        self._thread = threading.Thread(target=self._reader_loop, name="gpsd", daemon=True)
        self._thread.start()
        self.log(f"GPS gpsd connecte sur {host}:{port}")

    def disconnect(self):
        self._stop.set()
        if self._thread and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
        self._file = None
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None

    @property
    def connected(self):
        return self._sock is not None

    @staticmethod
    def _parse_time(value):
        if not value:
            return None
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
            except ValueError:
                return None
        return None

    @staticmethod
    def _satellites_used(report):
        """Retourne le nombre de satellites utilises depuis un rapport SKY.

        gpsd 3.25 expose directement ``uSat``. Certaines versions/configurations
        exposent aussi le tableau ``satellites`` avec un booléen ``used``.
        """
        usat = report.get("uSat")
        if usat is not None:
            try:
                return int(usat)
            except (TypeError, ValueError):
                pass

        sats = report.get("satellites") or []
        if isinstance(sats, list):
            return sum(1 for sat in sats if isinstance(sat, dict) and sat.get("used"))
        return None

    def _handle_report(self, report):
        """Integre un rapport gpsd dans l'etat du plugin.

        SKY et TPV arrivent dans des messages separes. Les metadonnees de
        qualite (satellites, HDOP) sont donc memorisees puis fusionnees avec la
        derniere position TPV, quel que soit l'ordre d'arrivee.
        """
        cls = report.get("class")

        if cls == "SKY":
            satellites = self._satellites_used(report)
            hdop = report.get("hdop")
            try:
                hdop = float(hdop) if hdop is not None else None
            except (TypeError, ValueError):
                hdop = None

            with self._lock:
                if satellites is not None:
                    self._satellites = satellites
                if hdop is not None:
                    self._hdop = hdop
                if self._position is not None:
                    self._position = replace(
                        self._position,
                        satellites=self._satellites,
                        hdop=self._hdop,
                    )
                self._error = None
            return

        if cls != "TPV":
            return

        mode = int(report.get("mode") or 0)
        ts = self._parse_time(report.get("time"))
        lat, lon = report.get("lat"), report.get("lon")

        with self._lock:
            self._mode = mode
            if ts is not None:
                self._gps_time = ts

            if mode >= 2 and lat is not None and lon is not None:
                # Pour l'altitude geographique affichee/utilisee par le projet,
                # privilegier le niveau moyen de la mer. altHAE (ellipsoide)
                # n'est qu'un fallback.
                alt = report.get("altMSL")
                if alt is None:
                    alt = report.get("alt")
                if alt is None:
                    alt = report.get("altHAE")

                speed = report.get("speed")
                self._position = GpsPosition(
                    latitude=float(lat),
                    longitude=float(lon),
                    altitude_m=float(alt) if alt is not None else None,
                    timestamp=ts or self._gps_time or datetime.now(timezone.utc),
                    satellites=self._satellites,
                    hdop=self._hdop,
                    speed_knots=float(speed) * 1.943844 if speed is not None else None,
                )
            self._error = None

    def _reader_loop(self):
        while not self._stop.is_set():
            try:
                line = self._file.readline()
                if not line:
                    if not self._stop.is_set():
                        self._error = "Connexion gpsd fermee"
                    break
                self._handle_report(json.loads(line))
            except socket.timeout:
                continue
            except json.JSONDecodeError:
                continue
            except Exception as exc:
                if not self._stop.is_set():
                    self._error = str(exc)
                break

    def status(self):
        with self._lock:
            return GpsStatus(
                connected=self.connected,
                fix=bool(self._mode >= 2 and self._position is not None),
                satellites=self._satellites,
                source=self.plugin_id,
                port=f"{self.config.get('host', '127.0.0.1')}:{int(self.config.get('port', 2947))}",
                message=self._error,
            )

    def get_position(self):
        with self._lock:
            return self._position

    def get_time(self):
        with self._lock:
            return self._gps_time

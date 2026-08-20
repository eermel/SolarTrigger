#!/usr/bin/env python3
"""Service applicatif GPS.
Version : 1.0.00

Le service encapsule un GpsPlugin et fournit au reste de l'application un etat
thread-safe, normalise et independant du materiel. Il ne synchronise jamais
l'horloge systeme : cette responsabilite appartient a un futur TimeService.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import threading
import time
from typing import Optional

from plugins.gps import GpsPlugin, GpsPosition, load_plugin


class GpsServiceState(str, Enum):
    DISCONNECTED = "disconnected"
    NO_FIX = "no_fix"
    READY = "ready"
    STALE = "stale"
    ERROR = "error"


@dataclass(frozen=True)
class GpsServiceSnapshot:
    state: GpsServiceState
    connected: bool
    fix: bool
    position: Optional[GpsPosition]
    gps_time: Optional[datetime]
    satellites: Optional[int]
    source: Optional[str]
    port: Optional[str]
    age_seconds: Optional[float]
    message: Optional[str]
    updated_at: datetime

    @property
    def usable(self) -> bool:
        """True uniquement si une position courante est exploitable."""
        return self.state == GpsServiceState.READY and self.position is not None


class GpsService:
    """Surveille un plugin GPS et conserve le dernier etat applicatif valide."""

    def __init__(self, plugin: GpsPlugin, *, poll_interval=0.5, stale_after=3.0,
                 log_fn=print, monotonic_fn=time.monotonic):
        if poll_interval <= 0:
            raise ValueError("poll_interval doit etre > 0")
        if stale_after <= 0:
            raise ValueError("stale_after doit etre > 0")

        self.plugin = plugin
        self.poll_interval = float(poll_interval)
        self.stale_after = float(stale_after)
        self.log = log_fn
        self._monotonic = monotonic_fn

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread = None
        self._last_position = None
        self._last_position_signature = None
        self._last_position_seen_monotonic = None
        self._snapshot = GpsServiceSnapshot(
            state=GpsServiceState.DISCONNECTED,
            connected=False,
            fix=False,
            position=None,
            gps_time=None,
            satellites=None,
            source=None,
            port=None,
            age_seconds=None,
            message=None,
            updated_at=datetime.now(timezone.utc),
        )

    @classmethod
    def from_config(cls, config, *, log_fn=print):
        """Construit le service depuis une configuration applicative simple.

        Exemple::
            {
              "plugin": "serial_nmea",
              "poll_interval": 0.5,
              "stale_after": 3.0,
              "plugin_config": {"port": "/dev/serial/by-id/...", "baudrate": 4800}
            }
        """
        config = config or {}
        plugin_id = config.get("plugin", "serial_nmea")
        plugin = load_plugin(plugin_id, log_fn=log_fn,
                             config=config.get("plugin_config", {}))
        return cls(plugin,
                   poll_interval=config.get("poll_interval", 0.5),
                   stale_after=config.get("stale_after", 3.0),
                   log_fn=log_fn)

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        if self.running:
            return
        self._stop_event.clear()
        try:
            self.plugin.connect()
        except Exception as exc:
            self._set_error(str(exc))
            raise
        self._thread = threading.Thread(target=self._monitor_loop,
                                        name="gps-service", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(2.0, self.poll_interval * 3.0))
        self._thread = None
        try:
            self.plugin.disconnect()
        finally:
            with self._lock:
                old = self._snapshot
                self._snapshot = GpsServiceSnapshot(
                    state=GpsServiceState.DISCONNECTED,
                    connected=False,
                    fix=False,
                    position=self._last_position,
                    gps_time=old.gps_time,
                    satellites=old.satellites,
                    source=old.source,
                    port=old.port,
                    age_seconds=self._age_seconds(),
                    message=None,
                    updated_at=datetime.now(timezone.utc),
                )

    def snapshot(self) -> GpsServiceSnapshot:
        """Retourne un snapshot immuable et recalcule l'age de la position."""
        with self._lock:
            snap = self._snapshot
            age = self._age_seconds()
            state = snap.state
            if snap.connected and snap.fix and age is not None:
                state = (GpsServiceState.STALE if age > self.stale_after
                         else GpsServiceState.READY)
            return GpsServiceSnapshot(
                state=state,
                connected=snap.connected,
                fix=snap.fix,
                position=snap.position,
                gps_time=snap.gps_time,
                satellites=snap.satellites,
                source=snap.source,
                port=snap.port,
                age_seconds=age,
                message=snap.message,
                updated_at=snap.updated_at,
            )

    def get_position(self, *, require_usable=True) -> Optional[GpsPosition]:
        snap = self.snapshot()
        if require_usable and not snap.usable:
            return None
        return snap.position

    def get_time(self) -> Optional[datetime]:
        return self.snapshot().gps_time

    def initialize(self, timeout_s=60.0, *, require_gga=True):
        """Acquisition ponctuelle demandee par l'operateur.

        Demarre le plugin, attend une position + heure GPS exploitables, puis
        arrete toujours le plugin avant de retourner. Si require_gga=True,
        attend aussi les informations typiquement issues de GGA/gpsd (altitude,
        satellites et HDOP) afin de ne pas retourner le premier RMC partiel.
        """
        timeout_s = float(timeout_s)
        if timeout_s <= 0:
            raise ValueError("timeout_s doit etre > 0")
        deadline = self._monotonic() + timeout_s
        self.start()
        try:
            while self._monotonic() < deadline:
                snap = self.snapshot()
                pos = snap.position
                complete = bool(
                    snap.usable and snap.gps_time is not None and pos is not None
                )
                if complete and require_gga:
                    complete = (pos.altitude_m is not None and
                                pos.satellites is not None and
                                pos.hdop is not None)
                if complete:
                    return snap
                if snap.state == GpsServiceState.ERROR:
                    raise RuntimeError(snap.message or "Erreur GPS")
                self._stop_event.wait(min(self.poll_interval, max(0.01, deadline - self._monotonic())))
            raise TimeoutError(f"Timeout GPS apres {timeout_s:.1f}s")
        finally:
            self.stop()

    def _age_seconds(self):
        if self._last_position_seen_monotonic is None:
            return None
        return max(0.0, self._monotonic() - self._last_position_seen_monotonic)

    @staticmethod
    def _position_signature(position):
        if position is None:
            return None
        return (position.latitude, position.longitude, position.altitude_m,
                position.timestamp, position.satellites, position.hdop,
                position.speed_knots)

    def _monitor_loop(self):
        while not self._stop_event.is_set():
            try:
                self._refresh_once()
            except Exception as exc:
                self._set_error(str(exc))
            self._stop_event.wait(self.poll_interval)

    def _refresh_once(self):
        status = self.plugin.status()
        position = self.plugin.get_position()
        gps_time = self.plugin.get_time()
        now_mono = self._monotonic()

        with self._lock:
            if position is not None and self.plugin.validate_position(position):
                signature = self._position_signature(position)
                if signature != self._last_position_signature:
                    self._last_position_signature = signature
                    self._last_position_seen_monotonic = now_mono
                self._last_position = position

            age = self._age_seconds()
            connected = bool(status.connected)
            fix = bool(status.fix and self._last_position is not None)

            if status.message:
                state = GpsServiceState.ERROR
            elif not connected:
                state = GpsServiceState.DISCONNECTED
            elif not fix:
                state = GpsServiceState.NO_FIX
            elif age is not None and age > self.stale_after:
                state = GpsServiceState.STALE
            else:
                state = GpsServiceState.READY

            self._snapshot = GpsServiceSnapshot(
                state=state,
                connected=connected,
                fix=fix,
                position=self._last_position,
                gps_time=gps_time,
                satellites=status.satellites,
                source=status.source,
                port=status.port,
                age_seconds=age,
                message=status.message,
                updated_at=datetime.now(timezone.utc),
            )

    def _set_error(self, message):
        with self._lock:
            old = self._snapshot
            self._snapshot = GpsServiceSnapshot(
                state=GpsServiceState.ERROR,
                connected=bool(getattr(self.plugin, "connected", False)),
                fix=False,
                position=self._last_position,
                gps_time=old.gps_time,
                satellites=old.satellites,
                source=old.source or getattr(self.plugin, "plugin_id", None),
                port=old.port,
                age_seconds=self._age_seconds(),
                message=message,
                updated_at=datetime.now(timezone.utc),
            )

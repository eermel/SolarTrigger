#!/usr/bin/env python3
"""Contrat commun des plugins GPS.
Version : 1.0.00

Le moteur ne connait ni le dongle, ni le protocole (NMEA direct, gpsd, etc.).
Le plugin expose uniquement des donnees GPS normalisees.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class GpsPosition:
    latitude: float
    longitude: float
    altitude_m: Optional[float]
    timestamp: datetime
    satellites: Optional[int] = None
    hdop: Optional[float] = None
    speed_knots: Optional[float] = None


@dataclass(frozen=True)
class GpsStatus:
    connected: bool
    fix: bool
    satellites: Optional[int] = None
    source: str = "unknown"
    port: Optional[str] = None
    message: Optional[str] = None


class GpsPlugin(ABC):
    """Interface materielle commune a toutes les sources GPS."""

    plugin_id = "generic"
    display_name = "GPS generique"

    def __init__(self, log_fn=print, config=None):
        self.log = log_fn
        self.config = config or {}

    @staticmethod
    def probe(config=None):
        """Detection non destructive optionnelle. False par defaut."""
        return False

    @abstractmethod
    def connect(self):
        ...

    @abstractmethod
    def disconnect(self):
        ...

    @property
    @abstractmethod
    def connected(self):
        ...

    @abstractmethod
    def status(self) -> GpsStatus:
        ...

    @abstractmethod
    def get_position(self) -> Optional[GpsPosition]:
        """Derniere position valide connue, ou None si aucun fix."""
        ...

    @abstractmethod
    def get_time(self) -> Optional[datetime]:
        """Heure UTC issuee par le GPS, ou None si indisponible."""
        ...

    def validate_position(self, position):
        if position is None:
            return False
        return (-90.0 <= position.latitude <= 90.0 and
                -180.0 <= position.longitude <= 180.0 and
                position.timestamp.tzinfo is not None)

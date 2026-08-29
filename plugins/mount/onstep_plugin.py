#!/usr/bin/env python3
"""
mount_plugins/onstep_plugin.py
Version : 1.1.00

Plugin OnStep (Tessek Mini 11 et toute monture OnStep en LX200 serie).

IMPORTANT : ce plugin ne reimplemente RIEN du protocole. Il enveloppe le module
onstep.py existant (fonctionnel et teste sur la Tessek Mini 11) derriere le
contrat MountPlugin. onstep.py reste intouche.

Correspondance : ce fichier joue le role de l'ancien mount_controller.py, mais
rattache au contrat commun pour que le moteur puisse traiter OnStep, ZWO,
SynScan... de facon uniforme.
"""

from pathlib import Path

from .base import MountPlugin, RATE_SIDEREAL, RATE_SOLAR, RATE_LUNAR

# onstep.py est fourni tel quel a cote (module non modifie).
from .onstep import OnStep, Direction, TrackingRate


# Correspondances contrat -> enums onstep.py
_DIRECTION_MAP = {
    "north": Direction.DEC_LEFT,
    "south": Direction.DEC_RIGHT,
    "east": Direction.AD_RIGHT,
    "west": Direction.AD_LEFT,
}

_RATE_MAP = {
    RATE_SIDEREAL: TrackingRate.SIDEREAL,
    RATE_SOLAR: TrackingRate.SOLAR,
    RATE_LUNAR: TrackingRate.LUNAR,
}


class OnStepMount(MountPlugin):
    plugin_id = "onstep"
    display_name = "OnStep / Tessek Mini 11 (LX200 serie)"

    def __init__(self, log_fn=print, config=None):
        super().__init__(log_fn, config)
        # parametres de connexion depuis la config (sinon defauts d'onstep.py)
        kwargs = {}
        port = self.config.get("port") or self.config.get(
            "fallback_physical_path"
        )
        if port:
            kwargs["port"] = port
        if "baudrate" in self.config:
            kwargs["baudrate"] = self.config["baudrate"]
        if "timeout" in self.config:
            kwargs["timeout"] = self.config["timeout"]
        self.mount = OnStep(**kwargs)

    # -- detection optionnelle (non destructive) --------------------------- #
    @staticmethod
    def probe(config=None):
        cfg = config or {}
        kwargs = {}
        for k in ("port", "baudrate", "timeout"):
            if k in cfg:
                kwargs[k] = cfg[k]
        m = OnStep(**kwargs)
        try:
            m.connect()
            res = m.ping()
            return bool(res.get("ok"))
        except Exception:
            return False
        finally:
            try:
                m.disconnect()
            except Exception:
                pass

    @classmethod
    def inventory(cls, config=None):
        """Enumerate OnStep controllers on stable serial-by-id paths."""
        cfg = dict(config or {})
        configured_port = cfg.get("port") or cfg.get(
            "fallback_physical_path"
        )

        if configured_port:
            ports = [str(configured_port)]
        else:
            try:
                ports = [
                    str(path)
                    for path in sorted(Path("/dev/serial/by-id").glob("*"))
                ]
            except OSError:
                ports = []

        devices = []
        for port in ports:
            probe_cfg = dict(cfg)
            probe_cfg["port"] = port
            if not cls.probe(probe_cfg):
                continue

            devices.append({
                "category": "mount",
                "backend": cls.plugin_id,
                "manufacturer": "OnStep",
                "model": "OnStep",
                "fallback_physical_path": port,
            })

        return devices

    # -- connexion --------------------------------------------------------- #
    def connect(self):
        self.mount.connect()

    def disconnect(self):
        self.mount.disconnect()

    @property
    def connected(self):
        return self.mount.connected

    def ping(self):
        return self.mount.ping()

    # -- statut ------------------------------------------------------------ #
    def status(self):
        return self.mount.status()

    # -- suivi ------------------------------------------------------------- #
    def start_tracking(self, rate=RATE_SIDEREAL):
        if rate not in _RATE_MAP:
            raise ValueError(f"Taux de suivi inconnu : {rate}")
        self.mount.start_tracking(_RATE_MAP[rate])

    def stop_tracking(self):
        self.mount.stop_tracking()

    @property
    def tracking(self):
        return self.mount.is_tracking()

    def get_tracking_capabilities(self):
        return {"modes": [RATE_SOLAR, RATE_SIDEREAL], "toggle": True}

    def set_tracking_mode(self, mode):
        if mode not in (RATE_SOLAR, RATE_SIDEREAL):
            raise ValueError(f"Mode de suivi inconnu : {mode}")
        self.mount.select_tracking_rate(_RATE_MAP[mode])

    # -- mouvements -------------------------------------------------------- #
    def move(self, direction):
        if direction not in _DIRECTION_MAP:
            raise ValueError(f"Direction inconnue : {direction}")
        self.mount.move(_DIRECTION_MAP[direction])

    def stop(self):
        self.mount.stop()

    def set_speed(self, speed):
        self.mount.set_move_rate(speed)

    def get_slew_speed_capabilities(self):
        return {
            "kind": "discrete",
            "unit": None,
            "min": None,
            "max": None,
            "step": None,
            "values": [
                {"value": rate, "label": f"{rate:g}x"}
                for rate in sorted(OnStep.MOVE_RATES.keys())
            ],
        }

    # -- home / recentrage / securite -------------------------------------- #
    def go_home(
        self,
        timeout=120,
        dt_utc=None,
        lat_deg=None,
        lon_deg=None,
        utc_offset=None,
        is_cancelled=None,
    ):
        """Retourne a la position Home connue du controleur (sans parker).

        Sans capteurs Home physiques, une perte d'alimentation arbitraire peut
        empecher la recuperation automatique de l'orientation absolue des axes.
        Cette fonction n'ajoute ni Set Home ni recherche d'index.

        Si dt_utc/lat/lon/utc_offset sont fournis, envoie le setup
        date/heure/position avant (requis pour l'unpark si parkee)."""
        return self.mount.go_home(
            timeout=timeout,
            dt_utc=dt_utc,
            lat_deg=lat_deg,
            lon_deg=lon_deg,
            utc_offset=utc_offset,
            is_cancelled=is_cancelled,
        )

    def recenter(self, utc_offset=1, gps_port=None, timeout=120):
        """Recentrage 'cle en main' facon ASIAIR : lit la position GPS
        (BU-353N5), envoie date/heure/position a la monture, dé-parke si
        besoin, puis retourne a la position Home connue du controleur.

        Sans capteurs Home physiques, une perte d'alimentation arbitraire peut
        empecher la recuperation automatique de l'orientation absolue des axes.
        Cette fonction n'ajoute ni Set Home ni recherche d'index.

        utc_offset : decalage local vs UTC (France ete=2, hiver=1).
        gps_port   : port du GPS (auto-detecte si None).
        """
        gps = self._read_gps(gps_port)
        if gps is None:
            raise RuntimeError(
                "Pas de fix GPS : impossible de recentrer. "
                "Verifier l'antenne / ciel degage, ou fournir la position."
            )
        dt_utc, lat, lon = gps
        self.log(f"[onstep] GPS : lat={lat:.5f} lon={lon:.5f} "
                 f"UTC={dt_utc:%H:%M:%S}")
        return self.mount.go_home(
            timeout=timeout,
            dt_utc=dt_utc,
            lat_deg=lat,
            lon_deg=lon,
            utc_offset=utc_offset,
        )

    @staticmethod
    def _read_gps(port=None, timeout=30):
        """Lit une position GPS valide via le BU-353N5 (trames GPRMC/GNRMC).
        Retourne (dt_utc, lat_deg, lon_deg) ou None. Autonome (pas de
        dependance a gps_sync.py pour rester utilisable isolement)."""
        import glob
        import time as _t
        from datetime import datetime, timezone
        try:
            import serial as _serial
        except ImportError:
            return None

        def _cksum_ok(s):
            try:
                data, ck = s[1:].split("*", 1)
                calc = 0
                for c in data:
                    calc ^= ord(c)
                return f"{calc:02X}" == ck.strip().upper()[:2]
            except Exception:
                return False

        def _parse(s):
            if not (s.startswith("$GPRMC") or s.startswith("$GNRMC")):
                return None
            if not _cksum_ok(s):
                return None
            p = s.split(",")
            if len(p) < 10 or p[2] != "A":
                return None
            ts, ds = p[1][:6], p[9]
            dt = datetime(2000 + int(ds[4:6]), int(ds[2:4]), int(ds[0:2]),
                          int(ts[0:2]), int(ts[2:4]), int(ts[4:6]),
                          tzinfo=timezone.utc)
            la = float(p[3][:2]) + float(p[3][2:]) / 60
            if p[4] == "S":
                la = -la
            lo = float(p[5][:3]) + float(p[5][3:]) / 60
            if p[6] == "W":
                lo = -lo
            return dt, la, lo

        if port is None:
            for cand in ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"]:
                if glob.glob(cand):
                    port = cand
                    break
            if port is None:
                port = "/dev/ttyUSB0"

        try:
            ser = _serial.Serial(port, 4800, timeout=1)
        except Exception:
            return None
        t0 = _t.monotonic()
        try:
            while (_t.monotonic() - t0) < timeout:
                line = ser.readline().decode("ascii", "replace").strip()
                res = _parse(line)
                if res:
                    return res
        finally:
            ser.close()
        return None

    def emergency_stop(self):
        try:
            self.mount.stop()
        except Exception:
            pass
        try:
            self.mount.stop_tracking()
        except Exception:
            pass

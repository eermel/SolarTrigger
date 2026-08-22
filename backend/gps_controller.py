from __future__ import annotations
import json, threading
from datetime import datetime, timezone

class GpsController:
    """Operator-triggered one-shot GPS acquisition. gpsd remains OS-owned."""
    def __init__(self, state_store, config_file, timezone_fn, time_sync_fn, log_fn, emit_fn):
        self.state = state_store; self.config_file = config_file
        self.timezone_fn = timezone_fn; self.time_sync_fn = time_sync_fn
        self.log = log_fn; self.emit = emit_fn
        self._lock = threading.Lock(); self._thread = None

    def start(self, timeout_s=60.0):
        with self._lock:
            if self._thread and self._thread.is_alive(): return False
            self.state.update_section("gps", {"gps_sync_running": True})
            self.state.set("gps_sync_running", True)
            self._thread = threading.Thread(target=self._run, args=(timeout_s,),
                                            name="gps-operator-sync", daemon=True)
            self._thread.start(); return True

    def _run(self, timeout_s):
        synced = False
        self.log("▶ Acquisition GPS demandée par l'opérateur…", "gps", "gps_sync")
        try:
            from services.gps_service import GpsService
            cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
            service = GpsService.from_config(cfg, log_fn=lambda m: self.log(str(m), "gps", "gps_sync"))
            snap = service.initialize(timeout_s=timeout_s, require_gga=True)
            pos = snap.position
            if pos is None or snap.gps_time is None:
                raise RuntimeError("GPS sans position ou heure exploitable")
            self.log(f"GPS_FIX lat={pos.latitude:.6f} lon={pos.longitude:.6f} "
                     f"alt={pos.altitude_m if pos.altitude_m is not None else 0.0:.1f} "
                     f"sats={pos.satellites or 0} hdop={pos.hdop if pos.hdop is not None else 'n/a'}",
                     "gps", "gps_sync")
            if not self.time_sync_fn(snap.gps_time, dry_run=False):
                raise RuntimeError("Échec de synchronisation de l'heure système")
            tz_offset = self.timezone_fn(pos.latitude, pos.longitude, eclipse_date=None)
            utc_offset_minutes = round(tz_offset * 60)
            try:
                from timezonefinder import TimezoneFinder
                timezone_name = TimezoneFinder().timezone_at(
                    lat=pos.latitude, lng=pos.longitude)
            except Exception:
                timezone_name = None
            tz_str = f"UTC{tz_offset:+g}"
            gps_snap = self.state.update_section("gps", {
                "connected": False, "synced": True,
                "lat": round(pos.latitude, 6), "lon": round(pos.longitude, 6),
                "alt": round(pos.altitude_m, 1) if pos.altitude_m is not None else None,
                "satellites": pos.satellites or 0,
                "hdop": round(pos.hdop, 2) if pos.hdop is not None else None,
                "date": snap.gps_time.strftime("%Y-%m-%d"),
                "sync_time": datetime.now(timezone.utc).isoformat(), "timezone": tz_str,
                "timezone_name": timezone_name,
                "utc_offset_minutes": utc_offset_minutes,
                "gps_sync_running": False,
            })
            self.state.set("gps_sync_running", False); self.state.save(); synced = True
            self.log(f"✅ GPS synchronisé — {tz_str}", "success", "gps_sync")
            self.emit("gps_update", gps_snap)
        except Exception as exc:
            self.log(f"❌ GPS : {exc}", "error", "gps_sync")
            gps_snap = self.state.update_section("gps", {"connected": False, "gps_sync_running": False})
            self.state.set("gps_sync_running", False); self.emit("gps_update", gps_snap)
        finally:
            self.emit("gps_sync_done", {"synced": synced})

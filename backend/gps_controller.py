from __future__ import annotations

import json
import math
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace


class GpsController:
    """Operator-triggered one-shot GPS acquisition. gpsd remains OS-owned."""

    SMARTPHONE_SOURCE = "smartphone"

    def __init__(
        self,
        state_store,
        config_file,
        timezone_fn,
        time_sync_fn,
        log_fn,
        emit_fn,
        time_adjust_fn=None,
    ):
        self.state = state_store
        self.config_file = config_file
        self.timezone_fn = timezone_fn
        self.time_sync_fn = time_sync_fn
        self.time_adjust_fn = time_adjust_fn
        self.log = log_fn
        self.emit = emit_fn
        self._lock = threading.Lock()
        self._thread = None

    def _selected_source(self):
        devices = self.state.snapshot("devices") or {}
        gps = devices.get("gps") or {}
        source = gps.get("plugin") if isinstance(gps, dict) else None
        return source if source not in (None, "", "none") else None

    @staticmethod
    def _request_payload():
        """Copy JSON while the Flask request context still belongs to this thread."""
        try:
            from flask import has_request_context, request

            if not has_request_context():
                return {}
            payload = request.get_json(silent=True)
            return dict(payload) if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def start(self, timeout_s=60.0, mode="time_location", source_payload=None):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False

            source = self._selected_source()
            if source == self.SMARTPHONE_SOURCE and source_payload is None:
                source_payload = self._request_payload()
            elif source_payload is not None:
                source_payload = dict(source_payload)

            self.state.update_section("gps", {"gps_sync_running": True})
            self.state.set("gps_sync_running", True)
            self._thread = threading.Thread(
                target=self._run,
                args=(timeout_s, mode, source, source_payload),
                name="gps-operator-sync",
                daemon=True,
            )
            self._thread.start()
            return True

    @staticmethod
    def _finite_number(payload, name, *, required=True, minimum=None, maximum=None):
        if not isinstance(payload, dict) or name not in payload or payload[name] is None:
            if required:
                raise ValueError(f"Smartphone payload is missing '{name}'")
            return None
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Smartphone field '{name}' must be numeric")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"Smartphone field '{name}' must be finite")
        if minimum is not None and value < minimum:
            raise ValueError(f"Smartphone field '{name}' must be >= {minimum}")
        if maximum is not None and value > maximum:
            raise ValueError(f"Smartphone field '{name}' must be <= {maximum}")
        return value

    @classmethod
    def _smartphone_snapshot(cls, payload):
        if not isinstance(payload, dict) or payload.get("source") != cls.SMARTPHONE_SOURCE:
            raise ValueError("Smartphone synchronization payload has an invalid source")

        latitude = cls._finite_number(payload, "latitude")
        longitude = cls._finite_number(payload, "longitude")
        altitude = cls._finite_number(payload, "altitude_m", required=False)
        accuracy = cls._finite_number(payload, "accuracy_m", required=False, minimum=0.0)
        altitude_accuracy = cls._finite_number(
            payload,
            "altitude_accuracy_m",
            required=False,
            minimum=0.0,
        )
        client_epoch_ms = cls._finite_number(payload, "client_epoch_ms", minimum=0.0)
        position_timestamp_ms = cls._finite_number(
            payload,
            "position_timestamp_ms",
            required=False,
            minimum=0.0,
        )
        clock_offset_ms = cls._finite_number(
            payload,
            "clock_offset_ms",
            minimum=-315576000000.0,  # +/- 10 years, sanity only
            maximum=315576000000.0,
        )
        clock_best_rtt_ms = cls._finite_number(
            payload,
            "clock_best_rtt_ms",
            minimum=0.0,
            maximum=60000.0,
        )
        clock_selected_rtt_ms = cls._finite_number(
            payload,
            "clock_selected_rtt_ms",
            required=False,
            minimum=0.0,
            maximum=60000.0,
        )
        clock_probe_count = cls._finite_number(
            payload,
            "clock_probe_count",
            minimum=3.0,
            maximum=64.0,
        )
        if int(clock_probe_count) != clock_probe_count:
            raise ValueError("Smartphone field 'clock_probe_count' must be an integer")

        if not -90.0 <= latitude <= 90.0:
            raise ValueError("Smartphone latitude must be between -90 and +90 degrees")
        if not -180.0 <= longitude <= 180.0:
            raise ValueError("Smartphone longitude must be between -180 and +180 degrees")

        # Browser epoch timestamps are UTC Unix epoch values.  Do not compare
        # them against the Pi clock here: correcting the Pi clock is the point
        # of this operation.
        if not 946684800000.0 <= client_epoch_ms <= 4102444800000.0:
            raise ValueError("Smartphone clock is outside the supported 2000-2100 range")

        if position_timestamp_ms is not None:
            age_ms = client_epoch_ms - position_timestamp_ms
            if age_ms < -5000.0 or age_ms > 120000.0:
                raise ValueError("Smartphone position is stale or has an invalid timestamp")

        # gps_time is retained for date/state compatibility only.  Smartphone
        # clock synchronization itself uses the measured phone-minus-Pi offset,
        # not this older absolute browser timestamp.
        gps_time = datetime.fromtimestamp(client_epoch_ms / 1000.0, tz=timezone.utc)
        position = SimpleNamespace(
            latitude=latitude,
            longitude=longitude,
            altitude_m=altitude,
            satellites=None,
            hdop=None,
        )
        return SimpleNamespace(
            position=position,
            gps_time=gps_time,
            accuracy_m=accuracy,
            altitude_accuracy_m=altitude_accuracy,
            position_timestamp_ms=position_timestamp_ms,
            clock_offset_ms=clock_offset_ms,
            clock_best_rtt_ms=clock_best_rtt_ms,
            clock_selected_rtt_ms=clock_selected_rtt_ms,
            clock_probe_count=int(clock_probe_count),
        )

    def _acquire(self, timeout_s, source, source_payload):
        if source == self.SMARTPHONE_SOURCE:
            return self._smartphone_snapshot(source_payload or {})

        from services.gps_service import GpsService

        cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
        service = GpsService.from_config(
            cfg,
            log_fn=lambda message: self.log(str(message), "gps", "gps_sync"),
        )
        return service.initialize(timeout_s=timeout_s, require_gga=True)

    def _sync_clock(self, snap, source):
        if source == self.SMARTPHONE_SOURCE:
            offset_s = snap.clock_offset_ms / 1000.0
            if self.time_adjust_fn is not None:
                return self.time_adjust_fn(offset_s, dry_run=False)

            # Compatibility fallback for tests/older adapters.  The production
            # Flask app injects time_adjust_fn so the privileged child evaluates
            # the relative correction at execution time, avoiding subprocess
            # startup latency in an absolute timestamp.
            target = datetime.now(timezone.utc) + timedelta(seconds=offset_s)
            return self.time_sync_fn(target, dry_run=False)

        return self.time_sync_fn(snap.gps_time, dry_run=False)

    def _run(self, timeout_s, mode="time_location", source=None, source_payload=None):
        synced = False
        source_label = "smartphone" if source == self.SMARTPHONE_SOURCE else "GPS dongle"
        self.log(
            f"▶ {source_label} acquisition requested by operator…",
            "gps",
            "gps_sync",
        )
        try:
            if mode not in {"time_location", "time_only", "location_only"}:
                raise ValueError(f"Unknown GPS mode: {mode}")

            snap = self._acquire(timeout_s, source, source_payload)
            pos = snap.position
            if pos is None or snap.gps_time is None:
                raise RuntimeError("GPS source has no usable position or time")

            if source == self.SMARTPHONE_SOURCE:
                accuracy = getattr(snap, "accuracy_m", None)
                accuracy_text = f"{accuracy:.1f}m" if accuracy is not None else "n/a"
                altitude_text = f"{pos.altitude_m:.1f}" if pos.altitude_m is not None else "n/a"
                self.log(
                    f"SMARTPHONE_FIX lat={pos.latitude:.6f} lon={pos.longitude:.6f} "
                    f"alt={altitude_text} accuracy={accuracy_text} "
                    f"clock_offset={snap.clock_offset_ms:+.3f}ms "
                    f"best_rtt={snap.clock_best_rtt_ms:.3f}ms "
                    f"probes={snap.clock_probe_count}",
                    "gps",
                    "gps_sync",
                )
            else:
                self.log(
                    f"GPS_FIX lat={pos.latitude:.6f} lon={pos.longitude:.6f} "
                    f"alt={pos.altitude_m if pos.altitude_m is not None else 0.0:.1f} "
                    f"sats={pos.satellites or 0} "
                    f"hdop={pos.hdop if pos.hdop is not None else 'n/a'}",
                    "gps",
                    "gps_sync",
                )

            values = {"gps_sync_running": False}
            if source:
                values["source"] = source

            if mode in {"time_location", "time_only"}:
                if not self._sync_clock(snap, source):
                    raise RuntimeError("System clock synchronization failed")
                values.update(
                    {
                        "synced": True,
                        "sync_time": datetime.now(timezone.utc).isoformat(),
                    }
                )
                if source == self.SMARTPHONE_SOURCE:
                    values.update(
                        {
                            "clock_offset_ms": round(snap.clock_offset_ms, 3),
                            "clock_best_rtt_ms": round(snap.clock_best_rtt_ms, 3),
                            "clock_selected_rtt_ms": (
                                round(snap.clock_selected_rtt_ms, 3)
                                if snap.clock_selected_rtt_ms is not None
                                else None
                            ),
                            "clock_probe_count": snap.clock_probe_count,
                        }
                    )
                else:
                    values.update(
                        {
                            "clock_offset_ms": None,
                            "clock_best_rtt_ms": None,
                            "clock_selected_rtt_ms": None,
                            "clock_probe_count": None,
                        }
                    )

            if mode in {"time_location", "location_only"}:
                tz_offset = self.timezone_fn(
                    pos.latitude,
                    pos.longitude,
                    eclipse_date=None,
                )
                utc_offset_minutes = round(tz_offset * 60)
                try:
                    from timezonefinder import TimezoneFinder

                    timezone_name = TimezoneFinder().timezone_at(
                        lat=pos.latitude,
                        lng=pos.longitude,
                    )
                except Exception:
                    timezone_name = None
                tz_str = f"UTC{tz_offset:+g}"
                values.update(
                    {
                        "lat": round(pos.latitude, 6),
                        "lon": round(pos.longitude, 6),
                        "alt": round(pos.altitude_m, 1) if pos.altitude_m is not None else None,
                        "satellites": pos.satellites or 0,
                        "hdop": round(pos.hdop, 2) if pos.hdop is not None else None,
                        "date": snap.gps_time.strftime("%Y-%m-%d"),
                        "timezone": tz_str,
                        "timezone_name": timezone_name,
                        "utc_offset_minutes": utc_offset_minutes,
                    }
                )
                if source == self.SMARTPHONE_SOURCE:
                    values.update(
                        {
                            "accuracy_m": round(snap.accuracy_m, 1) if snap.accuracy_m is not None else None,
                            "altitude_accuracy_m": (
                                round(snap.altitude_accuracy_m, 1)
                                if snap.altitude_accuracy_m is not None
                                else None
                            ),
                        }
                    )
                else:
                    values.update({"accuracy_m": None, "altitude_accuracy_m": None})

            if mode == "time_location":
                values["connected"] = False

            gps_snap = self.state.update_section("gps", values)
            self.state.set("gps_sync_running", False)
            self.state.save()
            synced = True

            if mode == "time_only":
                self.log(f"✅ {source_label} time synchronized", "success", "gps_sync")
            elif mode == "location_only":
                self.log(f"✅ {source_label} position acquired — {tz_str}", "success", "gps_sync")
            else:
                self.log(f"✅ {source_label} synchronized — {tz_str}", "success", "gps_sync")
            self.emit("gps_update", gps_snap)
        except Exception as exc:
            self.log(f"❌ GPS : {exc}", "error", "gps_sync")
            gps_snap = self.state.update_section(
                "gps",
                {"connected": False, "gps_sync_running": False},
            )
            self.state.set("gps_sync_running", False)
            self.emit("gps_update", gps_snap)
        finally:
            self.emit("gps_sync_done", {"synced": synced})

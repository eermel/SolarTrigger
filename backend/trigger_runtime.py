from __future__ import annotations
import json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

class RuntimeClock:
    """UTC clock anchored on time.monotonic().

    Real mode snapshots system UTC exactly once when configured, then advances
    exclusively from the monotonic clock. Therefore NTP/GPS/manual wall-clock
    corrections after trigger start cannot make phases jump or run twice.
    Public datetimes remain naive UTC for backward compatibility with the
    existing trigger engine.
    """
    def __init__(self, wall_clock_fn=None, monotonic_fn=None, sleep_fn=None):
        self._wall_clock_fn = wall_clock_fn or (lambda: datetime.now(timezone.utc))
        self._monotonic_fn = monotonic_fn or time.monotonic
        self._sleep_fn = sleep_fn or time.sleep
        self.sim_mode=False
        self.speed=1.0
        self._anchor_mono=None
        self._anchor_utc=None
        self.virt_start=None

    @staticmethod
    def _naive_utc(dt):
        if dt.tzinfo is None:
            return dt
        return dt.astimezone(timezone.utc).replace(tzinfo=None)

    def configure(self, simulate=False, speed=1.0):
        self.sim_mode=bool(simulate)
        self.speed=float(speed if simulate else 1.0)
        self._anchor_mono=self._monotonic_fn()
        self._anchor_utc=self._naive_utc(self._wall_clock_fn())
        if not self.sim_mode:
            self.virt_start=None

    def start_simulation(self, virtual_start):
        self.sim_mode=True
        self._anchor_mono=self._monotonic_fn()
        self.virt_start=self._naive_utc(virtual_start)
        self._anchor_utc=self.virt_start

    def now(self):
        if self._anchor_mono is None or self._anchor_utc is None:
            self.configure(self.sim_mode, self.speed)
        elapsed=self._monotonic_fn()-self._anchor_mono
        factor=self.speed if self.sim_mode else 1.0
        return self._anchor_utc + timedelta(seconds=elapsed*factor)

    def sleep(self, seconds):
        if seconds>0:
            self._sleep_fn(seconds/self.speed if self.sim_mode else seconds)

    def remaining(self, target):
        return (target-self.now()).total_seconds()

class TriggerWatchdog:
    def __init__(self, path: Path, clock: RuntimeClock): self.path=Path(path); self.clock=clock
    def write(self, phase, next_shot_time=None):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            self.path.write_text(json.dumps({
                "phase":phase,
                "next_shot_time":next_shot_time.isoformat() if next_shot_time else None,
                "written_at_utc":self.clock.now().isoformat()+"Z",
            }),encoding="utf-8")
        except Exception: pass
    def read(self):
        try: return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else None
        except Exception: return None
    def clear(self):
        try: self.path.unlink(missing_ok=True)
        except Exception: pass

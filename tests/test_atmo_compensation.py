import pytest
import importlib
from datetime import datetime, timedelta
import types, sys, json

# Ensure argparse in eclipse_trigger sees no pytest args
_sys_argv_backup = list(sys.argv)
sys.argv = [sys.argv[0]]

# Provide a minimal gphoto2 stub before importing the trigger module
if 'gphoto2' not in sys.modules:
    gp = types.SimpleNamespace(
        GP_LOG_ERROR=0,
        GP_LOG_VERBOSE=1,
        GP_LOG_DEBUG=2,
        GP_LOG_DATA=3,
        use_python_logging=lambda mapping=None: None,
        check_result=lambda *a, **k: None,
    )
    sys.modules['gphoto2'] = gp

import math

from backend.atmo import facteur_atmospherique, interpolate_altitude
from backend.timeline import build_timeline, rebase_timeline
from scripts import eclipse_trigger as trig
from scripts import eclipse_calculator_jubier as gen

# Restore argv for rest of tests
sys.argv = _sys_argv_backup


def test_facteur_reference_is_one_at_zenith():
    assert abs(facteur_atmospherique(90.0, 0.0) - 1.0) < 1e-6


def test_facteur_increases_toward_horizon():
    assert facteur_atmospherique(5.0, 0.0) > 1.0


def test_airmass_clamped_for_negative_altitude():
    # h<=0 uses constant airmass, therefore the factor is identical
    f1 = facteur_atmospherique(-1.0, 0.0)
    f2 = facteur_atmospherique(-10.0, 0.0)
    assert abs(f1 - f2) < 1e-9


def test_linear_interpolation_midpoint():
    # Timeline: C1 at t0, C2 at t1
    t0 = datetime(2026, 8, 12, 19, 0, 0)
    t1 = datetime(2026, 8, 12, 20, 0, 0)
    tl = {"C1": t0, "C2": t1, "TMAX": t1, "C3": t1 + timedelta(minutes=1), "C4": t1 + timedelta(minutes=2)}
    alts = {"C1_alt_deg": 10.0, "C2_alt_deg": 20.0, "TMAX_alt_deg": 25.0, "C3_alt_deg": 15.0, "C4_alt_deg": 5.0}
    mid = t0 + (t1 - t0) / 2
    h = interpolate_altitude(mid, tl, alts)
    assert abs(h - 15.0) < 1e-9


def test_dry_run_and_real_same_logical_altitude():
    # Build a timeline then rebase and compare interpolation at same logical point
    cfg = {
        "_date": "2026-08-12",
        "TSTART": "18:00:00.000",
        "C1": "19:00:00.000",
        "C2": "20:00:00.000",
        "TMAX": "20:30:00.000",
        "C3": "21:00:00.000",
        "C4": "22:00:00.000",
    }
    tl_real = build_timeline(cfg, fallback_date=datetime(2026, 8, 12).date())
    tl_dry = rebase_timeline(tl_real, datetime(2026, 8, 12, 12, 0, 0))
    alts = {"C1_alt_deg": 10.0, "C2_alt_deg": 20.0, "TMAX_alt_deg": 30.0, "C3_alt_deg": 15.0, "C4_alt_deg": 5.0}
    # pick middle of C2->TMAX segment
    t_real = tl_real["C2"] + (tl_real["TMAX"] - tl_real["C2"]) / 2
    t_dry = tl_dry["C2"] + (tl_dry["TMAX"] - tl_dry["C2"]) / 2
    h_real = interpolate_altitude(t_real, tl_real, alts)
    h_dry = interpolate_altitude(t_dry, tl_dry, alts)
    assert abs(h_real - h_dry) < 1e-9


def test_generator_json_contains_geometric_altitudes_from_arrays_32(tmp_path):
    # Simulate a JS result structure
    res = {
        "eclipse_type": "Totale",
        "magnitude": 1.0,
        "moon_sun_ratio": 1.0,
        "duration_str": "1m 0s",
        "sun_alt_tmax": "n/a",
        "C1_utc": "19:00:00.000",
        "C2_utc": "20:00:00.000",
        "TMAX_utc": "20:30:00.000",
        "C3_utc": "21:00:00.000",
        "C4_utc": "22:00:00.000",
        "C1_local": "21:00:00.000",
        "C2_local": "22:00:00.000",
        "TMAX_local": "22:30:00.000",
        "C3_local": "23:00:00.000",
        "C4_local": "00:00:00.000",
        # Geometric altitudes provided by c1[32]..c4[32] and mid[32]
        "C1_alt_deg": 12.3,
        "C2_alt_deg": 23.4,
        "TMAX_alt_deg": 34.5,
        "C3_alt_deg": 45.6,
        "C4_alt_deg": 56.7,
    }
    out = tmp_path / "todayeclipse.json"
    gen.generate_json(res, lat=40.0, lon=3.0, alt=650, tz_offset=2, eclipse_key=list(gen.ECLIPSES.keys())[0], output=str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["C1_alt_deg"] == 12.3
    assert data["C2_alt_deg"] == 23.4
    assert data["TMAX_alt_deg"] == 34.5
    assert data["C3_alt_deg"] == 45.6
    assert data["C4_alt_deg"] == 56.7


def test_atmo_reference_is_always_sea_level():
    """Reference must be F(90 deg, 0 m), not F(90 deg, observer altitude)."""

    factor_0m = facteur_atmospherique(90.0, 0.0)
    factor_3000m = facteur_atmospherique(90.0, 3000.0)

    assert factor_0m == pytest.approx(1.0, abs=1e-12)

    # At altitude, atmospheric extinction is lower than at sea level.
    # Since normalization remains F(90°, 0 m), the factor must not stay 1.
    assert factor_3000m != pytest.approx(1.0, abs=1e-6)
    assert factor_3000m < 1.0

def test_regular_bracket_extends_only_slowest_bound_with_atmo(monkeypatch):
    # Prepare trigger module globals
    monkeypatch.setitem(trig.cfg, "atmo_compensation", True)

    trig.cfg.update({
        "_circumstances_location": {
            "altitude_m": 0.0,
        },
        "C1_alt_deg": 10.0,
        "C2_alt_deg": 10.0,
        "TMAX_alt_deg": 10.0,
        "C3_alt_deg": 10.0,
        "C4_alt_deg": 10.0,
    })

    now = datetime(2026, 8, 12, 20, 0, 0)

    trig._timeline.update({
        "C1": now - timedelta(hours=1),
        "C2": now,
        "TMAX": now + timedelta(minutes=30),
        "C3": now + timedelta(hours=1),
        "C4": now + timedelta(hours=2),
    })

    # Force atmospheric factor to 4
    monkeypatch.setattr(
        trig,
        "facteur_atmospherique",
        lambda h, H: 4.0,
    )

    class FakeResult:
        frames = 0
        planned = 0
        detail = "test"

    class FakeService:
        def __init__(self):
            self.call = None

        def shoot_speed_list(
            self,
            speeds,
            photo_num_start=0,
            deadline=None,
            slowest_override_seconds=None,
        ):
            self.call = {
                "speeds": speeds,
                "photo_num_start": photo_num_start,
                "deadline": deadline,
                "slowest_override_seconds": slowest_override_seconds,
            }
            return FakeResult()

    svc = FakeService()

    trig.capture_speed_list(
        svc,
        ["1/8000", "1/125"],
        0,
        now + timedelta(seconds=1),
        deadline=now + timedelta(minutes=1),
    )

    assert svc.call is not None
    assert svc.call["speeds"] == ["1/8000", "1/125"]

    expected_slowest_atmo = (1.0 / 125.0) * 4.0

    assert svc.call["slowest_override_seconds"] == pytest.approx(
        expected_slowest_atmo,
        abs=1e-12,
    )

    assert svc.call["slowest_override_seconds"] == pytest.approx(
        1.0 / 31.25,
        abs=1e-12,
    )

def test_irregular_list_is_unchanged_under_atmo(monkeypatch):
    monkeypatch.setitem(trig.cfg, "atmo_compensation", True)
    class FakePlugin:
        pass
    class FakeService:
        def __init__(self): self.plugin = FakePlugin(); self.called = False
        def shoot_speed_list(self, speeds, **kw): self.called = True; 
        
    svc = FakeService()
    # An irregular list: explicit values not forming a regular EV step
    trig.capture_speed_list(svc, ["1/8000", "1/4000", "1/640"], 0, datetime(2026,8,12,20,0,1))
    assert svc.called


def test_disabled_or_absent_flag_keeps_behavior(monkeypatch):
    # Remove flag
    trig.cfg.pop("atmo_compensation", None)
    class FakeService:
        def __init__(self): self.called = False
        def shoot_speed_list(self, speeds, **kw): self.called = True
        
    svc = FakeService()
    trig.capture_speed_list(svc, ["1/8000", "1/125"], 0, datetime(2026,8,12,20,0,1))
    assert svc.called


def test_missing_observer_altitude_blocks_capture(monkeypatch):
    monkeypatch.setitem(trig.cfg, "atmo_compensation", True)
    trig.cfg.pop("_circumstances_location", None)
    class FakeService:
        def __init__(self): self.called = False
        def shoot_speed_list(self, speeds, **kw): self.called = True
    svc = FakeService()
    # Expect no attempt to call plugin path; capture returns 0
    n = trig.capture_speed_list(svc, ["1/8000", "1/125"], 0, datetime(2026,8,12,20,0,1))
    assert n == 0


def test_atmo_regular_bracket_keeps_monotonic_deadline(monkeypatch):
    from datetime import timedelta
    import time

    from backend.trigger_runtime import RuntimeClock
    from services.camera_service import CameraService

    class FakeTime:
        def __init__(self):
            self.wall = datetime(2027, 8, 2, 9, 0, 0)
            self.mono = 1000.0

        def wall_now(self):
            return self.wall

        def monotonic(self):
            return self.mono

        def sleep(self, seconds):
            self.mono += seconds
            self.wall += timedelta(seconds=seconds)

    class FakePlugin:
        def __init__(self):
            self.deadline = None
            self.args = None

        def shoot_speeds(
            self,
            fastest,
            slowest,
            step_il,
            photo_num_start=0,
            deadline=None,
        ):
            self.args = (fastest, slowest, step_il)
            self.deadline = deadline

            class R:
                frames = 0
                planned = 0
                detail = "test"

            return R()

    ft = FakeTime()

    clock = RuntimeClock(
        wall_clock_fn=ft.wall_now,
        monotonic_fn=ft.monotonic,
        sleep_fn=ft.sleep,
    )
    clock.configure(False)

    service = CameraService(clock=clock)
    plugin = FakePlugin()
    service.plugin = plugin

    monkeypatch.setattr(
        "services.camera_service.time.monotonic",
        lambda: 5000.0,
    )

    phase_deadline = clock.now() + timedelta(seconds=12)

    service.shoot_speed_list(
        ["1/8000", "1/4000", "1/2000", "1/1000", "1/500", "1/250", "1/125"],
        deadline=phase_deadline,
        slowest_override_seconds=1.0 / 31.25,
    )

    assert plugin.deadline == pytest.approx(
        5012.0,
        abs=1e-9,
    )

    assert isinstance(plugin.deadline, float)

    assert plugin.args[0] == "1/8000"
    assert float(plugin.args[1]) == pytest.approx(
        1.0 / 31.25,
        abs=1e-12,
    )

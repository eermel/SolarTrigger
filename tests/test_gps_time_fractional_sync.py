from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GPS_SYNC = (ROOT / "scripts" / "gps_sync.py").read_text(encoding="utf-8")
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")


def test_absolute_clock_setter_preserves_fractional_seconds():
    assert 'epoch_arg = f"@{dt_utc.timestamp():.6f}"' in GPS_SYNC
    assert '[date_bin, "-u", "--set", epoch_arg]' in GPS_SYNC


def test_relative_clock_adjuster_preserves_subsecond_offset():
    assert "def adjust_system_time(offset_seconds, dry_run=False):" in GPS_SYNC
    assert 'relative_arg = f"{offset_seconds:+.6f} seconds"' in GPS_SYNC
    assert '[date_bin, "-u", "--set", relative_arg]' in GPS_SYNC


def test_flask_injects_relative_clock_adjuster_into_gps_controller():
    assert "def _adjust_time_backend(offset_seconds, dry_run=False):" in APP
    assert "time_adjust_fn=_adjust_time_backend" in APP

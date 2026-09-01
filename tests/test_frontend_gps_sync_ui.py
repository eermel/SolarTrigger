from pathlib import Path


HTML = (
    Path(__file__).resolve().parents[1]
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def block(start_text, end_text):
    start = HTML.index(start_text)
    end = HTML.index(end_text, start)
    return HTML[start:end]


def test_system_timezone_is_not_hardcoded_to_utc_plus_one():
    assert 'id="sys-timezone" style="font-size:14px">UTC</span>' in HTML


def test_devices_detection_updates_gps_presence_state():
    renderer = block("function renderDevices(", "function rigDeviceIdentity")
    assert "state.gpsDeviceDetected = gpsDevice.detected === true" in renderer
    assert "if (state.gps) updateGPS(state.gps)" in renderer


def test_status_reload_also_updates_gps():
    assert HTML.count("if (status.gps) updateGPS(status.gps);") == 2


def test_missing_dot_gps_cannot_abort_gps_render():
    renderer = block("function updateGPS(gps)", "const PHASE_LABELS")
    assert "if (dotGps) dotGps.className" in renderer
    assert "dotGps.className   =" not in renderer


def test_detected_unsynchronised_gps_is_not_reported_missing():
    renderer = block("function updateGPS(gps)", "const PHASE_LABELS")
    assert "const deviceDetected = state.gpsDeviceDetected === true" in renderer
    assert "Detected — not synchronized" in renderer


def test_gps_coordinates_are_rendered_after_update():
    renderer = block("function updateGPS(gps)", "const PHASE_LABELS")
    assert "const gpsLat = document.getElementById('gps-lat')" in renderer
    assert "Number(gps.lat).toFixed(5)" in renderer
    assert "Number(gps.lon).toFixed(5)" in renderer
    assert "Number(gps.alt).toFixed(0)" in renderer


def test_clock_sync_indicator_is_reached_after_gps_render():
    renderer = block("function updateGPS(gps)", "const PHASE_LABELS")
    assert "clock-gps-sync" in renderer
    assert "GPS time synchronized" in renderer


def test_every_gps_action_completion_fetches_complete_state():
    handler = block("socket.on('gps_sync_done'", "socket.on('clock_reset'")
    assert "fetch('/api/gps/state')" in handler
    assert "updateGPS(gps)" in handler

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app" / "templates" / "index.html").read_text(encoding="utf-8")
SMARTPHONE_JS = (ROOT / "flask_app" / "static" / "js" / "gps_smartphone.js").read_text(encoding="utf-8")
APP = (ROOT / "flask_app" / "app.py").read_text(encoding="utf-8")


def test_smartphone_extension_is_loaded_after_main_frontend():
    main = INDEX.index('/static/js/solartrigger.js')
    smartphone = INDEX.index('/static/js/gps_smartphone.js')
    assert main < smartphone


def test_devices_exposes_smartphone_as_gps_source():
    assert "smartphone: 'Smartphone — geolocation + UTC'" in SMARTPHONE_JS
    assert "DEVICE_PLUGIN_OPTIONS.gps.push" in SMARTPHONE_JS
    assert "Selected source: smartphone browser geolocation + UTC" in SMARTPHONE_JS


def test_sync_button_label_depends_on_selected_source():
    assert "SYNC TIME & LOCATION — SMARTPHONE" in SMARTPHONE_JS
    assert "SYNC TIME & LOCATION — GPS DONGLE" in SMARTPHONE_JS


def test_smartphone_clock_uses_ntp_style_multi_probe_filtering():
    assert "CLOCK_PROBE_COUNT = 12" in SMARTPHONE_JS
    assert "CLOCK_LOW_RTT_SAMPLE_COUNT = 4" in SMARTPHONE_JS
    assert "/api/gps/smartphone/time_probe" in SMARTPHONE_JS
    assert "0.5 * ((t2 - t1) + (t3 - t4))" in SMARTPHONE_JS
    assert "phoneMinusPiMs = -piMinusPhoneMs" in SMARTPHONE_JS
    assert "sort((a, b) => a.rttMs - b.rttMs)" in SMARTPHONE_JS
    assert "clock_offset_ms: clock.offsetMs" in SMARTPHONE_JS


def test_smartphone_sync_uses_geolocation_and_existing_sync_endpoint():
    assert "navigator.geolocation.getCurrentPosition" in SMARTPHONE_JS
    assert "enableHighAccuracy: true" in SMARTPHONE_JS
    assert "maximumAge: 0" in SMARTPHONE_JS
    assert "fetch('/api/gps/sync_time_location'" in SMARTPHONE_JS
    assert "latitude: coords.latitude" in SMARTPHONE_JS
    assert "longitude: coords.longitude" in SMARTPHONE_JS


def test_backend_exposes_lightweight_smartphone_clock_probe():
    assert '@app.route("/api/gps/smartphone/time_probe", methods=["POST"])' in APP
    assert "t2_ns = time.time_ns()" in APP
    assert "t3_ns = time.time_ns()" in APP
    assert '"t2_epoch_ms": t2_ns / 1_000_000.0' in APP
    assert '"t3_epoch_ms": t3_ns / 1_000_000.0' in APP

def test_smartphone_geolocation_requires_secure_context():
    assert "window.isSecureContext" in SMARTPHONE_JS
    assert "Smartphone GPS requires the SolarTrigger HTTPS portal" in SMARTPHONE_JS


def test_smartphone_status_requires_a_smartphone_sync_source():
    assert "gps.source === SMARTPHONE_GPS_SOURCE" in SMARTPHONE_JS

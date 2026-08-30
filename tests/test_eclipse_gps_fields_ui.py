from pathlib import Path
import re


HTML = (
    Path(__file__).resolve().parents[1]
    / "flask_app/templates/index.html"
).read_text(encoding="utf-8")


def _function_body(name):
    match = re.search(
        rf"function {re.escape(name)}\([^)]*\)\s*\{{(?P<body>.*?)\n\}}",
        HTML,
        flags=re.DOTALL,
    )
    assert match, f"{name}() missing"
    return match.group("body")


def test_periodic_gps_render_does_not_overwrite_eclipse_location_fields():
    body = _function_body("updateGPS")

    assert "inp-lat" not in body
    assert "inp-lon" not in body
    assert "inp-alt" not in body
    assert "inp-tz" not in body


def test_one_shot_location_copy_updates_eclipse_form():
    body = _function_body("copyGpsLocationToEclipseForm")

    assert "inp-lat" in body
    assert "inp-lon" in body
    assert "inp-alt" in body
    assert "inp-tz" in body


def test_only_location_gps_actions_request_form_copy():
    sync_location = _function_body("syncGpsTimeLocation")
    sync_time = _function_body("syncGpsTime")
    get_location = _function_body("getGpsLocation")

    assert re.search(r",\s*true\s*\)", sync_location)
    assert re.search(r",\s*false\s*\)", sync_time)
    assert re.search(r",\s*true\s*\)", get_location)


def test_gps_sync_done_consumes_pending_location_copy_once():
    match = re.search(
        r"socket\.on\('gps_sync_done', async d => \{(?P<body>.*?)\n\}\);",
        HTML,
        flags=re.DOTALL,
    )
    assert match, "gps_sync_done handler missing"

    body = match.group("body")

    assert "const copyLocation = _pendingGpsLocationCopy;" in body
    assert "_pendingGpsLocationCopy = false;" in body
    assert "copyGpsLocationToEclipseForm(gps);" in body

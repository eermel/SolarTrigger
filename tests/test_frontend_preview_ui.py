import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def _preview_intents_body():
    match = re.search(
        r"function\s+buildPreviewIntents\s*\(\)\s*\{(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match
    return match.group("body")


def _request_rig_previews_body():
    match = re.search(
        r"async\s+function\s+requestRigPreviews\s*\(intents\)\s*\{"
        r"(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match
    return match.group("body")


def _render_rig_previews_body():
    match = re.search(
        r"function\s+renderRigPreviews\s*\(responseJson\)\s*\{"
        r"(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match
    return match.group("body")


def test_preview_intents_uses_expected_fields_and_contacts():
    body = _preview_intents_body()

    assert "_readCameraConfig()" in body
    assert re.search(r"eclipse\.C1\s*\|\|\s*eclipse\.TMAX", body)
    assert re.search(r"eclipse\.C2\s*\|\|\s*eclipse\.C3", body)
    assert "deadline: null" in body
    assert "step_ev: phaseConfig.step_ev ?? 1.0" in body
    assert "shutter_min: phaseConfig.shutter_min" in body
    assert "shutter_max: phaseConfig.shutter_max" in body
    assert "iso_target: phaseConfig.iso" in body

    for phase, request_id in (
        ("partial", "preview-partial"),
        ("diamond_ring", "preview-diamond-ring"),
        ("totality", "preview-totality"),
    ):
        assert re.search(
            rf"phase:\s*'{phase}'.*?request_id:\s*'{request_id}'", body, re.DOTALL
        )


def test_preview_phase_inclusion_conditions_are_independent():
    body = _preview_intents_body()

    assert re.search(
        r"if\s*\(eclipse\.C2\s*\|\|\s*eclipse\.C3\)\s*\{.*?"
        r"phase:\s*'diamond_ring'.*?\n\s*\}",
        body,
        re.DOTALL,
    )
    assert re.search(
        r"if\s*\(eclipse\.TMAX\)\s*\{.*?phase:\s*'totality'.*?\n\s*\}",
        body,
        re.DOTALL,
    )


def test_request_rig_previews_posts_intents_and_forwards_raw_payload():
    body = _request_rig_previews_body()

    assert re.search(
        r"fetch\(\s*'/api/rigs/preview'\s*,\s*\{.*?"
        r"method:\s*'POST'.*?"
        r"headers:\s*\{\s*'Content-Type'\s*:\s*'application/json'\s*\}.*?"
        r"body:\s*JSON\.stringify\(\{\s*intents\s*\}\)",
        body,
        re.DOTALL,
    )
    assert re.search(r"const\s+responseJson\s*=\s*await\s+response\.text\(\)", body)
    assert re.search(r"renderRigPreviews\(responseJson\)", body)
    assert re.search(r"function\s+renderRigPreviews\s*\(responseJson\)", INDEX)


def test_render_rig_previews_distributes_escaped_raw_values_without_conversion():
    body = _render_rig_previews_body()

    assert re.search(r"rigs\.forEach\(rig\s*=>\s*\{\s*let\s+body\s*=\s*null\s*;\s*try\s*\{", body)
    assert re.search(
        r"getElementById\(`rig-preview-\$\{displayValue\(rig\s*&&\s*rig\.rig_id\)\}`\)",
        body,
    )
    assert "document.querySelectorAll('.rig-preview')" in body
    assert "line.textContent" in body
    assert "message.textContent" in body
    assert "innerHTML" not in body

    for field in (
        "phase",
        "target_time",
        "iso_applied",
        "exposures_s",
        "corrections",
        "warnings",
        "motion_policy",
        "error.code",
        "error.message",
    ):
        assert f"appendField('{field}'" in body

    assert "RAW_JSON_NUMBER:" in body
    assert "numberPattern" in body
    assert ".sort(" not in body
    assert "parseFloat(" not in body
    assert "parseInt(" not in body
    assert "Number(" not in body


def test_request_rig_previews_guards_missing_intents():
    body = _request_rig_previews_body()

    assert re.search(
        r"if\s*\(\s*!Array\.isArray\(intents\)\s*\|\|\s*"
        r"intents\.length\s*===\s*0\s*\)",
        body,
    )
    assert "flash('configuration/circumstances incomplete', 'red')" in body
    guard = body.index("configuration/circumstances incomplete")
    request = body.index("fetch('/api/rigs/preview'")
    assert "return;" in body[guard:request]


def test_request_rig_previews_locks_until_finally():
    body = _request_rig_previews_body()

    assert re.search(r"let\s+rigPreviewInFlight\s*=\s*false\s*;", INDEX)
    assert re.search(r"if\s*\(rigPreviewInFlight\)\s*return\s*;", body)
    assert re.search(r"rigPreviewInFlight\s*=\s*true\s*;", body)
    assert re.search(
        r"finally\s*\{\s*rigPreviewInFlight\s*=\s*false\s*;\s*\}",
        body,
        re.DOTALL,
    )

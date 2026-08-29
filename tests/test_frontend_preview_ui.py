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

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
CSS = (
    ROOT / "flask_app" / "static" / "css" / "solartrigger.css"
).read_text(encoding="utf-8")


def _css_block(selector):
    match = re.search(
        re.escape(selector) + r"\s*\{(?P<body>.*?)\}",
        CSS,
        re.DOTALL,
    )
    assert match, f"CSS block missing: {selector}"
    return match.group("body")


def test_focuser_movement_buttons_are_double_height():
    match = re.search(
        r"#btn-focuser-minus\s*,\s*"
        r"#btn-focuser-plus\s*\{(?P<body>.*?)\}",
        CSS,
        re.DOTALL,
    )
    assert match

    body = match.group("body")
    assert re.search(
        r"height\s*:\s*calc\(var\(--btn-h\)\s*\*\s*2\)\s*;",
        body,
    )
    assert re.search(
        r"min-height\s*:\s*calc\(var\(--btn-h\)\s*\*\s*2\)\s*;",
        body,
    )
    assert re.search(r"font-size\s*:\s*26px\s*;", body)


def test_mount_virtual_joystick_has_large_touch_surface():
    body = _css_block(".mount-joystick")

    assert re.search(r"width\s*:\s*min\(280px,\s*82vw\)\s*;", body)
    assert re.search(r"aspect-ratio\s*:\s*1\s*;", body)
    assert re.search(r"border-radius\s*:\s*50%\s*;", body)
    assert re.search(r"touch-action\s*:\s*none\s*;", body)


def test_mount_virtual_joystick_knob_is_large_and_centered():
    body = _css_block(".mount-joystick-knob")

    assert re.search(r"width\s*:\s*64px\s*;", body)
    assert re.search(r"height\s*:\s*64px\s*;", body)
    assert re.search(r"left\s*:\s*50%\s*;", body)
    assert re.search(r"top\s*:\s*50%\s*;", body)
    assert re.search(r"border-radius\s*:\s*50%\s*;", body)


def test_operator_action_buttons_match_mount_movement_height():
    match = re.search(
        r'#btn-focuser-home\s*,\s*'
        r'#btn-focuser-go\s*,\s*'
        r'#btn-mount-home\s*,\s*'
        r'\[id\^="btn-run-sequencer-rig-"\]\s*,\s*'
        r'\[id\^="btn-clean-execution-plans-rig-"\]\s*,\s*'
        r'#btn-run-all-sequencers\s*\{(?P<body>.*?)\}',
        CSS,
        re.DOTALL,
    )
    assert match

    body = match.group("body")
    assert re.search(
        r'height\s*:\s*calc\(var\(--btn-h\)\s*\*\s*2\)\s*;',
        body,
    )
    assert re.search(
        r'min-height\s*:\s*calc\(var\(--btn-h\)\s*\*\s*2\)\s*;',
        body,
    )

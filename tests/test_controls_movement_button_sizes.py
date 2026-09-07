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


def test_mount_slew_pad_is_double_size():
    body = _css_block(".mount-slew-pad")

    assert re.search(
        r"grid-template-columns\s*:\s*repeat\(3,\s*96px\)\s*;",
        body,
    )
    assert re.search(
        r"grid-template-rows\s*:\s*repeat\(3,\s*60px\)\s*;",
        body,
    )
    assert re.search(r"gap\s*:\s*10px\s*;", body)


def test_mount_movement_buttons_are_double_size():
    body = _css_block(".mount-slew-button")

    assert re.search(r"width\s*:\s*96px\s*;", body)
    assert re.search(r"min-width\s*:\s*96px\s*;", body)
    assert re.search(
        r"height\s*:\s*calc\(var\(--btn-h\)\s*\*\s*2\)\s*;",
        body,
    )
    assert re.search(
        r"min-height\s*:\s*calc\(var\(--btn-h\)\s*\*\s*2\)\s*;",
        body,
    )
    assert re.search(r"font-size\s*:\s*26px\s*;", body)

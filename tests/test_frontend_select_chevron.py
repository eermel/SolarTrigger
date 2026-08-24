from pathlib import Path
import re


INDEX = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def test_select_chevron_indicator_does_not_intercept_pointer_events():
    rule = re.search(r"\.select-chev::after\s*\{(?P<body>[^}]*)\}", INDEX)

    assert rule, ".select-chev::after CSS rule is missing"
    assert re.search(r"pointer-events\s*:\s*none\s*;", rule.group("body"))

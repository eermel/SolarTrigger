import re
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def test_select_chevron_css_does_not_capture_pointer_events():
    rule = re.search(
        r"\.select-chev\s*::after\s*\{(?P<body>[^}]*)\}",
        INDEX_HTML,
        flags=re.DOTALL,
    )

    assert rule, "Missing .select-chev::after CSS rule"
    assert re.search(
        r"(?:^|;)\s*pointer-events\s*:\s*none\s*(?:;|$)",
        rule.group("body"),
    ), "The .select-chev::after indicator must use pointer-events: none"


def test_devices_plugin_select_uses_affordance_wrapper():
    plugin_field = re.compile(
        r'<label\b[^>]*\bfor\s*=\s*["\']device-\$\{category\}-select["\'][^>]*>'
        r"\s*Plugin\s*</label>\s*"
        r'<(?P<wrapper>[a-zA-Z][\w:-]*)\b[^>]*\bclass\s*=\s*["\']'
        r'[^"\']*\bselect-chev\b[^"\']*["\'][^>]*>\s*'
        r'<select\b[^>]*\bid\s*=\s*["\']device-\$\{category\}-select["\'][^>]*>',
        flags=re.DOTALL,
    )

    assert plugin_field.search(INDEX_HTML), (
        "The Devices Plugin select must be enclosed in a .select-chev affordance "
        "wrapper immediately after its label"
    )

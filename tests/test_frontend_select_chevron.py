from tests.frontend_source import frontend_source
from pathlib import Path
import re


INDEX = frontend_source()


def test_select_chevron_indicator_does_not_intercept_pointer_events():
    rule = re.search(r"\.select-chev::after\s*\{(?P<body>[^}]*)\}", INDEX)

    assert rule, ".select-chev::after CSS rule is missing"
    assert re.search(r"pointer-events\s*:\s*none\s*;", rule.group("body"))


def test_device_plugin_selects_use_chevron_wrapper():
    categories_match = re.search(
        r"const DEVICE_CATEGORIES\s*=\s*\[(?P<categories>[^]]*)\]", INDEX
    )
    assert categories_match, "DEVICE_CATEGORIES is missing"

    categories = re.findall(r"['\"]([^'\"]+)['\"]", categories_match.group("categories"))
    assert categories == ["camera", "gps", "focuser", "mount"]

    plugin_select = re.compile(
        r'<label\s+for="device-\$\{category\}-select">Plugin</label>\s*'
        r'<div\s+class="select-chev">\s*'
        r'<select\s+id="device-\$\{category\}-select"'
        r'\s+onchange="selectDevice\(\'\$\{category\}\', this\.value\)">'
    )
    assert plugin_select.search(INDEX), (
        "Each Devices plugin label must be followed by a .select-chev wrapper "
        "containing its selectable field"
    )

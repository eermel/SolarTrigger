import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app" / "templates" / "index.html").read_text(
    encoding="utf-8"
)


def _between(text, start, end):
    match = re.search(re.escape(start) + r"(?P<body>.*?)" + re.escape(end), text, re.DOTALL)
    assert match, f"missing region delimited by {start!r} and {end!r}"
    return match.group("body")


CONTROLS_PANEL = _between(
    INDEX,
    '<div class="page" id="controls-panel"',
    '<!-- ═══════════════ PAGE 4 : TRIGGER ═══════════════ -->',
)
MOUNT_SECTION = _between(CONTROLS_PANEL, '<div id="mount-section">', "    </div>\n\n")
MOUNT_JS = _between(INDEX, "// MOUNT UI START", "// MOUNT UI END")


def test_mount_home_button_is_unique_and_inside_mount_section():
    assert re.search(
        r'<button(?=[^>]*\bid=["\']btn-mount-home["\'])[^>]*>\s*Home\s*</button>',
        MOUNT_SECTION,
    )
    assert len(re.findall(r'id=["\']btn-mount-home["\']', INDEX)) == 1


def test_mount_cancel_reuses_existing_focuser_danger_style():
    assert re.search(r"\.focuser-cancel\s*\{", INDEX)
    assert re.search(
        r"homeButton\.classList\.toggle\(\s*['\"]focuser-cancel['\"]\s*,\s*homing\s*\)",
        MOUNT_JS,
    )
    assert re.search(r"homing\s*=\s*data\s*&&\s*data\.homing\s*===\s*true", MOUNT_JS)
    assert re.search(r"homeButton\.textContent\s*=\s*homing\s*\?\s*['\"]Cancel['\"]\s*:\s*['\"]Home['\"]", MOUNT_JS)


def test_each_mount_endpoint_is_referenced_once():
    for endpoint in (
        "/api/mount/status",
        "/api/mount/home",
        "/api/mount/slew/stop",
    ):
        assert MOUNT_JS.count(endpoint) == 1


def test_mount_uses_timeout_refresh_and_socket_resynchronization():
    assert "setInterval" not in MOUNT_JS
    assert re.search(r"setTimeout\(\s*refreshMount\s*,\s*delay\s*\)", MOUNT_JS)
    assert re.search(
        r"socket\.on\(\s*['\"]connect['\"]\s*,\s*refreshMount\s*\)", MOUNT_JS
    )
    assert re.search(
        r"socket\.on\(\s*['\"]status_update['\"]\s*,\s*refreshMount\s*\)",
        MOUNT_JS,
    )
    assert re.search(r"\brefreshMount\(\s*\)\s*;", MOUNT_JS)


def test_mount_click_uses_last_server_status_and_refreshes_after_post():
    assert re.search(
        r"postMount\(\s*homing\s*\?\s*['\"]/api/mount/slew/stop['\"]\s*"
        r":\s*['\"]/api/mount/home['\"]\s*\)",
        MOUNT_JS,
    )
    assert re.search(r"async\s+function\s+postMount\([^)]*\).*?refreshMount\(\s*\)", MOUNT_JS, re.DOTALL)

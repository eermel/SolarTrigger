import re
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def test_devices_has_one_global_gps_block_and_exactly_four_rig_columns():
    devices = re.search(
        r'<section\b[^>]*class="devices-section"[^>]*>(?P<body>.*?)</section>',
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert devices, "Devices section is missing"

    body = devices.group("body")
    gps = re.findall(
        r'<div\b[^>]*class="[^"]*\bdevices-gps\b[^"]*"',
        body,
    )
    columns = re.findall(
        r'<div\b[^>]*class="[^"]*\brig-column\b[^"]*"[^>]*'
        r'data-rig-id="([1-4])"',
        body,
    )

    assert len(gps) == 1
    assert columns == ["1", "2", "3", "4"]
    assert body.index('class="card devices-gps"') < body.index(
        'class="devices-rigs-row"'
    )

    assert 'id="rig-column-1"' in body
    assert 'id="rig-body-1"' in body
    assert 'id="rig-switch-1"' not in body
    assert 'class="rig-trigger-required"' in body
    assert "REQUIS" in body

    for rig_id in range(2, 5):
        assert re.search(
            rf'id="rig-column-{rig_id}".*?RIG {rig_id}.*?'
            rf'id="rig-switch-{rig_id}".*?id="rig-body-{rig_id}"',
            body,
            flags=re.DOTALL,
        )

def test_status_fetch_and_socket_update_use_rigs_with_disabled_fallback():
    assert re.search(
        r"fetch\('/api/status'\).*?payload\.rigs\s*\|\|\s*DEFAULT_RIGS",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert re.search(
        r"socket\.on\('status_update',\s*payload\s*=>\s*\{.*?"
        r"updateRigs\(payload\.rigs\s*\|\|\s*DEFAULT_RIGS\)",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert re.search(
        r"const DEFAULT_RIGS\s*=\s*Array\.from\(\{length:\s*4\}.*?enabled:\s*false",
        INDEX_HTML,
        flags=re.DOTALL,
    )


def test_switch_change_is_delegated_and_persists_trigger_participation():
    handler = re.search(
        r"document\.addEventListener\("
        r"'change',\s*async\s+event\s*=>\s*\{(?P<body>.*?)\n\}\);",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert handler, "RIG switch change handler is missing"

    body = handler.group("body")
    assert "event.target.closest('.rig-switch')" in body
    assert "toggle.closest('.rig-column')" in body
    assert "column.dataset.rigId" in body
    assert re.search(r"rigId\s*<\s*2\s*\|\|\s*rigId\s*>\s*4", body)

    assert "const requestedEnabled = toggle.checked" in body
    assert "column.classList.toggle('enabled', requestedEnabled)" in body
    assert "toggle.disabled = true" in body

    assert "fetch('/api/rigs/devices'" in body
    assert "method: 'POST'" in body
    assert "rig_id: rigId" in body
    assert "enabled: requestedEnabled" in body
    assert "await loadRigDevices()" in body

    assert "toggle.checked = previousEnabled" in body
    assert "toggle.disabled = false" in body

    # Le switch ne doit jamais verrouiller la configuration matérielle.
    assert "querySelectorAll('.rig-body" not in body
    assert "control.disabled" not in body

def test_devices_logic_does_not_add_polling_timers():
    devices_logic = re.search(
        r"const DEFAULT_RIGS\b(?P<body>.*?)function updateControlsVisibility",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert devices_logic
    assert not re.search(r"\bset(?:Interval|Timeout)\s*\(", devices_logic.group("body"))

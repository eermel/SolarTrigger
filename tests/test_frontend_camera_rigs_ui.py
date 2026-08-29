import re
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def test_cfg_photo_has_exactly_four_camera_rig_columns_before_phase_cards():
    cfg_photo = re.search(
        r'<div class="page" id="page-2">(?P<body>.*?)</div><!-- /page-2 CFG PHOTO -->',
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert cfg_photo, "CFG PHOTO page is missing"

    page_body = cfg_photo.group("body")
    rigs_section = re.search(
        r'<section\b[^>]*class="[^"]*\bcamera-rigs-section\b[^"]*"[^>]*>'
        r'(?P<body>.*?)</section>',
        page_body,
        flags=re.DOTALL,
    )
    assert rigs_section, "Camera RIGs section is missing from CFG PHOTO"

    columns = re.findall(
        r'<div\b(?=[^>]*class="[^"]*\bcamcfg-rig-column\b[^"]*")'
        r'(?=[^>]*\sid="([^"]+)")(?=[^>]*\sdata-rig-id="([^"]+)")[^>]*>',
        rigs_section.group("body"),
    )
    assert columns == [
        ("camcfg-rig-column-1", "1"),
        ("camcfg-rig-column-2", "2"),
        ("camcfg-rig-column-3", "3"),
        ("camcfg-rig-column-4", "4"),
    ]

    camera_config_position = page_body.index(
        '<div class="card-title">Camera configuration</div>'
    )
    rigs_position = page_body.index(rigs_section.group(0))
    first_phase_position = page_body.index("<!-- ── CARD 2 : Phase Partielle ── -->")
    assert camera_config_position < rigs_position < first_phase_position


def test_update_rigs_toggles_camera_column_visibility_and_enabled_class():
    update_rigs = re.search(
        r"function updateRigs\(rigs\)\s*\{(?P<body>.*?)\n\}",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert update_rigs, "updateRigs function is missing"

    body = update_rigs.group("body")
    assert re.search(
        r"document\.getElementById\("
        r"`camcfg-rig-column-\$\{defaultRig\.rig_id\}`\)",
        body,
    )
    assert (
        "const triggerEnabled = "
        "defaultRig.rig_id === 1 || rig.enabled === true"
        in body
    )
    assert (
        "cameraColumn.classList.toggle('enabled', triggerEnabled)"
        in body
    )
    assert "cameraColumn.hidden = false" in body

